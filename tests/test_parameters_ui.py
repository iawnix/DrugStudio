"""Drive the stage-parameter panel in a real browser.

The button this replaces raised a toast claiming parameters were shown in
the inspector, where none were. These assertions go through to the file the
next run is started from, so a panel that looks right but saves nothing
fails here.

Skipped when playwright or a Chrome binary is missing.
"""

import json
import os
import shutil
import threading
import time
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from tempfile import mkdtemp

from drugstudio import parameters
from drugstudio.web.server import Handler, WebApplication, _allowed_hosts

try:
    from playwright.sync_api import sync_playwright
except ImportError:
    sync_playwright = None

CHROME = next((path for path in ("/usr/bin/google-chrome", "/usr/bin/chromium",
                                 "/usr/bin/chromium-browser")
               if os.path.exists(path)), None)
IGNORABLE_CONSOLE = ("favicon", "Failed to load resource", "ERR_TUNNEL_CONNECTION_FAILED",
                     "ERR_NAME_NOT_RESOLVED", "ERR_CONNECTION_REFUSED")


@unittest.skipIf(sync_playwright is None, "playwright is not installed")
@unittest.skipIf(CHROME is None, "no Chrome binary available")
class StageParametersPanel(unittest.TestCase):
    PORT = 8931
    MANIFEST = {
        "run_id": "r1", "status": "created", "current_node": None,
        "target": {"target_id": "T1"}, "ligands": [{"ligand_id": "lig-1"}],
        "artifacts": {}, "stage_results": {}, "tasks": [], "decisions": [],
        "human_decisions": [], "approvals": [], "overrides": {}, "failures": [],
    }

    @classmethod
    def setUpClass(cls):
        cls.root = Path(mkdtemp(prefix="params-ui-"))
        cls.run_root = cls.root / "proj" / "runs" / "r1"
        (cls.run_root / "logs").mkdir(parents=True)
        (cls.run_root / "inputs").mkdir(parents=True)
        (cls.run_root / "manifest.json").write_text(json.dumps(cls.MANIFEST), encoding="utf-8")
        cls.app = WebApplication(cls.run_root, cls.root, cls.root, cls.root, web_token=None)
        cls.app.allowed_hosts = _allowed_hosts("127.0.0.1", cls.PORT, [])
        cls.httpd = ThreadingHTTPServer(("127.0.0.1", cls.PORT), Handler)
        cls.httpd.app = cls.app
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()
        time.sleep(0.4)
        cls._pw = sync_playwright().start()
        cls.browser = cls._pw.chromium.launch(
            executable_path=CHROME,
            args=["--use-gl=swiftshader", "--enable-unsafe-swiftshader",
                  "--no-sandbox", "--disable-dev-shm-usage"])

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls._pw.stop()
        cls.httpd.shutdown()
        cls.httpd.server_close()
        cls.app.close()
        shutil.rmtree(cls.root, ignore_errors=True)

    def setUp(self):
        # Each test starts from the run's defaults on disk.
        runtime = self.run_root / parameters.RUNTIME_RELATIVE
        if runtime.exists():
            runtime.unlink()
        (self.run_root / "manifest.json").write_text(json.dumps(self.MANIFEST), encoding="utf-8")
        self.errors = []
        self.page = self.browser.new_page(viewport={"width": 1500, "height": 980})
        self.page.on("pageerror", lambda error: self.errors.append(str(error)))
        self.page.on("console",
                     lambda message: self.errors.append(message.text) if message.type == "error" else None)
        self.page.goto(f"http://127.0.0.1:{self.PORT}/", wait_until="networkidle")
        self.page.wait_for_timeout(900)
        self.page.click("#conversation-workflow summary")
        self.page.wait_for_timeout(700)

    def tearDown(self):
        real = [item for item in self.errors
                if not any(token in item for token in IGNORABLE_CONSOLE)]
        self.page.close()
        self.assertEqual(real, [], "the page raised script errors")

    def open_stage(self, stage):
        self.page.click(f'[data-workflow-stage="{stage}"] [data-stage-action="params"]')
        self.page.wait_for_timeout(500)

    def saved(self):
        return json.loads((self.run_root / parameters.RUNTIME_RELATIVE).read_text(encoding="utf-8"))

    def test_each_stage_shows_only_its_own_parameters(self):
        self.open_stage("docking")
        self.assertTrue(self.page.is_visible("#param-exhaustiveness"))
        self.assertEqual(self.page.locator("#param-receptor_ph").count(), 0)
        self.open_stage("receptor")
        self.assertTrue(self.page.is_visible("#param-receptor_ph"))
        self.assertEqual(self.page.locator("#param-exhaustiveness").count(), 0)

    def test_a_stage_without_parameters_says_so(self):
        self.open_stage("structure")
        self.assertIn("没有可调参数", self.page.inner_text("#stage-parameters-fields"))

    def test_editing_warns_which_stage_must_be_rerun(self):
        self.open_stage("docking")
        self.page.fill("#param-exhaustiveness", "32")
        self.page.wait_for_timeout(300)
        note = self.page.inner_text("#stage-parameters-note")
        self.assertIn("分子对接", note)
        self.assertIn("重跑", note)

    def test_saving_writes_the_runtime_file(self):
        self.open_stage("docking")
        self.page.fill("#param-exhaustiveness", "32")
        self.page.fill("#param-seed", "4242")
        self.page.click("#save-parameters")
        self.page.wait_for_timeout(1200)
        saved = self.saved()
        self.assertEqual(saved["exhaustiveness"], 32)
        self.assertEqual(saved["seed"], 4242)

    def test_a_checkbox_saves_as_a_boolean(self):
        self.open_stage("ligand")
        self.page.check("#param-optimize_xtb")
        self.page.click("#save-parameters")
        self.page.wait_for_timeout(1200)
        self.assertIs(self.saved()["optimize_xtb"], True)

    def test_a_residue_list_saves_as_a_list(self):
        self.open_stage("receptor")
        self.page.fill("#param-keep_receptor_residue", "ZN\nMG")
        self.page.click("#save-parameters")
        self.page.wait_for_timeout(1200)
        self.assertEqual(self.saved()["keep_receptor_residue"], ["ZN", "MG"])

    def test_out_of_range_values_are_refused_with_a_reason(self):
        self.open_stage("receptor")
        self.page.fill("#param-receptor_ph", "99")
        self.page.click("#save-parameters")
        self.page.wait_for_timeout(1200)
        self.assertIn("pH", self.page.inner_text("#toast"))
        self.assertFalse((self.run_root / parameters.RUNTIME_RELATIVE).exists(),
                         "a rejected save must not write the file")

    def test_saved_values_come_back_on_reload(self):
        self.open_stage("docking")
        self.page.fill("#param-exhaustiveness", "64")
        self.page.click("#save-parameters")
        self.page.wait_for_timeout(1200)
        self.page.reload(wait_until="networkidle")
        self.page.wait_for_timeout(900)
        self.page.click("#conversation-workflow summary")
        self.page.wait_for_timeout(600)
        self.open_stage("docking")
        self.assertEqual(self.page.input_value("#param-exhaustiveness"), "64")

    def test_a_real_click_on_a_stage_node_selects_it(self):
        """The canvas used to take pointer capture on pointerdown, which
        retargets the following click to the canvas. Every click on a stage
        node and its action buttons was swallowed; only a synthetic click
        dispatched from script got through, so this must use a real one."""
        self.page.click('[data-workflow-stage="docking"]')
        self.page.wait_for_timeout(400)
        self.assertEqual(self.page.inner_text("#workflow-node-title"), "分子对接")
        self.assertTrue(self.page.is_visible("#param-exhaustiveness"))

    def test_dragging_the_canvas_still_pans_and_does_not_select(self):
        start = self.page.locator('[data-workflow-stage="docking"]').bounding_box()
        before = self.page.eval_on_selector("#workflow-world", "el => el.style.transform")
        self.page.mouse.move(start["x"] + start["width"] / 2, start["y"] + start["height"] / 2)
        self.page.mouse.down()
        self.page.mouse.move(start["x"] + 160, start["y"] + 40, steps=8)
        self.page.mouse.up()
        self.page.wait_for_timeout(300)
        after = self.page.eval_on_selector("#workflow-world", "el => el.style.transform")
        self.assertNotEqual(before, after, "a drag should pan the canvas")
        self.assertEqual(self.page.inner_text("#workflow-node-title"), "未选择节点",
                         "a drag must not count as selecting the node under the cursor")

    def set_stage_status(self, stage, status):
        """Put a stage into a state that the graph follows automatically."""
        path = self.run_root / "manifest.json"
        manifest = json.loads(path.read_text(encoding="utf-8"))
        manifest.setdefault("stage_results", {})[stage] = {"status": status}
        path.write_text(json.dumps(manifest), encoding="utf-8")

    def refresh(self, times=1):
        for _ in range(times):
            self.page.evaluate("document.querySelector('#refresh-button').click()")
            self.page.wait_for_timeout(350)

    def test_the_graph_follows_the_run_until_the_user_picks_a_node(self):
        # With nothing picked, the inspector should follow what is running.
        self.set_stage_status("docking", "running")
        self.refresh()
        self.assertEqual(self.page.inner_text("#workflow-node-title"), "分子对接")

    def test_a_manual_pick_survives_incoming_events(self):
        """renderWorkflow used to call inspectWorkflowStage(activeStage) on
        every event, so opening one stage and waiting half a second yanked
        the inspector back to whatever was running."""
        self.set_stage_status("docking", "running")
        self.refresh()
        self.page.click('[data-workflow-stage="receptor"]')
        self.page.wait_for_timeout(300)
        self.assertEqual(self.page.inner_text("#workflow-node-title"), "受体准备")
        self.refresh(times=4)
        self.assertEqual(self.page.inner_text("#workflow-node-title"), "受体准备",
                         "an event must not steal a node the user picked")

    def test_updates_patch_the_graph_instead_of_rebuilding_it(self):
        """A rebuild drops focus and restarts status animations. Tag the
        node, trigger repaints, and check the same element is still there."""
        self.page.evaluate(
            "document.querySelector('[data-workflow-stage=\"docking\"]').dataset.probe = 'kept'")
        self.page.focus('[data-workflow-stage="docking"]')
        for _ in range(3):
            self.page.evaluate("document.querySelector('#refresh-button').click()")
            self.page.wait_for_timeout(250)
        self.assertEqual(
            self.page.get_attribute('[data-workflow-stage="docking"]', "data-probe"), "kept",
            "the node element was replaced, so the graph was rebuilt")
        self.assertEqual(
            self.page.evaluate("document.activeElement.dataset.workflowStage"), "docking",
            "keyboard focus was lost across a repaint")

    def test_nothing_selected_says_so_rather_than_claiming_no_parameters(self):
        self.assertIn("选择一个阶段", self.page.inner_text("#stage-parameters-fields"))

    def test_reset_restores_the_defaults_for_that_stage(self):
        self.open_stage("docking")
        self.page.fill("#param-exhaustiveness", "64")
        self.page.click("#reset-parameters")
        self.page.wait_for_timeout(400)
        self.assertEqual(self.page.input_value("#param-exhaustiveness"),
                         str(parameters.BY_KEY["exhaustiveness"].default))


if __name__ == "__main__":
    unittest.main()
