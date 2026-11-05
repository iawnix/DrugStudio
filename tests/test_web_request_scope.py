"""Regressions for per-request project and run scope in the workbench server.

The server used to hold the selected project and run as instance state, so a
second browser tab silently retargeted the first. Nothing about a response
looks wrong when that happens — only the interleaving reveals it — so these
tests always exercise two scopes alternately rather than one at a time.
"""

import json
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path
from tempfile import TemporaryDirectory

from drugstudio.web.server import Handler, WebApplication, _allowed_hosts


def write_run(path: Path, run_id: str, target_id: str) -> None:
    (path / "logs").mkdir(parents=True, exist_ok=True)
    (path / "inputs").mkdir(parents=True, exist_ok=True)
    (path / "manifest.json").write_text(json.dumps({
        "run_id": run_id, "status": "created", "current_node": None,
        "target": {"target_id": target_id}, "ligands": [{"ligand_id": "lig-1"}],
        "artifacts": {"structure": [{"artifact_id": "a1", "kind": "supplied_structure",
                                     "path": "inputs/model.pdb", "sha256": "x",
                                     "parent_ids": [], "metadata": {}}]},
        "stage_results": {}, "tasks": [], "decisions": [],
    }), encoding="utf-8")
    (path / "inputs" / "model.pdb").write_text(
        "ATOM      1  CA  ALA A   1       0.0   0.0   0.0\n", encoding="utf-8")


class WorkbenchServerTestCase(unittest.TestCase):
    """Serves two projects, each owning runs, over a real HTTP listener."""

    PORT = 8787

    @classmethod
    def setUpClass(cls):
        cls._directory = TemporaryDirectory()
        cls.root = Path(cls._directory.name)
        write_run(cls.root / "alpha" / "runs" / "run-a", "run-a", "T-A")
        write_run(cls.root / "alpha" / "runs" / "run-a2", "run-a2", "T-A2")
        write_run(cls.root / "beta" / "runs" / "run-b", "run-b", "T-B")
        (cls.root / "alpha" / "note-alpha.txt").write_text("alpha\n", encoding="utf-8")
        (cls.root / "beta" / "note-beta.txt").write_text("beta\n", encoding="utf-8")

        cls.app = WebApplication(cls.root / "alpha" / "runs" / "run-a", cls.root,
                                 cls.root, cls.root, web_token=None)
        cls.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        cls.PORT = cls.httpd.server_port
        cls.app.allowed_hosts = _allowed_hosts("127.0.0.1", cls.PORT, [])
        cls.httpd.app = cls.app
        cls.base = f"http://127.0.0.1:{cls.PORT}"
        cls.thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()
        cls.app.close()
        cls._directory.cleanup()

    def request(self, path, method="GET", body=None, host=None):
        data = json.dumps(body).encode() if body is not None else None
        headers = {"Host": host or f"127.0.0.1:{self.PORT}"}
        if data:
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(self.base + path, method=method,
                                         data=data, headers=headers)
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                return response.status, json.loads(response.read())
        except urllib.error.HTTPError as error:
            payload = error.read()
            try:
                return error.code, json.loads(payload)
            except json.JSONDecodeError:
                return error.code, {"raw": payload[:200].decode("utf-8", "replace")}

    def get(self, path, **kwargs):
        return self.request(path, **kwargs)

    def post(self, path, body):
        return self.request(path, method="POST", body=body)


class HostHeaderIsChecked(WorkbenchServerTestCase):
    PORT = 8788

    def test_foreign_host_is_refused(self):
        # Without this a page on any origin could reach a loopback bind by
        # resolving its own name to 127.0.0.1, and the terminal endpoints
        # hand out a shell.
        status, _ = self.get("/api/health", host="attacker.example")
        self.assertEqual(status, 421)

    def test_bound_host_is_accepted(self):
        status, _ = self.get("/api/health")
        self.assertEqual(status, 200)


class ScopeTravelsWithTheRequest(WorkbenchServerTestCase):
    PORT = 8789

    def test_run_scope_does_not_leak_between_requests(self):
        _, first = self.get("/api/state?run_key=alpha/runs/run-a")
        _, other = self.get("/api/state?run_key=beta/runs/run-b")
        _, again = self.get("/api/state?run_key=alpha/runs/run-a")
        self.assertEqual(first["run_id"], "run-a")
        self.assertEqual(other["run_id"], "run-b")
        self.assertEqual(again["run_id"], "run-a",
                         "a request for another run must not move this one")

    def test_project_scope_does_not_leak_between_requests(self):
        _, first = self.get("/api/projects?project_id=alpha")
        _, other = self.get("/api/projects?project_id=beta")
        _, again = self.get("/api/projects?project_id=alpha")
        self.assertEqual(first["selected"]["project_id"], "alpha")
        self.assertEqual(other["selected"]["project_id"], "beta")
        self.assertEqual(again["selected"]["project_id"], "alpha")

    def test_select_project_echoes_without_mutating_other_requests(self):
        status, picked = self.post("/api/select-project", {"project_id": "beta"})
        self.assertEqual((status, picked["project_id"]), (200, "beta"))
        _, elsewhere = self.get("/api/projects?project_id=alpha")
        self.assertEqual(elsewhere["selected"]["project_id"], "alpha",
                         "selection is client state; it must not be server-wide")

    def test_select_project_does_not_move_the_fallback_scope(self):
        # A request that names no project falls back to the server default.
        # If select-project reassigned that default, this is the request that
        # would silently retarget — naming the project everywhere would hide
        # the regression.
        _, before = self.get("/api/projects")
        self.post("/api/select-project", {"project_id": "beta"})
        _, after = self.get("/api/projects")
        self.assertEqual(after["selected"]["project_id"], before["selected"]["project_id"])

    def test_select_run_does_not_move_the_fallback_scope(self):
        _, before = self.get("/api/state")
        self.post("/api/select-run", {"run_key": "beta/runs/run-b"})
        _, after = self.get("/api/state")
        self.assertEqual(after["run_id"], before["run_id"])

    def test_project_files_are_scoped(self):
        _, alpha = self.get("/api/project/files?project_id=alpha")
        _, beta = self.get("/api/project/files?project_id=beta")
        self.assertIn("note-alpha.txt", {item["name"] for item in alpha["entries"]})
        self.assertNotIn("note-beta.txt", {item["name"] for item in alpha["entries"]})
        self.assertIn("note-beta.txt", {item["name"] for item in beta["entries"]})

    def test_writes_land_in_the_named_project(self):
        status, _ = self.post("/api/project/file",
                              {"project_id": "beta", "path": "written.txt", "content": "hi"})
        self.assertEqual(status, 200)
        self.assertTrue((self.root / "beta" / "written.txt").is_file())
        self.assertFalse((self.root / "alpha" / "written.txt").exists())

    def test_pricing_is_written_to_the_named_run(self):
        self.post("/api/pricing", {"run_key": "beta/runs/run-b", "model": "m",
                                   "input_usd_per_million": 1, "output_usd_per_million": 2})
        self.assertTrue((self.root / "beta" / "runs" / "run-b" / "inputs" / "model-pricing.json").is_file())
        self.assertFalse((self.root / "alpha" / "runs" / "run-a" / "inputs" / "model-pricing.json").exists())

    def test_provenance_follows_the_run_scope(self):
        _, provenance = self.get("/api/provenance?run_key=beta/runs/run-b")
        self.assertEqual(provenance["run_id"], "run-b")


class RunsAreListedPerProject(WorkbenchServerTestCase):
    PORT = 8790

    def test_each_project_sees_only_its_own_runs(self):
        _, every = self.get("/api/runs")
        _, alpha = self.get("/api/runs?project_id=alpha")
        _, beta = self.get("/api/runs?project_id=beta")
        self.assertEqual(len(every["runs"]), 3)
        self.assertEqual(sorted(item["run_id"] for item in alpha["runs"]), ["run-a", "run-a2"])
        self.assertEqual([item["run_id"] for item in beta["runs"]], ["run-b"])

    def test_owning_project_shares_the_namespace_of_the_project_list(self):
        # run-meta.json derives project_id relative to runs_root, which is a
        # different namespace from the ids the UI uses; filtering on the raw
        # sidecar value would match nothing.
        _, every = self.get("/api/runs")
        _, projects = self.get("/api/projects?project_id=alpha")
        listed = {item["project_id"] for item in projects["projects"]}
        self.assertTrue({item["project_id"] for item in every["runs"]} <= listed)


class PathsStayInsideTheirRoot(WorkbenchServerTestCase):
    PORT = 8791

    def test_project_traversal_is_refused(self):
        status, _ = self.get("/api/project/file?project_id=alpha&path=../beta/note-beta.txt")
        self.assertEqual(status, 400)

    def test_run_traversal_is_refused(self):
        status, _ = self.get("/api/file?run_key=alpha/runs/run-a&path=../../../beta/note-beta.txt")
        self.assertEqual(status, 400)

    def test_unknown_run_key_is_refused(self):
        status, _ = self.post("/api/select-run", {"run_key": "../../etc"})
        self.assertEqual(status, 400)


class ArtifactsAreNotRenderableOnThisOrigin(WorkbenchServerTestCase):
    PORT = 8792

    def raw(self, path):
        with urllib.request.urlopen(self.base + path, timeout=10) as response:
            return dict(response.headers), response.read()

    def test_run_artifacts_are_served_as_downloads(self):
        # An .html or .svg written by an external tool would otherwise run on
        # the workbench origin and could read the session cookie.
        headers, body = self.raw("/api/file?run_key=alpha/runs/run-a&path=inputs/model.pdb")
        self.assertEqual(headers.get("X-Content-Type-Options"), "nosniff")
        self.assertTrue(headers.get("Content-Disposition", "").startswith("attachment"))
        self.assertTrue(body.startswith(b"ATOM"))

    def test_static_assets_stay_inline(self):
        headers, _ = self.raw("/")
        self.assertNotIn("Content-Disposition", headers)
        self.assertEqual(headers.get("X-Content-Type-Options"), "nosniff")


class TerminalSessionsBelongToOneProject(WorkbenchServerTestCase):
    PORT = 8793

    def test_a_session_cannot_be_driven_from_another_project(self):
        status, started = self.post("/api/project/terminal/start", {"project_id": "beta"})
        self.assertEqual(status, 200)
        session = started["session_id"]
        process = self.app._terminals[session]["process"]
        try:
            stolen, _ = self.post("/api/project/terminal/input",
                                  {"project_id": "alpha", "session_id": session, "input": "id\n"})
            self.assertEqual(stolen, 400)
            owned, _ = self.post("/api/project/terminal/input",
                                 {"project_id": "beta", "session_id": session, "input": "pwd\n"})
            self.assertEqual(owned, 200)
        finally:
            self.post("/api/project/terminal/stop", {"project_id": "beta", "session_id": session})
            process.wait(timeout=5)


# The base class only provides the fixture; it holds no tests of its own.
del WorkbenchServerTestCase


if __name__ == "__main__":
    unittest.main()
