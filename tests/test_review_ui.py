"""Real HTTP/browser review checks, with deterministic online/offline 3D fixtures.

Set DRUGSTUDIO_TEST_3DMOL to a local 3Dmol 2.5.5 build for renderer checks.
CI requires Playwright, Chromium and that asset; other environments may skip.
"""
import hashlib
import json
import os
import shutil
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from tempfile import TemporaryDirectory
from urllib.parse import urlparse

from drugstudio.web.server import Handler, WebApplication, _allowed_hosts
try:
    from playwright.sync_api import sync_playwright
except ImportError:
    sync_playwright = None

VIEWER = os.environ.get("DRUGSTUDIO_TEST_3DMOL")
REQUIRE_UI = os.environ.get("DRUGSTUDIO_REQUIRE_UI_TESTS") == "1"
if REQUIRE_UI and (sync_playwright is None or not VIEWER or not Path(VIEWER).is_file()):
    raise RuntimeError("UI checks require Playwright and DRUGSTUDIO_TEST_3DMOL")

# Observe calls to the real renderer; no fake model, shapes, or WebGL API.
OBSERVER = r"""
(() => {
  const library = window.$3Dmol, create = library.createViewer;
  // Webpack exports are readonly; wrap the namespace, not the export itself.
  window.$3Dmol = {...library, createViewer: (...args) => {
    const viewer = create(...args), lines = new Map();
    const add = viewer.addLine.bind(viewer), remove = viewer.removeShape.bind(viewer);
    const clear = viewer.removeAllShapes.bind(viewer);
    viewer.addLine = spec => { const shape = add(spec); lines.set(shape, JSON.parse(JSON.stringify(spec))); return shape; };
    viewer.removeShape = shape => { lines.delete(shape); return remove(shape); };
    viewer.removeAllShapes = () => { lines.clear(); return clear(); };
    viewer.__testLines = lines; window.__reviewViewer = viewer;
    window.__viewerCreationCount = (window.__viewerCreationCount || 0) + 1;
    return viewer;
  }};
})();
"""

class QuietHandler(Handler):
    def log_message(self, *args):
        pass

    def do_GET(self):
        try:
            super().do_GET()
        except (BrokenPipeError, ConnectionResetError):
            pass  # Reloading a page can cancel an in-flight fixture response.


def build_run(run: Path) -> None:
    (run / "logs").mkdir(parents=True, exist_ok=True)
    (run / "inputs").mkdir(parents=True, exist_ok=True)
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
class BrowserHarness(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._pw = sync_playwright().start()
        try:
            cls.browser = cls._pw.chromium.launch(
                executable_path=os.environ.get("DRUGSTUDIO_TEST_CHROME") or None,
                args=["--use-angle=swiftshader", "--enable-unsafe-swiftshader", "--no-sandbox"])
        except Exception as exc:
            cls._pw.stop()
            if REQUIRE_UI:
                raise
            raise unittest.SkipTest(f"Chromium unavailable: {exc}")

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls._pw.stop()

    def setUp(self):
        self.temp = TemporaryDirectory(prefix="review-ui-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.run_root = self.root / "proj/runs/r1"
        build_run(self.run_root)
        (self.root / "empty").mkdir()
        self.app = WebApplication(self.run_root, self.root, self.root, self.root)
        self.addCleanup(self.app.close)
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), QuietHandler)
        self.httpd.app = self.app
        self.port = self.httpd.server_port
        self.app.allowed_hosts = _allowed_hosts("127.0.0.1", self.port, [])
        self.thread = threading.Thread(target=self.httpd.serve_forever, kwargs={"poll_interval": .01}, daemon=True)
        self.thread.start()
        self.addCleanup(self.stop_server)
        self.context = self.browser.new_context(viewport={"width": 1500, "height": 950})
        self.addCleanup(self.context.close)
        self.renderer_enabled = True
        self.context.route("**/*", self.route)
        self.page = self.context.new_page()
        self.errors = []
        self.page.on("pageerror", lambda error: self.errors.append(str(error)))
        self.page.on("console", lambda message: self.errors.append(message.text) if message.type == "error" else None)
        self.page.set_default_timeout(10000)
        self.page.goto(f"http://127.0.0.1:{self.port}/", wait_until="domcontentloaded")
        self.page.wait_for_function("document.querySelector('#run-selector').value === 'proj/runs/r1'")

    def stop_server(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join(timeout=3)

    def route(self, route):
        url = urlparse(route.request.url)
        if url.hostname == "127.0.0.1" and url.path != "/favicon.ico":
            route.continue_()
        elif "3Dmol-min.js" in url.path and self.renderer_enabled and VIEWER:
            route.fulfill(content_type="application/javascript", body=Path(VIEWER).read_text() + OBSERVER)
        else:
            route.fulfill(status=200, content_type="text/css" if route.request.resource_type == "stylesheet" else "application/javascript", body="")

    def tearDown(self):
        self.assertEqual(self.errors, [], "the page raised script errors")

    def manifest(self):
        return json.loads((self.run_root / "manifest.json").read_text())

    def write_manifest(self, manifest):
        (self.run_root / "manifest.json").write_text(json.dumps(manifest))
        self.page.reload(wait_until="domcontentloaded")
        self.page.wait_for_function("document.querySelector('#run-selector').value === 'proj/runs/r1'")

    def open_review(self):
        self.page.click('[data-view="review"]')
        self.page.locator("#box-coverage .box-stat").wait_for()

    def open_structure(self):
        self.page.click('[data-view="structure"]')
        if self.renderer_enabled and VIEWER:
            self.page.wait_for_function("window.__reviewViewer && document.querySelector('#protein-viewer canvas')")

    def set_box(self, centre_x, side):
        for axis, value in (("x", centre_x), ("y", 0), ("z", 0)):
            self.page.fill(f"#center-{axis}", str(value))
        for axis in ("x", "y", "z"):
            self.page.fill(f"#size-{axis}", str(side))

    def submit(self):
        with self.page.expect_response(lambda response: "/api/review" in response.url and response.request.method == "POST") as pending:
            self.page.click("#submit-review")
        self.assertEqual(pending.value.status, 200, pending.value.text())
        # Ensure the following state refresh finishes before more UI actions.
        self.page.wait_for_function("document.querySelector('#decision-history').textContent.includes('web-reviewer')")


class ReviewUI(BrowserHarness):
    def test_box_starts_hidden_and_toggles(self):
        self.open_structure()
        self.assertEqual(self.page.get_attribute("#toggle-box", "aria-pressed"), "false")
        self.page.click("#toggle-box")
        self.assertEqual(self.page.get_attribute("#toggle-box", "aria-pressed"), "true")

    def test_coverage_works_directly_in_review_without_renderer(self):
        self.renderer_enabled = False
        self.page.reload(wait_until="domcontentloaded")
        self.page.wait_for_function("document.querySelector('#run-selector').value === 'proj/runs/r1'")
        self.open_review()
        self.assertTrue(self.page.evaluate("typeof window.$3Dmol === 'undefined'"))
        self.assertIn("盒外", self.page.inner_text("#box-coverage"))
        self.assertIn("A:20", self.page.inner_text("#box-coverage"))

    def test_coverage_warns_about_residues_outside_the_box(self):
        self.open_review()
        self.set_box(0, 12)
        self.assertIn("盒外", self.page.inner_text("#box-coverage"))
        self.assertIn("A:20", self.page.inner_text("#box-coverage"))
        self.assertEqual(self.page.inner_text("#box-coverage .box-stat strong"), "1")

    def test_widening_the_box_covers_every_key_residue(self):
        self.open_review()
        self.set_box(15, 80)
        self.assertIn("全部在盒内", self.page.inner_text("#box-coverage"))
        self.assertEqual(self.page.inner_text("#box-coverage .box-stat strong"), "2")

    def test_invalid_size_reports_no_coverage(self):
        self.open_review()
        self.page.fill("#size-x", "0")
        self.assertIn("填写盒中心和正的盒尺寸", self.page.inner_text("#box-coverage"))

    def test_blank_center_cannot_be_submitted_as_zero(self):
        self.open_review()
        self.page.select_option("#review-action", "override")
        self.page.fill("#center-x", "")
        self.page.fill("#review-reason", "invalid box")
        requests = []
        self.page.on("request", lambda request: requests.append(request.url) if "/api/review" in request.url else None)
        self.page.click("#submit-review")
        self.assertIn("有效的盒中心", self.page.inner_text("#toast"))
        self.assertEqual(requests, [])
        self.assertEqual(self.manifest()["overrides"], {})

    def test_target_residues_and_unresolved_labels_are_not_silently_ignored(self):
        manifest = self.manifest()
        manifest["stage_results"]["pocket"]["metadata"]["candidates"][0]["residue_ids"] = ["A:10"]
        manifest["target"]["binding_site_residues"] += ["A:10A", "unknown-label"]
        self.write_manifest(manifest)
        self.open_review()
        text = self.page.inner_text("#box-coverage")
        for label in ("A:20", "A:10A", "unknown-label"):
            self.assertIn(label, text)
        self.assertIn("未找到坐标", text)
        self.assertNotIn("全部在盒内", text)

    def test_partial_residue_is_not_reported_as_fully_covered(self):
        self.open_review()
        self.set_box(0, 2)
        self.assertIn("仅部分原子", self.page.inner_text("#box-coverage"))
        self.assertIn("A:10", self.page.inner_text("#box-coverage"))

    def test_insertion_code_and_blank_chain_are_preserved(self):
        manifest = self.manifest()
        pdb = self.run_root / "inputs/model.pdb"
        lines = []
        for line in pdb.read_text().splitlines():
            if line.startswith("ATOM"):
                line = line[:21] + " " + line[22:26] + "A" + line[27:]
            lines.append(line)
        pdb.write_text("\n".join(lines) + "\n")
        manifest["artifacts"]["structure"][0]["sha256"] = hashlib.sha256(pdb.read_bytes()).hexdigest()
        manifest["target"]["binding_site_residues"] = [":10A"]
        manifest["stage_results"]["pocket"]["metadata"]["candidates"][0]["residue_ids"] = [":10A"]
        self.write_manifest(manifest)
        self.open_review()
        self.assertIn("1 个关键残基的已载入原子全部在盒内", self.page.inner_text("#box-coverage"))

    def test_receptor_stage_offers_only_what_the_backend_accepts(self):
        self.open_review()
        options = self.page.locator("#review-action option").evaluate_all("els => els.map(e => e.value)")
        self.assertEqual(sorted(options), ["approve", "override", "reject", "select"])
        self.page.select_option("#review-stage", "receptor")
        self.assertEqual(self.page.locator("#review-action option").evaluate_all("els => els.map(e => e.value)"), ["approve"])
        self.assertTrue(self.page.is_visible("#review-stage-note"))
        self.assertFalse(self.page.is_visible("#box-inputs"))

    def test_receptor_approval_reaches_the_manifest(self):
        self.open_review()
        self.page.select_option("#review-stage", "receptor")
        self.page.fill("#review-reason", "修复结果经人工检查通过")
        self.submit()
        manifest = self.manifest()
        approvals = [item for item in manifest["approvals"] if item.get("stage") == "receptor"]
        self.assertEqual(len(approvals), 1)
        self.assertEqual(approvals[0]["reason"], "修复结果经人工检查通过")
        self.assertTrue(any(item.get("stage") == "receptor" for item in manifest["human_decisions"]))

    def test_box_override_carries_numbers_and_survives_reload(self):
        self.open_review()
        self.page.select_option("#review-action", "override")
        self.set_box(7, 18)
        self.page.fill("#review-reason", "缩小盒体以排除相邻亚口袋")
        self.submit()
        override = self.manifest()["overrides"]["docking_box"]
        self.assertEqual(override["center"], [7.0, 0.0, 0.0])
        self.assertEqual(override["size"], [18.0, 18.0, 18.0])
        self.page.reload(wait_until="domcontentloaded")
        self.page.wait_for_function("document.querySelector('#run-selector').value === 'proj/runs/r1'")
        self.open_review()
        self.assertEqual(self.page.input_value("#center-x"), "7")
        self.assertEqual(self.page.input_value("#size-x"), "18")

    def test_switching_to_empty_project_clears_box_and_coverage(self):
        self.open_review()
        self.page.select_option("#project-selector", "empty")
        self.page.wait_for_function("document.querySelector('#run-state-pill').textContent === '无运行'")
        self.assertEqual(self.page.input_value("#center-x"), "")
        self.assertFalse(self.page.is_visible("#box-coverage .box-stat"))
        self.assertTrue(self.page.is_disabled("#submit-review"))

    def test_delayed_coordinates_cannot_restore_previous_project_coverage(self):
        pending = []
        self.context.route("**/api/file?*", lambda route: pending.append(route))
        self.page.click('[data-view="review"]')
        self.page.wait_for_timeout(100)
        self.assertEqual(len(pending), 1)
        self.page.select_option("#project-selector", "empty")
        self.page.wait_for_function("document.querySelector('#run-state-pill').textContent === '无运行'")
        pending[0].fulfill(content_type="text/plain", body=(self.run_root / "inputs/model.pdb").read_text())
        self.page.wait_for_timeout(100)
        self.assertFalse(self.page.is_visible("#box-coverage .box-stat"))

    @unittest.skipUnless(VIEWER, "set DRUGSTUDIO_TEST_3DMOL for actual renderer checks")
    def test_real_wireframe_updates_without_duplicate_or_lost_edges(self):
        self.open_structure()
        self.assertTrue(self.page.evaluate("Boolean(window.$3Dmol && window.__reviewViewer)"))
        self.assertEqual(self.page.evaluate("window.__reviewViewer.__testLines.size"), 0)
        self.page.click("#toggle-box")
        self.assertEqual(self.page.evaluate("window.__reviewViewer.__testLines.size"), 12)
        self.open_review()
        self.set_box(7, 18)
        lines = self.page.evaluate("[...window.__reviewViewer.__testLines.values()]")
        self.assertEqual(len(lines), 12)
        self.assertEqual({point["x"] for line in lines for point in (line["start"], line["end"])}, {-2, 16})
        self.open_structure()
        for selector in ('[data-protein-style="site"]', '[data-protein-style="cartoon"]', '#reset-protein'):
            self.page.click(selector)
            self.assertEqual(self.page.evaluate("window.__reviewViewer.__testLines.size"), 12)
        generation = self.page.evaluate("window.__viewerCreationCount")
        self.page.select_option("#pose-select", "")
        self.page.wait_for_function("generation => window.__viewerCreationCount > generation && window.__reviewViewer.__testLines.size === 12", arg=generation)
        if os.environ.get("DRUGSTUDIO_UI_SCREENSHOT"):
            output = Path(os.environ["DRUGSTUDIO_UI_SCREENSHOT"])
            output.parent.mkdir(parents=True, exist_ok=True)
            self.page.click('[data-protein-style="site"]')
            self.page.click("#reset-protein")
            canvas = self.page.locator("#protein-viewer").bounding_box()
            x, y = canvas["x"] + canvas["width"] / 2, canvas["y"] + canvas["height"] / 2
            self.page.mouse.move(x, y)
            self.page.mouse.down()
            self.page.mouse.move(x + 55, y + 35, steps=8)
            self.page.mouse.up()
            self.page.locator("#protein-viewer").screenshot(path=str(output))
        self.page.click("#toggle-box")
        self.assertEqual(self.page.evaluate("window.__reviewViewer.__testLines.size"), 0)


if __name__ == "__main__":
    unittest.main()
