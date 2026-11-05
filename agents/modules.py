"""Reference implementations for the independent workflow modules."""

from __future__ import annotations

from dataclasses import replace
import hashlib
import json
from typing import Iterable, List, Optional

from ..contracts import ArtifactRef, DockingTask, RunState, RunStatus
from ..ports import (
    Assessor,
    DockingEngine,
    LigandPreparer,
    PocketFinder,
    ReceptorPreparer,
    StructureProvider,
    ToolContext,
    ToolResult,
)
from ..integrations.structure_qc import assess_pdb
from .base import AgentContext, AgentDecision, DomainAgent, ensure_workdir


class StructureAgent:
    """Select an experimental structure or fall back to prediction."""

    name = "structure"

    def __init__(self, provider: StructureProvider):
        self.provider = provider

    def invoke(self, state: RunState, context: AgentContext) -> RunState:
        state.current_node = self.name
        search = self.provider.search(state.target, _stage_context(context, self.name))
        _record_result(state, "structure", search)
        if search.status == "succeeded" and any(
            item.kind in {"experimental_structure", "supplied_structure"} for item in search.artifacts
        ):
            state.decisions.append(
                _decision(
                    self.name,
                    "select_structure",
                    "Selected the provider's structure; target and binding-site suitability require scientific review.",
                    outputs={"artifact_ids": [item.artifact_id for item in search.artifacts]},
                )
            )
            return state

        if search.status not in {"no_match", "unsupported"}:
            return _fail_state(state, self.name, search.error or "structure search returned no usable coordinates")

        if not state.policy.allow_structure_prediction:
            _fail(state, self.name, search.error or "no suitable experimental structure")
            state.status = RunStatus.FAILED
            return state

        prediction = self.provider.predict(state.target, _stage_context(context, "structure_prediction"))
        if prediction.status == "waiting_approval":
            _record_result(state, "structure_prediction", prediction)
            state.status = RunStatus.WAITING_APPROVAL
            state.approvals.append(
                {
                    "stage": self.name,
                    "reason": "AlphaFold Server request is prepared and requires user-authorized browser submission.",
                }
            )
            state.decisions.append(
                _decision(
                    self.name,
                    "prepare_prediction_request",
                    "No database prediction was available; a validated AlphaFold Server request is ready for approval.",
                    requires_approval=True,
                )
            )
            return state
        if prediction.status == "waiting_execution":
            _record_result(state, "structure_prediction", prediction)
            state.status = RunStatus.WAITING_EXECUTION
            state.stage_results["structure_prediction"].update(status="waiting_execution")
            return state
        if prediction.status != "succeeded" or not any(item.kind == "predicted_structure" for item in prediction.artifacts):
            _record_result(state, "structure_prediction", prediction)
            _fail(state, self.name, prediction.error or "structure prediction failed")
            state.status = RunStatus.FAILED
            return state

        _record_result(state, "structure_prediction", prediction)
        predicted_coordinate = next((item for item in reversed(prediction.artifacts)
                                     if item.kind == "predicted_structure"), None)
        if predicted_coordinate is not None:
            try:
                quality = assess_pdb(context.store.path(predicted_coordinate.path), predicted=True,
                                     expected_sequence=state.target.sequence)
                quality_artifact = context.store.publish_json(
                    relative_path="results/structure_prediction/structure_quality.json", value=quality,
                    kind="predicted_structure_quality_report", parent_ids=[predicted_coordinate.artifact_id])
                state.add_artifact("structure_prediction", quality_artifact)
                state.stage_results["structure_prediction"].setdefault("metadata", {})["structure_quality"] = quality
                if quality["gate"] == "fail":
                    return _fail_state(state, self.name, "; ".join(quality["hard_failures"]))
            except (OSError, ValueError) as exc:
                return _fail_state(state, self.name, f"predicted structure quality assessment failed: {exc}")
        state.stage_results["structure"] = {"status": "succeeded", "metadata": {
            "selected_stage": "structure_prediction", "source": prediction.metadata.get("source")}}
        state.decisions.append(
            _decision(
                self.name,
                "request_prediction",
                "No suitable experimental structure was returned; prediction was allowed by policy.",
                outputs={"artifact_ids": [item.artifact_id for item in prediction.artifacts]},
            )
        )
        return state


class ReceptorAgent:
    name = "receptor"

    def __init__(self, preparer: ReceptorPreparer):
        self.preparer = preparer

    def invoke(self, state: RunState, context: AgentContext) -> RunState:
        state.current_node = self.name
        structure = _latest_structure_artifact(state, "structure") or _latest_structure_artifact(
            state, "structure_prediction"
        )
        if structure is None:
            return _fail_state(state, self.name, "no structure artifact is available")
        result = self.preparer.prepare(
            structure,
            state.target,
            _stage_context(context, self.name),
        )
        _record_result(state, self.name, result)
        if result.status == "waiting_input":
            state.status = RunStatus.WAITING_INPUT
            return state
        if result.status == "waiting_approval":
            state.status = RunStatus.WAITING_APPROVAL
            if not any(item.get("stage") == self.name and item.get("reason") == (result.error or "receptor repair needs approval")
                       for item in state.approvals):
                state.approvals.append({"stage": self.name, "approved": False,
                                        "reason": result.error or "receptor repair needs approval"})
            return state
        if result.status != "succeeded":
            return _fail_state(state, self.name, result.error or "receptor preparation failed")
        prepared = next((item for item in reversed(result.artifacts)
                         if item.kind == "prepared_receptor_pdb"), None)
        if prepared is None:
            return _fail_state(state, self.name, "receptor preparation produced no coordinate PDB for quality assessment")
        try:
            predicted = any(item.kind == "predicted_structure"
                            for items in state.artifacts.values() for item in items)
            quality = assess_pdb(context.store.path(prepared.path), predicted=False,
                                 expected_sequence=state.target.sequence)
            if predicted:
                prediction_quality = state.stage_results.get("structure_prediction", {}).get("metadata", {}).get("structure_quality")
                if prediction_quality:
                    quality["prediction_confidence"] = {
                        "gate": prediction_quality.get("gate"),
                        "ca_bfactor_median": prediction_quality.get("ca_bfactor_median"),
                        "ca_bfactor_below_50_fraction": prediction_quality.get("ca_bfactor_below_50_fraction"),
                        "review_flags": prediction_quality.get("review_flags", []),
                        "source": "unprepared predicted structure; confidence evaluated before receptor preparation",
                    }
                    quality["review_flags"].extend(
                        flag for flag in prediction_quality.get("review_flags", [])
                        if flag not in quality["review_flags"])
                    if quality["review_flags"]:
                        quality["gate"] = "review"
            quality_artifact = context.store.publish_json(
                relative_path="results/receptor/structure_quality.json", value=quality,
                kind="structure_quality_report", parent_ids=[prepared.artifact_id])
            state.add_artifact(self.name, quality_artifact)
            state.stage_results[self.name].setdefault("metadata", {})["structure_quality"] = quality
            if quality["gate"] == "fail":
                return _fail_state(state, self.name, "; ".join(quality["hard_failures"]))
        except (OSError, ValueError) as exc:
            return _fail_state(state, self.name, f"structure quality assessment failed: {exc}")
        state.decisions.append(
            _decision(self.name, "prepare", "Prepared the receptor through the configured adapter.")
        )
        return state


class LigandAgent:
    name = "ligand"

    def __init__(self, preparer: LigandPreparer):
        self.preparer = preparer

    def invoke(self, state: RunState, context: AgentContext) -> RunState:
        state.current_node = self.name
        for ligand in state.ligands:
            result = self.preparer.prepare(ligand, _stage_context(context, f"{self.name}/{ligand.ligand_id}"))
            _record_result(state, f"ligand:{ligand.ligand_id}", result)
            if result.status != "succeeded":
                _fail(state, self.name, result.error or f"failed to prepare {ligand.ligand_id}", ligand_id=ligand.ligand_id)
        if state.failures:
            return _fail_state(state, self.name, "one or more ligands failed preparation", preserve_failure=True)
        state.stage_results[self.name] = {"status": "succeeded", "metadata": {"ligand_count": len(state.ligands)}}
        state.decisions.append(
            _decision(self.name, "prepare", "Prepared all requested ligand inputs through the configured adapter.")
        )
        return state


class PocketAgent:
    name = "pocket"

    def __init__(self, finder: PocketFinder):
        self.finder = finder

    def invoke(self, state: RunState, context: AgentContext) -> RunState:
        state.current_node = self.name
        receptor = _latest_prepared_artifact(state, "receptor", "prepared_receptor_pdb")
        if receptor is None:
            return _fail_state(state, self.name, "prepared receptor coordinates are required for pocket prediction")
        predicted = any(item.kind == "predicted_structure"
                        for items in state.artifacts.values() for item in items)
        quality = state.stage_results.get("receptor", {}).get("metadata", {}).get("structure_quality")
        if quality is None:
            try:
                quality = assess_pdb(context.store.path(receptor.path), predicted=predicted,
                                     expected_sequence=state.target.sequence)
                quality_artifact = context.store.publish_json(
                    relative_path="results/pocket/structure_quality.json", value=quality,
                    kind="structure_quality_report", parent_ids=[receptor.artifact_id])
                state.add_artifact(self.name, quality_artifact)
            except (OSError, ValueError) as exc:
                return _fail_state(state, self.name, f"structure quality assessment failed: {exc}")
        if quality["gate"] == "fail":
            state.stage_results[self.name] = {"status": "failed", "metadata": quality,
                                               "error": "; ".join(quality["hard_failures"])}
            state.status = RunStatus.FAILED
            return state
        result = self.finder.predict(receptor, is_predicted=predicted,
                                     context=_stage_context(context, self.name, {
                                         "binding_site_residues": state.target.binding_site_residues,
                                         "known_site_provided": bool(state.target.binding_site_residues),
                                         "structure_quality": quality,
                                     }))
        _record_result(state, self.name, result)
        if result.status == "waiting_input":
            state.status = RunStatus.WAITING_INPUT
            return state
        if result.status != "succeeded":
            return _fail_state(state, self.name, result.error or "pocket prediction failed")
        state.decisions.append(_decision(
            self.name, "rank_pockets",
            "P2Rank produced computational pocket candidates; quality flags and reference-site overlap are evidence, not proof of binding.",
            outputs={"candidate_count": len(result.metadata.get("candidates", [])),
                     "profile": result.metadata.get("profile"), "structure_gate": quality["gate"],
                     "review_recommended": result.metadata.get("review_recommended", True)}))
        return state


class DockingAgent:
    name = "docking"

    def __init__(self, engine: DockingEngine):
        self.engine = engine

    def invoke(self, state: RunState, context: AgentContext) -> RunState:
        state.current_node = self.name
        receptor = _latest_prepared_artifact(state, "receptor", "prepared_receptor_pdbqt")
        ligands = [
            artifact
            for stage, items in state.artifacts.items()
            if stage.startswith("ligand:")
            for artifact in items
            if artifact.kind == "prepared_ligand_pdbqt"
        ]
        if receptor is None or not ligands:
            return _fail_state(state, self.name, "receptor and prepared ligands are required")
        # A human review can select a different ranked pocket or provide a
        # custom box.  This override is applied before planning so the exact
        # reviewed coordinates become part of the task fingerprint.
        override = state.overrides.get("docking_box", {})
        if override:
            if override.get("pocket_rank") is not None:
                rank = int(override["pocket_rank"])
                pocket = state.stage_results.get("pocket", {}).get("metadata", {})
                candidates = pocket.get("candidates", [])
                candidate = next((item for item in candidates if int(item.get("rank", -1)) == rank), None)
                if candidate is None:
                    return _fail_state(state, self.name, f"human-selected pocket rank {rank} is unavailable")
                self.engine.center = tuple(candidate["box"]["center"])
                self.engine.size = tuple(candidate["box"]["size"])
                for item in candidates:
                    item["selected"] = int(item.get("rank", -1)) == rank
                pocket["selected_rank"] = rank
                pocket["selected_box"] = candidate["box"]
            elif override.get("center") is not None and override.get("size") is not None:
                self.engine.center = tuple(float(value) for value in override["center"])
                self.engine.size = tuple(float(value) for value in override["size"])
            else:
                return _fail_state(state, self.name, "human docking-box override is incomplete")
        elif context.configuration.get("auto_box"):
            pocket = state.stage_results.get("pocket", {}).get("metadata", {})
            candidates = pocket.get("candidates", [])
            rank = int(context.configuration.get("pocket_rank", 1))
            candidate = next((item for item in candidates if int(item.get("rank", -1)) == rank), None)
            if candidate is None:
                return _fail_state(state, self.name, f"P2Rank candidate rank {rank} is unavailable")
            self.engine.center = tuple(candidate["box"]["center"])
            self.engine.size = tuple(candidate["box"]["size"])
            for item in candidates:
                item["selected"] = False
            candidate["selected"] = True
            pocket["selected_rank"] = rank
            pocket["selected_box"] = candidate["box"]
        attempt = int(state.stage_results.get(self.name, {}).get("attempt", 1))
        docking_context = _stage_context(context, "docking", {"docking_attempt": attempt})
        tasks = self.engine.plan(receptor, ligands, docking_context)
        if not tasks:
            return _fail_state(state, self.name, "docking plan contains no tasks")
        fingerprint = hashlib.sha256(json.dumps([
            {"engine": task.engine, "inputs": task.input_artifact_ids,
             "center": task.metadata.get("center"), "size": task.metadata.get("size")}
            for task in tasks
        ], sort_keys=True).encode()).hexdigest()
        approved = (context.configuration.get("box_approved") is True
                    or bool(override.get("approved"))
                    or any(
            item.get("stage") == "docking_box" and item.get("approved") is True
            and item.get("fingerprint") == fingerprint for item in state.approvals))
        if state.policy.require_human_approval_for_box:
            approval = next((item for item in state.approvals
                             if item.get("stage") == "docking_box"
                             and item.get("fingerprint") == fingerprint), None)
            if approval is None and override.get("decision_id"):
                approval = next((item for item in state.approvals
                                 if item.get("stage") == "docking_box"
                                 and item.get("decision_id") == override.get("decision_id")), None)
                if approval is not None:
                    approval["fingerprint"] = fingerprint
                    approval["boxes"] = [{"center": task.metadata.get("center"),
                                          "size": task.metadata.get("size")} for task in tasks]
            if approval is None:
                state.approvals.append({
                    "stage": "docking_box", "approved": approved, "fingerprint": fingerprint,
                    "boxes": [{"center": task.metadata.get("center"), "size": task.metadata.get("size")}
                              for task in tasks],
                    "source": "human_override" if override else "cli_or_pending",
                    "decision_id": override.get("decision_id"),
                    "reason": override.get("reason") or "Use --approve-box or the review command to approve these coordinates in Å.",
                })
            elif approved:
                approval["approved"] = True
            if not approved:
                state.status = RunStatus.WAITING_APPROVAL
                state.stage_results[self.name].update(status="waiting_approval")
                return state
        if override:
            state.decisions.append(_decision(
                self.name, "apply_human_override",
                "Applied the recorded human review before docking; the Agent recommendation remains in the manifest.",
                decision_id=override.get("decision_id"), override=override,
            ))
        state.tasks = tasks
        result = self.engine.run(tasks, docking_context)
        _record_result(state, self.name, result)
        if result.status != "succeeded":
            return _fail_state(state, self.name, result.error or "docking failed")
        state.decisions.append(
            _decision(self.name, "run", f"Executed docking with the {self.engine.name} adapter.", outputs={"task_count": len(tasks)})
        )
        return state


class AssessmentAgent:
    name = "assessment"

    def __init__(self, assessor: Assessor):
        self.assessor = assessor

    def invoke(self, state: RunState, context: AgentContext) -> RunState:
        state.current_node = self.name
        artifacts = [artifact for items in state.artifacts.values() for artifact in items]
        attempt = int(state.stage_results.get("docking", {}).get("attempt", 1))
        result = self.assessor.assess(artifacts, _stage_context(context, self.name,
                                                               {"docking_attempt": attempt}))
        _record_result(state, self.name, result)
        if result.status != "succeeded":
            return _fail_state(state, self.name, result.error or "assessment failed")
        state.decisions.append(
            _decision(self.name, "assess", "Computed the configured docking and drug-likeness assessment.")
        )
        return state


def _stage_context(context: AgentContext, stage: str, configuration: Optional[dict] = None) -> ToolContext:
    merged = dict(context.tools.configuration)
    if configuration:
        merged.update(configuration)
    return replace(context.tools, workdir=ensure_workdir(context, stage), configuration=merged)


def _record_result(state: RunState, stage: str, result: ToolResult) -> None:
    payload = {
        "status": result.status, "metadata": result.metadata, "error": result.error,
    }
    if stage in {"docking", "assessment"}:
        attempt = result.metadata.get("attempt") if isinstance(result.metadata, dict) else None
        if attempt is None:
            attempt = state.stage_results.get(stage, {}).get("attempt")
        if attempt is not None:
            payload["attempt"] = int(attempt)
    state.stage_results[stage] = payload
    for artifact in result.artifacts:
        state.add_artifact(stage, artifact)


def _latest_artifact(state: RunState, stage: str) -> Optional[ArtifactRef]:
    artifacts = state.artifacts.get(stage, [])
    return artifacts[-1] if artifacts else None


def _latest_structure_artifact(state: RunState, stage: str) -> Optional[ArtifactRef]:
    """Select the coordinate file when a stage also published metadata JSON."""

    artifacts = state.artifacts.get(stage, [])
    for artifact in reversed(artifacts):
        if artifact.kind in {"experimental_structure", "predicted_structure", "supplied_structure"}:
            return artifact
    return None


def _latest_prepared_artifact(state: RunState, stage: str, kind: str) -> Optional[ArtifactRef]:
    artifacts = [artifact for artifact in state.artifacts.get(stage, []) if artifact.kind == kind]
    return artifacts[-1] if artifacts else None


def _decision(agent: str, action: str, rationale: str, **kwargs: object) -> dict:
    return {
        "agent": agent,
        "action": action,
        "rationale": rationale,
        **kwargs,
    }


def _fail(state: RunState, stage: str, message: str, **details: object) -> None:
    state.stage_results.setdefault(stage, {}).update(status="failed", error=message)
    state.add_failure(stage, message, **details)


def _fail_state(state: RunState, stage: str, message: str, *, preserve_failure: bool = False) -> RunState:
    if not preserve_failure:
        _fail(state, stage, message)
    state.status = RunStatus.FAILED
    return state


__all__ = ["AssessmentAgent", "DockingAgent", "LigandAgent", "PocketAgent", "ReceptorAgent", "StructureAgent"]
