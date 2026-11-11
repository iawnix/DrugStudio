"""Drive the chat approval card in a real browser.

The server-side gate is covered by test_chat_approval.py. What this adds is
the part a user actually touches: that a suspended action shows up in the
transcript as something they must answer, and that nothing happens on disk
until they do.

The chat endpoint is stubbed at the WebApplication level, so no model is
called and the scripted action is deterministic.

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

from drugstudio.web.server import Handler, WebApplication, _allowed_hosts

try:
    from playwright.sync_api import sync_playwright
except ImportError:
    sync_playwright = None

CHROME = os.environ.get("DRUGSTUDIO_TEST_CHROME") or next((path for path in ("/usr/bin/google-chrome", "/usr/bin/chromium",
                                 "/usr/bin/chromium-browser")
               if os.path.exists(path)), None)
IGNORABLE_CONSOLE = ("favicon", "Failed to load resource", "ERR_TUNNEL_CONNECTION_FAILED",
                     "ERR_NAME_NOT_RESOLVED", "ERR_CONNECTION_REFUSED")


@unittest.skipIf(sync_playwright is None, "playwright is not installed")
@unittest.skipIf(CHROME is None, "no Chrome binary available")
class ChatApprovalCard(unittest.TestCase):
    PORT = 8951

    @classmethod
    def setUpClass(cls):
        cls.root = Path(mkdtemp(prefix="chat-ui-"))
        cls.run_root = cls.root / "proj" / "runs" / "r1"
        (cls.run_root / "logs").mkdir(parents=True)
        (cls.run_root / "inputs").mkdir(parents=True)
        (cls.run_root / "manifest.json").write_text(json.dumps({
            "run_id": "r1", "status": "created", "current_node": None,
            "target": {"target_id": "T1"}, "ligands": [], "artifacts": {},
            "stage_results": {}, "tasks": [], "decisions": [], "human_decisions": [],
            "approvals": [], "overrides": {}, "failures": [],
        }), encoding="utf-8")
        cls.app = WebApplication(cls.run_root, cls.root, cls.root, cls.root, web_token=None)
        cls.app.allowed_hosts = _allowed_hosts("127.0.0.1", cls.PORT, [])

        cls.marker = cls.root / "proj" / "ran.txt"

        # Stand in for the model turn: always ask to run one command.
        def fake_chat(payload, *, project_root=None, run_root=None):
            action_id = "fixed-action-id"
            cls.app._pending_actions[action_id] = {
                "call": {"name": "run_command", "args": {"command": f"touch {cls.marker}"},
                         "id": "call-1"},
                "remaining": [], "messages": [], "actions": [],
                "project_root": project_root or cls.app.default_project_root,
                "run_root": run_root or cls.app.default_run_root,
                "chat_id": payload.get("chat_id", ""), "payload": {},
            }
            return {"reply": "", "actions": [],
                    "pending": {"action_id": action_id, "tool": "run_command",
                                "command": f"touch {cls.marker}", "path": "", "cwd": "",
                                "content_preview": "", "content_bytes": 0}}

        def fake_resolve(payload):
            # Reuse the real resolver, but answer with a fixed reply instead
            # of calling a model after the tool runs.
            state = cls.app._pending_actions.get(payload.get("action_id"))
            if state is None:
                raise ValueError("这个操作请求已失效，请重新提问")
            approved = bool(payload.get("approved"))
            if approved:
                cls.app.run_project_command(state["call"]["args"]["command"],
                                            root=state["project_root"])
            cls.app._pending_actions.pop(payload["action_id"], None)
            cls.app.events_for(state["run_root"]).emit(
                "chat_action_decided", status="approved" if approved else "rejected",
                tool="run_command", action_id=payload["action_id"],
                reason=str(payload.get("reason") or "") or None)
            return {"reply": "已执行" if approved else "好的，我换个方式。", "actions": [],
                    "decided": {"action_id": payload["action_id"], "approved": approved}}

        cls.app.chat = fake_chat
        cls.app.resolve_chat_action = fake_resolve

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
        if self.marker.exists():
            self.marker.unlink()
        self.app._pending_actions.clear()
        self.errors = []
        self.page = self.browser.new_page(viewport={"width": 1500, "height": 980})
        self.page.on("pageerror", lambda error: self.errors.append(str(error)))
        self.page.on("console",
                     lambda message: self.errors.append(message.text) if message.type == "error" else None)
        self.page.goto(f"http://127.0.0.1:{self.PORT}/", wait_until="networkidle")
        self.page.wait_for_timeout(900)

    def tearDown(self):
        real = [item for item in self.errors
                if not any(token in item for token in IGNORABLE_CONSOLE)]
        self.page.close()
        self.assertEqual(real, [], "the page raised script errors")

    def ask(self):
        self.page.fill("#chat-input", "帮我整理一下结果")
        self.page.click("#send-chat")
        self.page.wait_for_selector("#pending-action", timeout=10000)

    def test_a_requested_command_waits_in_the_transcript(self):
        self.ask()
        card = self.page.inner_text("#pending-action")
        self.assertIn("AI 请求执行命令", card)
        self.assertIn("touch", card)
        self.assertFalse(self.marker.exists(), "the command ran before the user answered")

    def test_approving_runs_it(self):
        self.ask()
        self.page.click("#approve-action")
        self.page.wait_for_timeout(1500)
        self.assertTrue(self.marker.exists())
        self.assertIn("已执行", self.page.inner_text("#chat-messages"))
        self.assertEqual(self.page.locator("#pending-action").count(), 0,
                         "the card should be gone once answered")

    def test_rejecting_does_not_run_it(self):
        self.ask()
        self.page.fill("#pending-reason", "这会覆盖现有结果")
        self.page.click("#reject-action")
        self.page.wait_for_timeout(1500)
        self.assertFalse(self.marker.exists())
        transcript = self.page.inner_text("#chat-messages")
        self.assertIn("已拒绝", transcript)
        self.assertIn("这会覆盖现有结果", transcript)

    def test_sending_a_new_message_abandons_the_request_on_the_record(self):
        """Walking away from the card must not silently lose the request:
        nothing may run, and the refusal has to reach the audit trail."""
        self.ask()
        self.page.fill("#chat-input", "算了，换个问题")
        self.page.click("#send-chat")
        self.page.wait_for_timeout(1800)
        self.assertFalse(self.marker.exists(), "the abandoned command ran anyway")
        decisions = [event for event in self.app.events_for(self.run_root).read_since(0)
                     if event["type"] == "chat_action_decided"]
        self.assertTrue(decisions, "abandoning a request left no record")
        self.assertEqual(decisions[-1]["status"], "rejected")

    def test_the_decision_reaches_the_event_timeline(self):
        self.ask()
        self.page.click("#reject-action")
        self.page.wait_for_timeout(1500)
        events = self.app.events_for(self.run_root).read_since(0)
        decisions = [event for event in events if event["type"] == "chat_action_decided"]
        self.assertTrue(decisions)
        self.assertEqual(decisions[-1]["status"], "rejected")


if __name__ == "__main__":
    unittest.main()
