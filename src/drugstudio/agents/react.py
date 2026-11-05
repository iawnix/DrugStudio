"""LangGraph ReAct controller for one docking run."""

from __future__ import annotations

import json
import os
import threading
import uuid
from dataclasses import dataclass, field
from typing import Annotated, Any, Dict, List, Optional, TypedDict

from ..contracts import RunState, RunStatus
from ..events import EventWriter
from .base import AgentContext
from .modules import AssessmentAgent, DockingAgent, LigandAgent, PocketAgent, ReceptorAgent, StructureAgent


SYSTEM_PROMPT = """You are the workflow supervisor for a reproducible molecular docking run.
Use the available tools to inspect the run and complete its requested stages.
Choose one tool at a time and base each next action on the tool observation.
When identify_binding_sites is available, call it after receptor preparation and
before docking. Do not infer candidate boxes from model text.
The allowed sequence is structure retrieval, receptor and ligand preparation,
optional pocket identification, docking, then assessment. Receptor and ligand
preparation can be done in either order after a usable structure exists. Do not
repeat a successful stage
unless an observation explains why it must be repeated. Do not invent tool
outputs, scores, structures, chemical properties, or approvals.

If a tool reports waiting_approval, stop and explain which approval is needed.
Human decisions and overrides in the run state are authoritative for the
affected stage. Do not replace them with a model-selected pocket or box.
If it reports waiting_input, stop and identify the missing input or preparation.
If the run is already waiting_execution at the start of a resumed session, call
retrieve_structure once to poll the recorded cluster job. If it still reports
waiting_execution, explain its current state and tell the user to resume this
same run later. Never submit a duplicate job.
If a tool fails, inspect the run and report the failure with preserved
diagnostics. Do not repeat failed calculations or bypass quality checks within
the same run. Docking may run only after receptor and ligand preparation
succeed. Assessment may run only after docking has produced results.

Treat docking scores as engine-specific empirical scores, not experimental
binding affinities. Distinguish experimental structures from predicted models,
and report uncertainty and missing evidence. Keep the final response concise
and state which stages completed, failed, or are waiting for approval."""


class ReActState(TypedDict):
    messages: List[Any]


@dataclass
class DockingReActController:
    """Run the LLM/tool observation loop while domain agents do the science."""

    model: Any
    run_state: RunState
    context: AgentContext
    structure_agent: StructureAgent
    receptor_agent: ReceptorAgent
    ligand_agent: LigandAgent
    pocket_agent: PocketAgent
    docking_agent: DockingAgent
    assessment_agent: AssessmentAgent
    recursion_limit: int = 40
    checkpointer: Any = None
    _graph: Any = field(init=False, repr=False)
    _messages: List[Any] = field(default_factory=list, init=False, repr=False)
    _lock: Any = field(default_factory=threading.RLock, init=False, repr=False)
    _invoke_lock: Any = field(default_factory=threading.Lock, init=False, repr=False)
    _thread_id: Optional[str] = field(default=None, init=False, repr=False)

    def __post_init__(self) -> None:
        self._graph = _build_graph(self.model, self._build_tools(), self.checkpointer,
                                   self.context.events, self.run_state)

    def invoke(self, prompt: str, *, thread_id: Optional[str] = None) -> Dict[str, Any]:
        from langchain_core.messages import HumanMessage, SystemMessage

        if self.checkpointer is not None and not thread_id:
            raise ValueError("thread_id is required when a LangGraph checkpointer is configured")
        # Never hold the tool/state lock while waiting for graph worker threads.
        with self._invoke_lock:
            self._emit("run_started", status="running", prompt=prompt[:500], model=_model_label(self.model))
            if self.checkpointer is not None:
                if self._thread_id is not None and thread_id != self._thread_id:
                    raise ValueError("one controller must use one checkpoint thread")
                self._thread_id = thread_id
            if not self._messages:
                if self.checkpointer is not None:
                    snapshot = self._graph.get_state({"configurable": {"thread_id": thread_id}})
                    if snapshot.values:
                        raise ValueError("restoring a graph checkpoint without matching domain state is unsupported; load the manifest and use a fresh thread")
                self._messages.append(SystemMessage(content=SYSTEM_PROMPT, id=uuid.uuid4().hex))
            previous_count = len(self._messages)
            self._messages.append(HumanMessage(content=prompt, id=uuid.uuid4().hex))
            if self.run_state.status in {RunStatus.CREATED, RunStatus.RUNNING}:
                self.run_state.status = RunStatus.RUNNING
            self.context.store.save_manifest(self.run_state)
            config: Dict[str, Any] = {"recursion_limit": self.recursion_limit}
            if thread_id:
                config["configurable"] = {"thread_id": thread_id}
            trace_path = self.context.store.path(f"logs/react-{uuid.uuid4().hex}.jsonl")
            seen_messages = set()
            try:
                # Save public messages as they arrive, including tool observations.
                # An interrupted model call must not erase earlier execution evidence.
                with trace_path.open("x", encoding="utf-8") as trace:
                    for result in self._graph.stream({"messages": self._messages}, config=config, stream_mode="values"):
                        for message in result["messages"]:
                            message_id = getattr(message, "id", None) or id(message)
                            if message_id in seen_messages:
                                continue
                            seen_messages.add(message_id)
                            record = {"type": message.type, "content": _message_text(message),
                                      "tool_calls": getattr(message, "tool_calls", []),
                                      "tool_call_id": getattr(message, "tool_call_id", None),
                                      "usage_metadata": getattr(message, "usage_metadata", None),
                                      "response_metadata": {key: value for key, value in getattr(message, "response_metadata", {}).items()
                                                            if key in {"model_name", "model_provider", "finish_reason"}}}
                            trace.write(json.dumps(record, ensure_ascii=False) + "\n")
                            trace.flush()
            except Exception as exc:
                if self.context.events is not None:
                    self.context.events.set_control_request(None)
                self._emit("run_failed", status="failed", error=f"{type(exc).__name__}: {exc}")
                self.run_state.add_failure("react_supervisor", f"{type(exc).__name__}: {exc}")
                self.run_state.status = RunStatus.FAILED
                self.context.store.save_manifest(self.run_state)
                raise

            if self.run_state.status not in {RunStatus.WAITING_APPROVAL, RunStatus.WAITING_INPUT, RunStatus.WAITING_EXECUTION, RunStatus.FAILED}:
                self._apply_control_request(stage=self.run_state.current_node)
            elif self.run_state.status == RunStatus.FAILED and self.context.events is not None:
                self.context.events.set_control_request(None)
            self._messages = list(result["messages"])
            self._record_tool_decisions(self._messages[previous_count:])
            final_message = self._messages[-1]
            response = _message_text(final_message)
            if self.run_state.status == RunStatus.PAUSED:
                response = f"Workflow paused at a safe checkpoint ({self.run_state.current_node or 'workflow'})."
            elif self.run_state.status == RunStatus.CANCELLED:
                response = f"Workflow cancelled at a safe checkpoint ({self.run_state.current_node or 'workflow'})."
            if self.run_state.status not in {RunStatus.WAITING_APPROVAL, RunStatus.WAITING_INPUT, RunStatus.WAITING_EXECUTION,
                                             RunStatus.FAILED, RunStatus.PAUSED, RunStatus.CANCELLED}:
                if _assessment_complete(self.run_state):
                    self.run_state.status = RunStatus.SUCCEEDED
                else:
                    self.run_state.status = RunStatus.RUNNING
            self.run_state.current_node = "react_supervisor"
            report = self.context.store.publish_bytes(
                relative_path=f"results/agent-response-{uuid.uuid4().hex}.md",
                content=(response.rstrip() + "\n").encode("utf-8"),
                kind="agent_response",
                metadata={"run_id": self.run_state.run_id, "status": self.run_state.status.value},
            )
            self.run_state.add_artifact("agent_response", report)
            self.context.store.save_manifest(self.run_state)
            self._emit("run_completed", status=self.run_state.status.value,
                       current_node=self.run_state.current_node)
            return {
                "response": response,
                "status": self.run_state.status.value,
                "run_state": self.run_state,
                "messages": self._messages,
            }

    def _emit(self, event_type: str, *, stage: Optional[str] = None,
              status: Optional[str] = None, **metadata: Any) -> None:
        if self.context.events is not None:
            self.context.events.emit(event_type, stage=stage, status=status, **metadata)

    def _record_tool_decisions(self, messages: List[Any]) -> None:
        observations: Dict[str, Any] = {}
        for message in messages:
            call_id = getattr(message, "tool_call_id", None)
            if call_id:
                observations[call_id] = _observation_summary(getattr(message, "content", ""))
        for message in messages:
            for call in getattr(message, "tool_calls", []) or []:
                self.run_state.decisions.append(
                    {
                        "agent": "llm_supervisor",
                        "action": call.get("name"),
                        "tool_call_id": call.get("id"),
                        "arguments": call.get("args", {}),
                        "observation": observations.get(call.get("id")),
                    }
                )

    def _build_tools(self) -> List[Any]:
        from langchain_core.tools import StructuredTool

        def inspect_run() -> str:
            with self._lock:
                return json.dumps(_run_summary(self.run_state), ensure_ascii=False, sort_keys=True)

        def retrieve_structure() -> str:
            return self._invoke_stage("structure", self.structure_agent)

        def prepare_receptor() -> str:
            return self._invoke_stage("receptor", self.receptor_agent)

        def prepare_ligands() -> str:
            return self._invoke_stage("ligand", self.ligand_agent)

        def identify_binding_sites() -> str:
            return self._invoke_stage("pocket", self.pocket_agent)

        def run_docking() -> str:
            return self._invoke_stage("docking", self.docking_agent)

        def assess_results() -> str:
            return self._invoke_stage("assessment", self.assessment_agent)

        tool_specs = (
            (inspect_run, "inspect_run", "Inspect current stage status, artifacts, approvals, and failures."),
            (retrieve_structure, "retrieve_structure", "Find an experimental structure, then use the configured prediction fallback if needed."),
            (prepare_receptor, "prepare_receptor", "Prepare the selected experimental or predicted protein structure for docking."),
            (prepare_ligands, "prepare_ligands", "Prepare each requested ligand using the configured chemistry pipeline."),
            (identify_binding_sites, "identify_binding_sites", "Predict and rank binding pockets and propose Vina boxes when automatic box selection is enabled."),
            (run_docking, "run_docking", "Run the configured docking engine after receptor and ligand preparation."),
            (assess_results, "assess_results", "Summarize docking QC and molecular quality using the configured assessor."),
        )
        return [
            StructuredTool.from_function(func=func, name=name, description=description)
            for func, name, description in tool_specs
            if name != "identify_binding_sites" or self.context.configuration.get("auto_box")
        ]

    def _invoke_stage(self, name: str, agent: Any) -> str:
        with self._lock:
            if self.run_state.status in {RunStatus.PAUSED, RunStatus.CANCELLED}:
                return _blocked_observation(self.run_state, name)
            if self.run_state.status in {RunStatus.WAITING_APPROVAL, RunStatus.WAITING_INPUT, RunStatus.FAILED} or (
                self.run_state.status == RunStatus.WAITING_EXECUTION and name != "structure"
            ):
                return _blocked_observation(self.run_state, name)
            if _stage_complete(self.run_state, name):
                summary = _run_summary(self.run_state, stage=name)
                summary["status"] = "already_complete"
                return json.dumps(summary, ensure_ascii=False, sort_keys=True)
            dependencies = {"receptor": ("structure",), "ligand": ("structure",),
                            "pocket": ("receptor",), "docking": ("receptor", "ligand"),
                            "assessment": ("docking",)}
            if name == "docking" and self.context.configuration.get("auto_box"):
                dependencies["docking"] = (*dependencies["docking"], "pocket")
            missing = [stage for stage in dependencies.get(name, ()) if not _stage_complete(self.run_state, stage)]
            if missing:
                return _tool_error(name, "complete these stages first: " + ", ".join(missing))
            if name == "ligand" and not self.run_state.ligands:
                return _tool_error(name, "the run has no ligands")
            previous_attempt = int(self.run_state.stage_results.get(name, {}).get("attempt", 0) or 0)
            self.run_state.stage_results[name] = {
                "status": "running",
                **({"attempt": previous_attempt + 1} if name in {"docking", "assessment"} else {}),
            }
            self._emit("stage_started", stage=name, status="running",
                       attempt=previous_attempt + 1)
            self.context.store.save_manifest(self.run_state)
            try:
                before = {item.artifact_id for items in self.run_state.artifacts.values() for item in items}
                agent.invoke(self.run_state, self.context)
                for stage_items in self.run_state.artifacts.values():
                    for artifact in stage_items:
                        if artifact.artifact_id not in before:
                            self._emit("artifact_published", stage=name, status="succeeded",
                                       artifact_id=artifact.artifact_id, kind=artifact.kind,
                                       path=artifact.path, parent_ids=artifact.parent_ids)
                if self.run_state.status not in {RunStatus.WAITING_APPROVAL, RunStatus.WAITING_INPUT, RunStatus.WAITING_EXECUTION, RunStatus.FAILED}:
                    if not _stage_has_outputs(self.run_state, name):
                        raise RuntimeError(f"{name} returned without required output artifacts")
                    self.run_state.stage_results[name].update(status="succeeded", error=None)
                    self._emit("stage_completed", stage=name, status="succeeded",
                               artifact_count=len(self.run_state.artifacts.get(name, [])))
                else:
                    self.run_state.stage_results[name]["status"] = self.run_state.status.value
                    event_type = ("waiting_approval" if self.run_state.status == RunStatus.WAITING_APPROVAL
                                  else "stage_progress")
                    self._emit(event_type, stage=name, status=self.run_state.status.value,
                               reason=self.run_state.stage_results[name].get("error"),
                               metadata=self.run_state.stage_results[name].get("metadata", {}))
            except Exception as exc:
                self.run_state.add_failure(name, f"{type(exc).__name__}: {exc}")
                self.run_state.status = RunStatus.FAILED
                self.run_state.stage_results[name].update(status="failed", error=str(exc))
                self._emit("stage_failed", stage=name, status="failed", error=str(exc))
            if self.run_state.status not in {RunStatus.WAITING_APPROVAL, RunStatus.WAITING_INPUT, RunStatus.WAITING_EXECUTION, RunStatus.FAILED}:
                self._apply_control_request(stage=name)
            elif self.run_state.status == RunStatus.FAILED and self.context.events is not None:
                self.context.events.set_control_request(None)
            self.context.store.save_manifest(self.run_state)
            return json.dumps(_run_summary(self.run_state, stage=name), ensure_ascii=False, sort_keys=True)

    def _apply_control_request(self, *, stage: Optional[str] = None) -> None:
        events = self.context.events
        action = events.control_request() if events is not None else None
        if not action:
            return
        if self.run_state.status in {RunStatus.WAITING_APPROVAL, RunStatus.WAITING_INPUT, RunStatus.FAILED}:
            events.set_control_request(None)
            return
        self.run_state.status = RunStatus.PAUSED if action == "pause" else RunStatus.CANCELLED
        self._emit("run_paused" if action == "pause" else "run_cancelled",
                   stage=stage, status=self.run_state.status.value,
                   checkpoint=stage or self.run_state.current_node)
        if events is not None:
            events.set_control_request(None)


def create_openai_chat_model(model: Optional[str] = None, *, temperature: Optional[float] = None) -> Any:
    """Create the optional OpenAI LangChain chat model from environment config."""

    if not os.environ.get("OPENAI_API_KEY"):
        raise RuntimeError("Set OPENAI_API_KEY in the process environment before starting the LLM agent")
    try:
        from langchain_openai import ChatOpenAI
    except ImportError as exc:
        raise RuntimeError("Install the optional OpenAI integration with: pip install 'drugstudio[openai]'") from exc
    return ChatOpenAI(
        model=model or os.environ.get("DOCK_AGENT_MODEL", "gpt-4.1-mini"),
        temperature=temperature,
        timeout=90,
        max_retries=1,
    )


def _build_graph(model: Any, tools: List[Any], checkpointer: Any = None,
                 events: EventWriter | None = None, run_state: RunState | None = None) -> Any:
    try:
        from langchain_core.messages import AIMessage, ToolMessage
        from langgraph.graph import END, START, StateGraph
        from langgraph.graph.message import add_messages
    except ImportError as exc:
        raise RuntimeError("Install LangGraph and LangChain Core with: pip install 'drugstudio[orchestration]'") from exc

    try:
        model_with_tools = model.bind_tools(tools, parallel_tool_calls=False)
    except TypeError:
        model_with_tools = model.bind_tools(tools)

    def call_model(state: ReActState) -> Dict[str, Any]:
        request_id = uuid.uuid4().hex
        if events is not None:
            events.emit("model_started", status="reasoning", model=_model_label(model), request_id=request_id)
        try:
            reply = model_with_tools.invoke(state["messages"])
        except Exception as exc:
            if events is not None:
                events.emit("model_finished", status="error", model=_model_label(model),
                            request_id=request_id, error=f"{type(exc).__name__}: {exc}")
            raise
        if not isinstance(reply, AIMessage):
            raise TypeError("chat model must return a LangChain AIMessage")
        if events is not None:
            events.emit("model_finished", status="completed", model=_model_label(model),
                        usage=getattr(reply, "usage_metadata", None),
                        request_id=request_id,
                        finish_reason=getattr(reply, "response_metadata", {}).get("finish_reason"))
        return {"messages": [reply]}

    def route_after_model(state: ReActState) -> str:
        if run_state is not None and events is not None and run_state.status not in {
                RunStatus.WAITING_APPROVAL, RunStatus.WAITING_INPUT, RunStatus.WAITING_EXECUTION, RunStatus.FAILED}:
            action = events.control_request()
            if action:
                run_state.status = RunStatus.PAUSED if action == "pause" else RunStatus.CANCELLED
                events.set_control_request(None)
                events.emit("run_paused" if action == "pause" else "run_cancelled",
                            status=run_state.status.value, checkpoint=run_state.current_node)
        if run_state is not None and run_state.status in {RunStatus.PAUSED, RunStatus.CANCELLED}:
            return END
        message = state["messages"][-1]
        return "tools" if getattr(message, "tool_calls", None) else END

    tool_by_name = {tool.name: tool for tool in tools}

    def call_tools(state: ReActState) -> Dict[str, Any]:
        # Providers can ignore parallel_tool_calls=False. Enforce order here.
        observations = []
        for call in state["messages"][-1].tool_calls:
            name = call["name"]
            stage = _tool_stage(name)
            if events is not None:
                events.emit("tool_started", stage=stage, status="calling_tool", tool=name,
                            tool_call_id=call.get("id"),
                            arguments=_bounded_observation(call.get("args", {})))
            try:
                tool = tool_by_name.get(name)
                if tool is None:
                    raise ValueError(f"unknown tool: {name}")
                observation = tool.invoke(call["args"])
                if events is not None:
                    events.emit("tool_finished", stage=stage, status=_observation_status(observation),
                                tool=name, tool_call_id=call.get("id"),
                                observation=_observation_summary(observation))
            except Exception as exc:
                observation = _tool_error(name, f"{type(exc).__name__}: {exc}")
                if events is not None:
                    events.emit("tool_finished", stage=stage, status="failed", tool=name,
                                tool_call_id=call.get("id"), error=f"{type(exc).__name__}: {exc}")
            observations.append(ToolMessage(content=str(observation), tool_call_id=call["id"], name=name))
        return {"messages": observations}

    def route_after_tools(_: ReActState) -> str:
        return END if run_state is not None and run_state.status in {
            RunStatus.PAUSED, RunStatus.CANCELLED, RunStatus.WAITING_APPROVAL,
            RunStatus.WAITING_INPUT, RunStatus.WAITING_EXECUTION, RunStatus.FAILED} else "reason"

    graph_state = TypedDict(
        "ReActGraphState",
        {"messages": Annotated[List[Any], add_messages]},
    )
    graph = StateGraph(graph_state)
    graph.add_node("reason", call_model)
    graph.add_node("act", call_tools)
    graph.add_edge(START, "reason")
    graph.add_conditional_edges("reason", route_after_model, {"tools": "act", END: END})
    graph.add_conditional_edges("act", route_after_tools, {"reason": "reason", END: END})
    return graph.compile(checkpointer=checkpointer)


def _run_summary(state: RunState, *, stage: Optional[str] = None) -> Dict[str, Any]:
    artifacts = {
        name: [{"id": item.artifact_id, "kind": item.kind, "path": item.path} for item in items]
        for name, items in state.artifacts.items()
    }
    return {
        "stage": stage,
        "run_id": state.run_id,
        "status": state.status.value,
        "current_node": state.current_node,
        "target_id": state.target.target_id,
        "ligand_ids": [ligand.ligand_id for ligand in state.ligands],
        "artifacts": _bounded_observation(artifacts),
        "task_count": len(state.tasks),
        "approvals": state.approvals,
        "human_decisions": _bounded_observation(state.human_decisions[-10:]),
        "overrides": _bounded_observation(state.overrides),
        "failures": state.failures,
        "decisions": _bounded_observation(state.decisions[-10:]),
        "stage_results": _bounded_observation(state.stage_results),
    }


def _message_text(message: Any) -> str:
    content = getattr(message, "content", "")
    if isinstance(content, str):
        return content
    return "\n".join(str(item.get("text", "")) for item in content if isinstance(item, dict))


def _model_label(model: Any) -> str:
    kwargs = getattr(model, "model_kwargs", {}) or {}
    return str(getattr(model, "model_name", None) or getattr(model, "model", None)
               or kwargs.get("model") or type(model).__name__)


def _tool_stage(name: str) -> Optional[str]:
    return {"retrieve_structure": "structure", "prepare_receptor": "receptor",
            "prepare_ligands": "ligand", "identify_binding_sites": "pocket",
            "run_docking": "docking", "assess_results": "assessment"}.get(name)


def _observation_status(observation: Any) -> str:
    summary = _observation_summary(observation)
    status = summary.get("status") if isinstance(summary, dict) else None
    if status in {"failed", "waiting_approval", "waiting_input", "waiting_execution"}:
        return str(status)
    return "succeeded"


def _observation_summary(content: Any) -> Dict[str, Any]:
    text = content if isinstance(content, str) else str(content)
    try:
        value = json.loads(text)
    except (TypeError, json.JSONDecodeError):
        return {"text": text[:500]}
    if not isinstance(value, dict):
        return {"value": value}
    summary = {key: value[key] for key in ("stage", "status", "reason", "error") if key in value}
    if "artifacts" in value:
        summary["artifact_stages"] = list(value["artifacts"].keys())
    if "approvals" in value and value["approvals"]:
        summary["approvals"] = value["approvals"]
    if "failures" in value and value["failures"]:
        summary["failures"] = value["failures"]
    return summary


def _has_artifact(state: RunState, stage: str, kind: Optional[str] = None) -> bool:
    artifacts = state.artifacts.get(stage, [])
    return any(kind is None or item.kind == kind for item in artifacts)


def _has_artifacts(state: RunState, stage: str) -> bool:
    return bool(state.artifacts.get(stage))


def _prepared_ligand_count(state: RunState) -> int:
    ligand_ids = {
        stage.split(":", 1)[1]
        for stage, artifacts in state.artifacts.items()
        if stage.startswith("ligand:")
        for artifact in artifacts
        if artifact.kind == "prepared_ligand_pdbqt"
    }
    return len(ligand_ids.intersection(ligand.ligand_id for ligand in state.ligands))


def _assessment_complete(state: RunState) -> bool:
    return _stage_complete(state, "docking") and _stage_complete(state, "assessment")


def _stage_complete(state: RunState, stage: str) -> bool:
    return state.stage_results.get(stage, {}).get("status") == "succeeded" and _stage_has_outputs(state, stage)


def _stage_has_outputs(state: RunState, stage: str) -> bool:
    if stage == "structure":
        return bool(
            _has_artifact(state, "structure", "experimental_structure")
            or _has_artifact(state, "structure", "supplied_structure")
            or _has_artifact(state, "structure_prediction", "predicted_structure")
        )
    if stage == "receptor":
        return _has_artifact(state, "receptor", "prepared_receptor_pdbqt")
    if stage == "pocket":
        return _has_artifact(state, "pocket", "pocket_candidates")
    if stage == "ligand":
        return bool(state.ligands) and _prepared_ligand_count(state) == len(state.ligands)
    if stage == "docking":
        return _has_artifact(state, "docking", "docking_output")
    if stage == "assessment":
        return _has_artifact(state, "assessment", "assessment_report")
    return False


def _bounded_observation(value: Any, depth: int = 0) -> Any:
    """Expose numerical evidence without sending full logs to the model."""
    if depth > 8:
        return "[nested detail omitted; see manifest]"
    if isinstance(value, dict):
        entries = [(key, item) for key, item in value.items()
                   if key not in {"stdout", "stderr", "builder_output", "request"}]
        result = {str(key): _bounded_observation(item, depth + 1) for key, item in entries[:40]}
        if len(entries) > 40:
            result["_omitted_keys"] = len(entries) - 40
        return result
    if isinstance(value, (list, tuple)):
        result = [_bounded_observation(item, depth + 1) for item in value[:20]]
        if len(value) > 20:
            result.append({"omitted_items": len(value) - 20, "full_data": "run manifest and result artifacts"})
        return result
    if isinstance(value, str) and len(value) > 2000:
        return value[:2000] + " [truncated]"
    return value


def _blocked_observation(state: RunState, stage: str) -> str:
    return json.dumps({"stage": stage, "status": state.status.value, "approvals": state.approvals,
                       "human_decisions": state.human_decisions[-10:], "overrides": state.overrides,
                       "failures": state.failures, "stage_results": _bounded_observation(state.stage_results)}, ensure_ascii=False)


def _tool_error(stage: str, message: str) -> str:
    return json.dumps({"stage": stage, "status": "blocked", "reason": message}, ensure_ascii=False)


__all__ = ["DockingReActController", "ReActState", "SYSTEM_PROMPT", "create_openai_chat_model"]
