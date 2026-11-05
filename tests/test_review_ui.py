"""Drive the workbench review UI in a real browser.

Box coverage is computed from parsed coordinates rather than read back out
of the renderer, so these assertions hold whether or not 3Dmol loaded. That
is the air-gapped cluster case, and it is also how this suite runs where the
CDNs are unreachable.

Skipped when playwright or a Chrome binary is missing.
"""

import hashlib
import json
import os
import shutil
import threading
import time
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from tempfile import mkdtemp

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


def build_run(run: Path) -> None:
    (run / "logs").mkdir(parents=True)
    (run / "inputs").mkdir(parents=True)
    # Two residues ~30 A apart, so a small box holds one and misses the other.
    atoms, serial = [], 0
    for resi, cx in ((10, 0.0), (20, 30.0)):
        for name, dx in (("N", 0.0), ("CA", 1.2), ("C", 2.4), ("O", 3.0)):
            serial += 1
            atoms.append(f"ATOM  {serial:5d}  {name:<3s} ALA A{resi:4d}    "
                         f"{cx + dx:8.3f}{0.0:8.3f}{0.0:8.3f}  1.00  0.00           {name[0]}")
    text = "\n".join(atoms) + "\nEND\n"
    (run / "inputs" / "model.pdb").write_text(text, encoding="utf-8")
    # The artifact store verifies hashes, so the fixture carries a real one.
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
    (run / "manifest.json").write_text(json.dumps({
        "run_id": "r1", "status": "waiting_approval", "current_node": "pocket",
        "target": {"target_id": "T1", "binding_site_residues": ["A:10", "A:20"]},
        "ligands": [{"ligand_id": "lig-1"}],
        "artifacts": {"structure": [{"artifact_id": "a1", "kind": "supplied_structure",
                                     "path": "inputs/model.pdb", "sha256": digest,
                                     "parent_ids": [], "metadata": {}}]},
        "stage_results": {"pocket": {"status": "succeeded", "metadata": {"candidates": [{
            "rank": 1, "name": "Pocket 1", "selected": True,
            "residue_ids": ["A:10", "A:20"], "p2rank_score": 9.1,
            "box": {"center": [0.0, 0.0, 0.0], "size": [12.0, 12.0, 12.0]},
        }]}}},
        "tasks": [], "decisions": [], "human_decisions": [],
        "approvals": [], "overrides": {}, "failures": [],
    }), encoding="utf-8")


@unittest.skipIf(sync_playwright is None, "playwright is not installed")
@unittest.skipIf(CHROME is None, "no Chrome binary available")
class ReviewUI(unittest.TestCase):
    PORT = 8921

    @classmethod
    def setUpClass(cls):
        cls.root = Path(mkdtemp(prefix="review-ui-"))
        cls.run_root = cls.root / "proj" / "runs" / "r1"
        build_run(cls.run_root)
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
        self.errors = []
        self.page = self.browser.new_page(viewport={"width": 1500, "height": 950})
        self.page.on("pageerror", lambda error: self.errors.append(str(error)))
        self.page.on("console",
                     lambda message: self.errors.append(message.text) if message.type == "error" else None)
        self.page.goto(f"http://127.0.0.1:{self.PORT}/", wait_until="networkidle")
        self.page.wait_for_timeout(1200)
        self.page.click('[data-view="structure"]')
        self.page.wait_for_timeout(1500)

    def tearDown(self):
        real = [item for item in self.errors
                if not any(token in item for token in IGNORABLE_CONSOLE)]
        self.page.close()
        self.assertEqual(real, [], "the page raised script errors")

    def set_box(self, centre_x, side):
        for axis, value in (("x", centre_x), ("y", 0), ("z", 0)):
            self.page.fill(f"#center-{axis}", str(value))
        for axis in ("x", "y", "z"):
            self.page.fill(f"#size-{axis}", str(side))
        self.page.wait_for_timeout(350)

    def open_review(self):
        self.page.click('[data-view="review"]')
        self.page.wait_for_timeout(350)

    def test_box_starts_hidden_and_toggles(self):
        self.assertEqual(self.page.get_attribute("#toggle-box", "aria-pressed"), "false")
        self.page.click("#toggle-box")
        self.page.wait_for_timeout(400)
        self.assertEqual(self.page.get_attribute("#toggle-box", "aria-pressed"), "true")

    def test_coverage_warns_about_residues_outside_the_box(self):
        self.open_review()
        self.set_box(0, 12)
        coverage = self.page.inner_text("#box-coverage")
        self.assertIn("盒外", coverage)
        self.assertIn("A:20", coverage)
        self.assertEqual(self.page.inner_text("#box-coverage .box-stat strong").strip(), "1")

    def test_widening_the_box_covers_every_key_residue(self):
        self.open_review()
        self.set_box(15, 80)
        coverage = self.page.inner_text("#box-coverage")
        self.assertIn("全部在盒内", coverage)
        self.assertEqual(self.page.inner_text("#box-coverage .box-stat strong").strip(), "2")

    def test_invalid_size_reports_no_coverage(self):
        self.open_review()
        self.set_box(0, 12)
        self.page.fill("#size-x", "0")
        self.page.wait_for_timeout(350)
        self.assertIn("填写盒中心和正的盒尺寸", self.page.inner_text("#box-coverage"))

    def test_receptor_stage_offers_only_what_the_backend_accepts(self):
        self.open_review()
        options = self.page.eval_on_selector_all("#review-action option", "els => els.map(e => e.value)")
        self.assertEqual(sorted(options), ["approve", "override", "reject", "select"])
        self.page.select_option("#review-stage", "receptor")
        self.page.wait_for_timeout(300)
        options = self.page.eval_on_selector_all("#review-action option", "els => els.map(e => e.value)")
        self.assertEqual(options, ["approve"])
        self.assertTrue(self.page.is_visible("#review-stage-note"))
        # A display:grid rule used to defeat the hidden attribute here.
        self.assertFalse(self.page.is_visible("#box-inputs"))

    def test_receptor_approval_reaches_the_manifest(self):
        self.open_review()
        self.page.select_option("#review-stage", "receptor")
        self.page.wait_for_timeout(250)
        self.page.fill("#review-reason", "修复结果经人工检查通过")
        self.page.click("#submit-review")
        self.page.wait_for_timeout(2500)
        manifest = json.loads((self.run_root / "manifest.json").read_text())
        approvals = [item for item in manifest["approvals"] if item.get("stage") == "receptor"]
        self.assertEqual(len(approvals), 1)
        self.assertEqual(approvals[0]["reason"], "修复结果经人工检查通过")
        self.assertTrue(any(item.get("stage") == "receptor" for item in manifest["human_decisions"]))

    def test_box_override_carries_the_edited_numbers(self):
        self.open_review()
        self.page.select_option("#review-stage", "docking_box")
        self.page.wait_for_timeout(250)
        self.page.select_option("#review-action", "override")
        self.set_box(7, 18)
        self.page.fill("#review-reason", "缩小盒体以排除相邻亚口袋")
        self.page.click("#submit-review")
        self.page.wait_for_timeout(2500)
        override = json.loads((self.run_root / "manifest.json").read_text())["overrides"]["docking_box"]
        self.assertEqual(override["center"], [7.0, 0.0, 0.0])
        self.assertEqual(override["size"], [18.0, 18.0, 18.0])


if __name__ == "__main__":
    unittest.main()
