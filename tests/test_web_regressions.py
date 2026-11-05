"""Exercise the real HTTP handler without chemistry, models or shell jobs."""
import http.client
import json
import tempfile
import threading
import unittest
from http.cookies import SimpleCookie
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

from drugstudio.web.server import Handler, WebApplication, _allowed_hosts


class QuietHandler(Handler):
    def log_message(self, *args):
        pass


class WebRegressions(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.workspace = Path(self.temp.name)
        for project in ("alpha", "beta", "empty"):
            root = self.workspace / project
            root.mkdir()
            (root / "notes.txt").write_text(project)
        for project in ("alpha", "beta"):
            root = self.workspace / project / "run-001"
            root.mkdir()
            (root / "manifest.json").write_text(json.dumps({
                "run_id": project, "status": "waiting_input",
                "target": {"target_id": project}, "artifacts": {},
            }))
            (root / "report.html").write_text("<script>alert(1)</script>")
        self.default = self.workspace / "alpha/run-001"
        (self.default / "inputs").mkdir()
        (self.default / "inputs/llm-config-public.json").write_text(json.dumps({
            "base_url": "https://alpha.example/v1", "model": "alpha-model",
        }))
        with patch.dict("os.environ", {"OPENAI_API_KEY": "", "OPENAI_BASE_URL": "", "DOCK_AGENT_MODEL": ""}):
            self.app = WebApplication(self.default, self.workspace, self.workspace, self.workspace)
        self.addCleanup(self.app.close)
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), QuietHandler)
        self.server.app = self.app
        self.port = self.server.server_port
        self.app.allowed_hosts = _allowed_hosts("127.0.0.1", self.port, [])
        self.thread = threading.Thread(target=self.server.serve_forever,
                                       kwargs={"poll_interval": .01}, daemon=True)
        self.thread.start()
        self.addCleanup(self.stop_server)

    def stop_server(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=3)

    def request(self, path, payload=None, headers=None):
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=3)
        try:
            connection.request("GET" if payload is None else "POST", path,
                               body=None if payload is None else json.dumps(payload),
                               headers=headers or {})
            response = connection.getresponse()
            body = response.read()
            content = json.loads(body) if "application/json" in response.getheader("Content-Type", "") else body
            return response.status, dict(response.getheaders()), content
        finally:
            connection.close()

    def test_tabs_and_selection_do_not_change_each_others_scope(self):
        for _ in range(2):
            for project in ("alpha", "beta"):
                status, _, state = self.request(f"/api/state?project_id={project}&run_key={project}/run-001")
                self.assertEqual(status, 200)
                self.assertEqual(state["project"]["project_id"], project)
                self.assertEqual(state["target"]["target_id"], project)
        self.request("/api/select-project", {"project_id": "beta"})
        self.assertEqual(self.request("/api/state")[2]["run_id"], "alpha")
        self.assertEqual(self.request("/api/state?project_id=beta")[2]["run_id"], "beta")

    def test_empty_project_has_empty_state_and_no_foreign_model(self):
        status, _, state = self.request("/api/state?project_id=empty")
        self.assertEqual(status, 200)
        self.assertEqual(state["project"]["project_id"], "empty")
        self.assertIsNone(state["run_id"])
        self.assertEqual(state["run_key"], "")
        self.assertEqual(state["target"], {})
        self.assertEqual(state["artifacts"], {})
        self.assertFalse(state["server"]["workflow_running"])
        self.assertEqual(state["model_config"]["base_url"], "")
        self.assertNotEqual(state["model_config"]["model"], "alpha-model")
        self.assertEqual(self.request("/api/model-config?project_id=empty")[2]["base_url"], "")

    def test_run_lists_are_filtered_including_empty_project(self):
        for project in ("alpha", "beta", "empty"):
            status, _, data = self.request(f"/api/runs?project_id={project}")
            self.assertEqual(status, 200)
            self.assertEqual([row["run_id"] for row in data["runs"]], [] if project == "empty" else [project])

    def test_empty_project_cannot_read_default_run_artifacts_or_events(self):
        for endpoint in ("events", "provenance", "file"):
            with self.subTest(endpoint=endpoint):
                status, _, data = self.request(f"/api/{endpoint}?project_id=empty&path=report.html")
                self.assertEqual(status, 400)
                self.assertIn("no run", data["error"])

    def test_empty_project_cannot_modify_default_run(self):
        original = (self.default / "manifest.json").read_bytes()
        for endpoint, method in (("continue", "start_workflow"), ("control", "request_control"),
                                 ("rerun", "rerun_from"), ("review", "record_review"),
                                 ("pricing", "save_pricing")):
            with self.subTest(endpoint=endpoint), patch.object(self.app, method) as operation:
                status, _, data = self.request(f"/api/{endpoint}", {"project_id": "empty"})
                self.assertEqual(status, 400)
                self.assertIn("no run", data["error"])
                operation.assert_not_called()
        self.assertEqual((self.default / "manifest.json").read_bytes(), original)

    def test_conflicting_project_and_run_are_rejected(self):
        for project in ("beta", "empty"):
            for endpoint in ("state", "file", "events", "model-config"):
                self.assertEqual(self.request(f"/api/{endpoint}?project_id={project}&run_key=alpha/run-001")[0], 400)
            self.assertEqual(self.request("/api/select-run", {"project_id": project, "run_key": "alpha/run-001"})[0], 400)
            with patch.object(self.app, "start_workflow") as operation:
                self.assertEqual(self.request("/api/continue", {"project_id": project, "run_key": "alpha/run-001"})[0], 400)
                operation.assert_not_called()

    def test_empty_project_keeps_files_and_conversations_available(self):
        self.assertEqual(self.request("/api/project/file?project_id=empty&path=notes.txt")[2]["content"], "empty")
        self.assertEqual(self.request("/api/chats", {"project_id": "empty"})[0], 201)
        self.assertEqual(len(self.request("/api/chats?project_id=empty")[2]["chats"]), 1)
        with patch.object(self.app, "chat", return_value={"reply": "ok"}) as chat:
            self.assertEqual(self.request("/api/project/chat", {"project_id": "empty", "messages": []})[0], 200)
            self.assertIsNone(chat.call_args.kwargs["run_root"])
            self.assertEqual(chat.call_args.kwargs["project_root"], self.workspace / "empty")

    def test_foreign_host_rejected_for_get_and_post(self):
        for payload in (None, {"project_id": "alpha"}):
            self.assertEqual(self.request("/api/state", payload, {"Host": "attacker.example"})[0], 421)
        self.app.allowed_hosts.add("workbench.example")
        self.assertEqual(self.request("/api/state", headers={"Host": "workbench.example"})[0], 200)

    def test_authentication_uses_session_instead_of_token_cookie(self):
        self.app.web_token = "test-only-access-token"
        self.assertEqual(self.request("/api/state")[0], 401)
        self.assertEqual(self.request("/api/auth", {"token": "wrong"})[0], 401)
        status, headers, _ = self.request("/api/auth", {"token": self.app.web_token})
        self.assertEqual(status, 200)
        cookie = SimpleCookie(headers["Set-Cookie"])
        session = cookie["drugstudio_session"]
        self.assertNotEqual(session.value, self.app.web_token)
        self.assertTrue(session["httponly"])
        self.assertEqual(session["samesite"], "Strict")
        self.assertEqual(self.request("/api/state", headers={"Cookie": f"drugstudio_session={session.value}"})[0], 200)
        self.assertEqual(self.request("/api/state", headers={"Cookie": f"drugstudio_session={self.app.web_token}"})[0], 401)

    def test_artifacts_are_downloads_but_static_ui_stays_inline(self):
        status, headers, _ = self.request("/api/file?project_id=alpha&run_key=alpha/run-001&path=report.html")
        self.assertEqual(status, 200)
        self.assertEqual(headers["X-Content-Type-Options"], "nosniff")
        self.assertTrue(headers["Content-Disposition"].startswith("attachment;"))
        self.assertIn("sandbox", headers["Content-Security-Policy"])
        status, headers, _ = self.request("/")
        self.assertEqual(status, 200)
        self.assertNotIn("Content-Disposition", headers)

    def test_paths_cannot_escape_their_scope(self):
        (self.workspace / "alpha/link.txt").symlink_to(self.workspace / "beta/notes.txt")
        for path in ("../beta/notes.txt", "link.txt", str(self.workspace / "beta/notes.txt")):
            self.assertEqual(self.request(f"/api/project/file?project_id=alpha&path={path}")[0], 400)
        self.assertEqual(self.request("/api/state?run_key=../../outside")[0], 400)
        self.assertEqual(self.request("/api/state?run_key=nonexistent")[0], 400)
        self.assertEqual(self.request("/api/project/files?project_id=../outside")[0], 400)

    def test_terminal_session_cannot_be_used_from_another_project(self):
        self.app._terminals["owned-by-alpha"] = {"project": self.workspace / "alpha"}
        try:
            self.assertEqual(self.request("/api/project/terminal/output?project_id=beta&session_id=owned-by-alpha")[0], 400)
            for endpoint in ("input", "resize", "stop"):
                status, _, data = self.request(f"/api/project/terminal/{endpoint}", {
                    "project_id": "beta", "session_id": "owned-by-alpha", "input": "x",
                })
                self.assertEqual(status, 400)
                self.assertIn("another project", data["error"])
        finally:
            self.app._terminals.clear()


if __name__ == "__main__":
    unittest.main()
