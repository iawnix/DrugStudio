"""Regressions for the chat approval gate and the run context it is given.

The gate replaces a sentence in the system prompt ("Ask before destructive
operations"), which is a request to the model, not a control the user holds.
These tests drive the loop with a scripted model so the dangerous path is
exercised deterministically rather than depending on what a real model
decides to do.
"""

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from drugstudio import parameters
from drugstudio.web.server import WebApplication

try:
    from langchain_core.messages import AIMessage
except ImportError:
    # langchain-core is an optional extra. The approval gate is the one part
    # of this server that must not be left untested because a dependency is
    # absent, so stand in for the three message classes the loop touches.
    # The loop only appends these objects and reads .tool_calls, so the code
    # under test is the real code; what the stub does not cover is how
    # langchain itself serialises them, which is not what these tests claim.
    import sys
    import types

    class _Message:
        def __init__(self, content="", **kwargs):
            self.content = content
            for key, value in kwargs.items():
                setattr(self, key, value)

    class AIMessage(_Message):
        def __init__(self, content="", tool_calls=None, **kwargs):
            super().__init__(content, **kwargs)
            self.tool_calls = tool_calls or []

    class HumanMessage(_Message):
        pass

    class SystemMessage(_Message):
        pass

    class ToolMessage(_Message):
        def __init__(self, content="", tool_call_id=None, **kwargs):
            super().__init__(content, **kwargs)
            self.tool_call_id = tool_call_id

    class StructuredTool:
        """Mirrors the one factory the server uses: name + invoke(args)."""

        def __init__(self, function):
            self.func = function
            self.name = function.__name__
            self.description = function.__doc__ or ""

        @classmethod
        def from_function(cls, function):
            return cls(function)

        def invoke(self, args):
            return self.func(**(args or {}))

    _core = types.ModuleType("langchain_core")
    _core.__path__ = []          # make it a package so submodules import
    _messages = types.ModuleType("langchain_core.messages")
    for _name, _cls in (("AIMessage", AIMessage), ("HumanMessage", HumanMessage),
                        ("SystemMessage", SystemMessage), ("ToolMessage", ToolMessage)):
        setattr(_messages, _name, _cls)
    _tools = types.ModuleType("langchain_core.tools")
    _tools.StructuredTool = StructuredTool
    _core.messages, _core.tools = _messages, _tools
    sys.modules.setdefault("langchain_core", _core)
    sys.modules.setdefault("langchain_core.messages", _messages)
    sys.modules.setdefault("langchain_core.tools", _tools)


def manifest_with(**overrides):
    base = {
        "run_id": "r1", "status": "failed", "current_node": "docking",
        "target": {"target_id": "6LU7"}, "ligands": [{"ligand_id": "lig-1"}],
        "artifacts": {}, "tasks": [], "decisions": [], "human_decisions": [],
        "approvals": [], "overrides": {}, "failures": [],
        "stage_results": {
            "structure": {"status": "succeeded"},
            "receptor": {"status": "succeeded"},
            "docking": {"status": "failed", "error": "vina exited with code 1", "attempt": 2},
            "pocket": {"status": "succeeded", "metadata": {"candidates": [
                {"rank": 1, "name": "Pocket 1", "p2rank_score": 9.3,
                 "residue_ids": ["A:41", "A:145"], "selected": True},
                {"rank": 2, "name": "Pocket 2", "p2rank_score": 4.1, "residue_ids": ["A:300"]},
            ]}},
        },
    }
    base.update(overrides)
    return base


class ScriptedModel:
    """Stands in for the bound chat model, replaying a fixed script."""

    def __init__(self, script):
        self.script = list(script)
        self.seen = []

    def invoke(self, messages):
        self.seen.append(list(messages))
        return self.script.pop(0)


class ChatTestCase(unittest.TestCase):
    def setUp(self):
        self._directory = tempfile.TemporaryDirectory()
        self.root = Path(self._directory.name)
        self.run_root = self.root / "proj" / "runs" / "r1"
        (self.run_root / "logs").mkdir(parents=True)
        (self.run_root / "inputs").mkdir(parents=True)
        (self.run_root / "manifest.json").write_text(json.dumps(manifest_with()), encoding="utf-8")
        self.project_root = self.root / "proj"
        self.app = WebApplication(self.run_root, self.root, self.root, self.root, web_token=None)

    def tearDown(self):
        self.app.close()
        self._directory.cleanup()


class RunContext(ChatTestCase):
    def test_context_reports_stage_failures(self):
        context = self.app.chat_context(self.run_root)
        self.assertEqual(context["run_status"], "failed")
        self.assertEqual(context["stages"]["docking"]["status"], "failed")
        self.assertIn("vina exited", context["stages"]["docking"]["error"])
        self.assertEqual(context["stages"]["docking"]["attempt"], 2)

    def test_context_reports_pocket_candidates(self):
        pockets = self.app.chat_context(self.run_root)["pocket_candidates"]
        self.assertEqual(len(pockets), 2)
        self.assertEqual(pockets[0]["rank"], 1)
        self.assertEqual(pockets[0]["residues"], 2)
        self.assertTrue(pockets[0]["selected"])

    def test_context_reports_changed_parameters_only(self):
        self.app.save_run_parameters({"values": {"exhaustiveness": 64}}, self.run_root)
        context = self.app.chat_context(self.run_root)
        self.assertEqual(context["changed_parameters"], {"exhaustiveness": 64})

    def test_context_survives_a_missing_manifest(self):
        (self.run_root / "manifest.json").unlink()
        self.assertEqual(self.app.chat_context(self.run_root), {})

    def test_system_prompt_carries_the_failure(self):
        prompt = self.app._chat_system_prompt(self.project_root, self.run_root)
        self.assertIn("vina exited with code 1", prompt)
        self.assertIn("6LU7", prompt)
        # The scientific caveats are part of the contract with the user.
        self.assertIn("not binding affinities", prompt)


class ApprovalGate(ChatTestCase):
    def call(self, name, args, call_id="c1"):
        return AIMessage(content="", tool_calls=[{"name": name, "args": args, "id": call_id}])

    def drive(self, script, history=None):
        """Run one chat turn against a scripted model."""
        model = ScriptedModel(script)
        actions: list = []
        tools = self.app._chat_tools(self.project_root, actions)
        from langchain_core.messages import SystemMessage
        messages = [SystemMessage(content="system")]
        messages.extend(self.app._restore_messages(history or [{"role": "user", "content": "hi"}]))
        outcome = self.app._run_chat_loop(
            model, messages, {tool.name: tool for tool in tools}, actions,
            project_root=self.project_root, run_root=self.run_root, chat_id="chat-1", payload={})
        return outcome, model

    def test_model_text_blocks_are_returned_as_text_and_restored_readably(self):
        blocks = [{"type": "text", "text": "你好！\n", "id": "msg_1", "phase": "final_answer"},
                  {"type": "text", "text": "运行已完成。", "annotations": []}]
        outcome, _ = self.drive([AIMessage(content=blocks)])
        self.assertEqual(outcome["reply"], "你好！\n运行已完成。")
        restored = self.app._restore_messages([
            {"role": "user", "content": repr(blocks)},
            {"role": "assistant", "content": repr(blocks)}])
        self.assertEqual(restored[0].content, repr(blocks))
        self.assertEqual(restored[1].content, outcome["reply"])

    def test_a_command_is_not_run_before_it_is_approved(self):
        marker = self.project_root / "ran.txt"
        outcome, _ = self.drive([self.call("run_command", {"command": f"touch {marker}"})])
        self.assertIn("pending", outcome)
        self.assertEqual(outcome["pending"]["tool"], "run_command")
        self.assertFalse(marker.exists(), "the command ran before anyone approved it")

    def test_a_write_is_not_applied_before_it_is_approved(self):
        outcome, _ = self.drive([self.call("write_file", {"path": "new.txt", "content": "x"})])
        self.assertIn("pending", outcome)
        self.assertFalse((self.project_root / "new.txt").exists())

    def test_workflow_control_is_a_supervisor_proposal_until_approved(self):
        with patch.object(self.app, "request_control", return_value={"status": "pause_requested"}) as control:
            outcome, _ = self.drive([self.call("request_workflow_action", {"action": "pause"})])
            self.assertIn("pending", outcome)
            control.assert_not_called()
            action_id = outcome["pending"]["action_id"]
            self.app._chat_model = lambda payload, run_root, tools: ScriptedModel([AIMessage(content="已请求暂停")])
            resumed = self.app.resolve_chat_action({"action_id": action_id, "approved": True})
            control.assert_called_once_with("pause", self.run_root, actor="chat_supervisor")
            self.assertEqual(resumed["reply"], "已请求暂停")

    def test_reads_need_no_approval(self):
        (self.project_root / "notes.txt").write_text("hello", encoding="utf-8")
        outcome, _ = self.drive([self.call("read_file", {"path": "notes.txt"}),
                                 AIMessage(content="读到了")])
        self.assertNotIn("pending", outcome)
        self.assertEqual(outcome["reply"], "读到了")

    def test_approving_runs_the_command_and_resumes(self):
        marker = self.project_root / "ran.txt"
        outcome, _ = self.drive([self.call("run_command", {"command": f"touch {marker}"})])
        action_id = outcome["pending"]["action_id"]
        # Resume: the model gets the tool result and answers.
        self.app._chat_model = lambda payload, run_root, tools: ScriptedModel([AIMessage(content="已执行")])
        resumed = self.app.resolve_chat_action({"action_id": action_id, "approved": True})
        self.assertTrue(marker.exists(), "approval did not run the command")
        self.assertEqual(resumed["reply"], "已执行")
        self.assertTrue(resumed["decided"]["approved"])

    def test_rejecting_does_not_run_it_and_tells_the_model_why(self):
        marker = self.project_root / "ran.txt"
        outcome, _ = self.drive([self.call("run_command", {"command": f"touch {marker}"})])
        action_id = outcome["pending"]["action_id"]
        resumed_model = ScriptedModel([AIMessage(content="那我换个方式")])
        self.app._chat_model = lambda payload, run_root, tools: resumed_model
        resumed = self.app.resolve_chat_action(
            {"action_id": action_id, "approved": False, "reason": "这会覆盖结果"})
        self.assertFalse(marker.exists(), "a rejected command still ran")
        self.assertFalse(resumed["decided"]["approved"])
        # The refusal and its reason must reach the model, or it just retries.
        relayed = resumed_model.seen[0][-1].content
        self.assertIn("declined", relayed)
        self.assertIn("这会覆盖结果", relayed)

    def test_an_action_cannot_be_approved_twice(self):
        outcome, _ = self.drive([self.call("run_command", {"command": "echo hi"})])
        action_id = outcome["pending"]["action_id"]
        self.app._chat_model = lambda payload, run_root, tools: ScriptedModel([AIMessage(content="ok")])
        self.app.resolve_chat_action({"action_id": action_id, "approved": True})
        with self.assertRaises(ValueError):
            self.app.resolve_chat_action({"action_id": action_id, "approved": True})

    def test_an_unknown_action_is_refused(self):
        with self.assertRaises(ValueError):
            self.app.resolve_chat_action({"action_id": "nope", "approved": True})

    def test_a_queued_second_call_also_waits(self):
        """Two calls in one response: approving the first must not let the
        second through unreviewed."""
        marker = self.project_root / "second.txt"
        response = AIMessage(content="", tool_calls=[
            {"name": "run_command", "args": {"command": "echo first"}, "id": "c1"},
            {"name": "run_command", "args": {"command": f"touch {marker}"}, "id": "c2"},
        ])
        outcome, _ = self.drive([response])
        first = outcome["pending"]["action_id"]
        self.app._chat_model = lambda payload, run_root, tools: ScriptedModel([AIMessage(content="done")])
        resumed = self.app.resolve_chat_action({"action_id": first, "approved": True})
        self.assertIn("pending", resumed, "the queued second command skipped its own approval")
        self.assertFalse(marker.exists())

    def test_the_request_and_the_decision_are_on_the_event_timeline(self):
        outcome, _ = self.drive([self.call("run_command", {"command": "echo hi"})])
        self.app._chat_model = lambda payload, run_root, tools: ScriptedModel([AIMessage(content="ok")])
        self.app.resolve_chat_action({"action_id": outcome["pending"]["action_id"],
                                      "approved": False, "reason": "不需要"})
        events = self.app.events_for(self.run_root).read_since(0)
        kinds = [event["type"] for event in events]
        self.assertIn("chat_action_requested", kinds)
        self.assertIn("chat_action_decided", kinds)
        decision = next(event for event in events if event["type"] == "chat_action_decided")
        self.assertEqual(decision["status"], "rejected")
        self.assertEqual(decision["reason"], "不需要")

    def test_recording_a_refusal_without_resuming_calls_no_model(self):
        """The abandon path only wants the decision on the record. Resuming
        would spend a model call on a reply nobody is waiting for."""
        outcome, _ = self.drive([self.call("run_command", {"command": "echo hi"})])
        def explode(*args, **kwargs):
            raise AssertionError("the model must not be called when resume is false")
        self.app._chat_model = explode
        resumed = self.app.resolve_chat_action(
            {"action_id": outcome["pending"]["action_id"], "approved": False, "resume": False})
        self.assertEqual(resumed["reply"], "")
        self.assertFalse(resumed["decided"]["approved"])
        decisions = [event for event in self.app.events_for(self.run_root).read_since(0)
                     if event["type"] == "chat_action_decided"]
        self.assertEqual(decisions[-1]["status"], "rejected")

    def test_pending_requests_are_bounded(self):
        for index in range(40):
            self.drive([self.call("run_command", {"command": f"echo {index}"}, call_id=f"c{index}")])
        self.assertLessEqual(len(self.app._pending_actions), 32)





class StreamingChat(ChatTestCase):
    @staticmethod
    def chunk(content='', tool_calls=None):
        try:
            from langchain_core.messages import AIMessageChunk
            return AIMessageChunk(content=content, tool_calls=tool_calls or [])
        except ImportError:
            class Chunk(AIMessage):
                def __add__(self, other):
                    return Chunk(content=self.content + other.content,
                                 tool_calls=self.tool_calls + other.tool_calls)
            return Chunk(content=content, tool_calls=tool_calls or [])

    def drive_stream(self, batches):
        class Model:
            def stream(self, messages):
                yield from batches.pop(0)
            def invoke(self, messages):
                raise AssertionError('streaming must not fall back to a buffered model call')
        events, actions, messages = [], [], []
        tools = self.app._chat_tools(self.project_root, actions)
        result = self.app._run_chat_loop(Model(), messages, {tool.name: tool for tool in tools}, actions,
                                        project_root=self.project_root, run_root=self.run_root,
                                        chat_id='stream-test', payload={}, emit=events.append)
        return result, events

    def test_deltas_are_sent_and_final_reply_is_complete(self):
        result, events = self.drive_stream([[self.chunk('中文 '), self.chunk('$E=mc^2$')]])
        self.assertEqual(result['reply'], '中文 $E=mc^2$')
        self.assertEqual(events, [{'type': 'response_start'}, {'type': 'delta', 'text': '中文 '},
                                  {'type': 'delta', 'text': '$E=mc^2$'}])

    def test_streamed_mutating_tool_still_requires_approval(self):
        marker = self.project_root / 'must-not-exist.txt'
        result, events = self.drive_stream([[self.chunk('I need approval.'), self.chunk(tool_calls=[{
            'id': 'call-stream', 'name': 'run_command', 'args': {'command': f'touch {marker}'}}])]])
        self.assertFalse(marker.exists())
        self.assertIn('pending', result)
        self.assertEqual(result['reply'], 'I need approval.')
        self.assertNotIn(str(marker), json.dumps(events))

    def test_read_tool_iteration_resets_preview_before_final_response(self):
        result, events = self.drive_stream([
            [self.chunk('Checking files'), self.chunk(tool_calls=[{
                'id': 'call-read', 'name': 'list_files', 'args': {}}])],
            [self.chunk('Finished')],
        ])
        self.assertEqual(result['reply'], 'Finished')
        self.assertEqual([event['type'] for event in events].count('response_start'), 2)
        self.assertEqual(result['actions'][0]['tool'], 'list_files')


if __name__ == "__main__":
    unittest.main()
