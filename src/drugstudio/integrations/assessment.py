"""Vina results aggregation with molecular descriptors and explicit limits."""

from __future__ import annotations

import csv
import io
import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from ..contracts import ArtifactRef
from ..ports import ToolContext, ToolResult
from ..storage import ArtifactStore
from .autodock_vina import _run
from .chemistry_environment import chemistry_command
from .tool_paths import external_runner


RDKIT_RUNNER = external_runner(
    "DOCK_AGENT_CHEMISTRY_RUNNER", ".agents/skills/rdkit/scripts/run_in_env.sh")


LIMITATIONS = [
    "Vina scores are empirical kcal/mol scores, not experimental affinity or evidence of activity.",
    "Each task keeps its receptor, ligand state and seed. Scores from different protocols are not pooled.",
    "Lipinski uses MW <= 500, cLogP <= 5, HBD <= 5 and HBA <= 10; all four must pass the reported flag.",
    "Veber uses strict rotatable bonds <= 10 and TPSA <= 140 Å².",
    "QED, PAINS and Brenk alerts are screening heuristics, not measured safety, toxicity or complete ADMET predictions.",
    "Descriptors describe the supplied microstate. Protonation populations and synthetic accessibility were not evaluated.",
    "Receptor preparation assertions do not validate target identity, pocket suitability or prediction confidence.",
]


@dataclass
class VinaDruglikenessAssessor:
    store: ArtifactStore
    chemistry_runner: Path = RDKIT_RUNNER
    timeout_seconds: int = 600

    def assess(self, artifacts: Iterable[ArtifactRef], context: ToolContext) -> ToolResult:
        items = list(artifacts)
        requested_attempt = int(context.configuration.get("docking_attempt", 1) or 1)
        docking_items = [item for item in items if item.kind == "docking_output"
                         and int(item.metadata.get("docking_attempt", 1) or 1) == requested_attempt]
        if not docking_items:
            return ToolResult(status="failed", error=f"no docking artifacts found for attempt {requested_attempt}")
        # Keep immutable artifacts from earlier human-review reruns in the
        # manifest, but never mix their poses into the current assessment.
        current_items = [item for item in items if item.kind != "docking_output"] + docking_items
        inputs = []
        for item in current_items:
            if item.kind == "prepared_ligand_sdf":
                ligand_id = item.metadata.get("ligand_id") or item.metadata.get("identifier")
                if not ligand_id:
                    return ToolResult(status="failed", error="prepared ligand is missing its identifier")
                inputs.append({"ligand_id": ligand_id, "artifact_id": item.artifact_id,
                               "path": str(self.store.path(item.path))})
        if not inputs or len({item["ligand_id"] for item in inputs}) != len(inputs):
            return ToolResult(status="failed", error="assessment needs one prepared SDF per ligand ID")
        log = context.workdir / "assessment.log"
        published = []
        try:
            request = context.workdir / "inputs.json"
            output = context.workdir / "properties.json"
            request.write_text(json.dumps(inputs, indent=2) + "\n", encoding="utf-8")
            summary_artifact = next(item for item in docking_items
                                    if item.metadata.get("relative_output") == "summary.json")
            summary = json.loads(self.store.path(summary_artifact.path).read_text(encoding="utf-8"))
            tasks = []
            for item in docking_items:
                if item.kind != "docking_output" or not str(item.metadata.get("relative_output", "")).endswith("/result.json"):
                    continue
                data = json.loads(self.store.path(item.path).read_text(encoding="utf-8"))
                passing = [pose for pose in data.get("poses", []) if pose.get("qc_pass") is True]
                if any(not math.isfinite(float(pose["affinity_kcal_mol"])) for pose in passing):
                    raise ValueError("non-finite QC-passing docking score")
                best = min(passing, key=lambda pose: float(pose["affinity_kcal_mol"])) if passing else None
                if data.get("status") == "complete" and best is None:
                    raise ValueError("completed docking task has no QC-passing pose")
                tasks.append({key: data.get(key) for key in ("task_id", "ligand_id", "state_id", "receptor_id", "seed", "status", "error")})
                tasks[-1].update(best_pose=best, pose_count=len(data.get("poses", [])),
                                 qc_pass_count=len(passing), artifact_id=item.artifact_id)
            if not tasks or len(tasks) != summary.get("tasks") or len({item["task_id"] for item in tasks}) != len(tasks):
                raise ValueError("docking summary and task records disagree")
            if {item["ligand_id"] for item in tasks} != {item["ligand_id"] for item in inputs}:
                raise ValueError("docking tasks and molecular assessments cover different ligands")
            references = [item for item in current_items if item.kind == "redock_reference_sdf"]
            redocking = {"status": "not_applicable", "reason": "no crystal reference ligand was supplied"}
            redocking_path = None
            if references:
                if len(references) != 1:
                    raise ValueError("redocking evaluation currently accepts one crystal reference per run")
                crystal_receptors = [item for item in current_items if item.kind in {"experimental_structure", "supplied_structure"}]
                prepared_receptors = [item for item in current_items if item.kind == "prepared_receptor_pdb"]
                if len(crystal_receptors) != 1 or len(prepared_receptors) != 1:
                    raise ValueError("redocking requires one crystal-frame receptor and its prepared PDB")
                result_artifacts = [item for item in docking_items
                                    if str(item.metadata.get("relative_output", "")).endswith("/result.json")]
                redocking_path = context.workdir / "redocking-inputs.json"
                redocking_request = {
                    "reference": {"ligand_id": references[0].metadata["ligand_id"],
                                  "path": str(self.store.path(references[0].path)),
                                  "artifact_id": references[0].artifact_id,
                                  "artifact_path": references[0].path, "sha256": references[0].sha256,
                                  "thresholds_A": references[0].metadata.get("thresholds_A", [2.0]),
                                  "top_k": references[0].metadata.get("top_k", [1, 3, 5])},
                    "reference_receptor": {"path": str(self.store.path(crystal_receptors[0].path)),
                                           "artifact_id": crystal_receptors[0].artifact_id,
                                           "artifact_path": crystal_receptors[0].path},
                    "prepared_receptor": {"path": str(self.store.path(prepared_receptors[0].path)),
                                          "artifact_id": prepared_receptors[0].artifact_id,
                                          "artifact_path": prepared_receptors[0].path},
                    "tasks": [],
                }
                for item in result_artifacts:
                    relative = str(item.metadata["relative_output"])
                    task_dir = Path(relative).parent
                    result_data = json.loads(self.store.path(item.path).read_text(encoding="utf-8"))
                    poses_artifact = next((candidate for candidate in docking_items
                                           if candidate.metadata.get("relative_output") == f"{task_dir}/poses.sdf"), None)
                    if poses_artifact is None:
                        raise ValueError(f"poses.sdf is missing for docking task {task_dir}")
                    redocking_request["tasks"].append({
                        "task_id": result_data.get("task_id"), "ligand_id": result_data.get("ligand_id"),
                        "state_id": result_data.get("state_id"), "seed": result_data.get("seed"),
                        "status": result_data.get("status"), "result_path": str(self.store.path(item.path)),
                        "poses_path": str(self.store.path(poses_artifact.path)),
                    })
                redocking_path.write_text(json.dumps(redocking_request, indent=2) + "\n", encoding="utf-8")
                redocking = {"status": "pending"}
            arguments = ["--inputs", str(request), "--output", str(output)]
            if redocking_path:
                arguments.extend(["--redocking-inputs", str(redocking_path)])
            _run(chemistry_command(Path(__file__).with_name("assessment_bridge.py"), arguments,
                                   context.workdir, self.chemistry_runner), cwd=context.workdir,
                 environment=context.environment, timeout=self.timeout_seconds, log_path=log)
            properties = json.loads(output.read_text(encoding="utf-8"))
            redocking = properties.get("redocking", redocking)
            report = {"engine": "vina", "aggregation": "lowest Vina score among QC-passing poses within each task",
                      "docking_attempt": requested_attempt,
                      "summary": summary, "tasks": tasks, **properties, "limitations": LIMITATIONS,
                      "structures": [{"artifact_id": item.artifact_id, "kind": item.kind, "metadata": item.metadata}
                                     for item in current_items if item.kind in {"experimental_structure", "predicted_structure", "supplied_structure"}]}
            suffix = "" if requested_attempt == 1 else f"-attempt-{requested_attempt}"
            parents = [item.artifact_id for item in current_items if item.kind in {
                "prepared_ligand_sdf", "docking_output", "redock_reference_sdf",
                "experimental_structure", "supplied_structure", "prepared_receptor_pdb"}]
            published.append(self.store.publish_json(relative_path=f"results/assessment/report{suffix}.json", value=report,
                                                     kind="assessment_data", parent_ids=parents))
            published.append(self.store.publish_bytes(relative_path=f"results/assessment/report{suffix}.md",
                                                      content=_markdown(report).encode("utf-8"),
                                                      kind="assessment_report", parent_ids=[published[0].artifact_id]))
            published.append(self.store.publish_bytes(relative_path=f"results/assessment/ligands{suffix}.csv",
                                                      content=_ligand_csv(properties["ligands"]).encode("utf-8"),
                                                      kind="assessment_table", parent_ids=[published[0].artifact_id]))
            if references:
                published.append(self.store.publish_bytes(
                    relative_path=f"results/assessment/redocking{suffix}.csv",
                    content=_redocking_csv(redocking).encode("utf-8"),
                    kind="redocking_table", parent_ids=[published[0].artifact_id]))
            failures = [item for item in properties["ligands"] if item["status"] != "succeeded"]
            redocking_failed = bool(references and redocking.get("status") != "succeeded")
            error = "some molecular assessments failed" if failures else (
                "redocking reference evaluation failed; see the report" if redocking_failed else None)
            return ToolResult(status="failed" if failures or redocking_failed else "succeeded", artifacts=published,
                              metadata=report, error=error)
        except Exception as exc:
            if log.is_file():
                published.append(self.store.publish_bytes(relative_path="results/assessment/assessment.log",
                                                         content=log.read_bytes(), kind="assessment_log"))
            return ToolResult(status="failed", artifacts=published, error=f"{type(exc).__name__}: {exc}")


def _markdown(report: dict) -> str:
    lines = ["# Docking and molecular assessment", "", f"RDKit: {report['rdkit_version']}", "",
             "Structure origins: " + ", ".join(item["kind"] for item in report["structures"]), "",
             f"Docking summary: `{json.dumps(report['summary'], sort_keys=True)}`", "",
             "Best QC-passing pose per task (no ranking across different protocols):", "",
             "| Task | Ligand state | Receptor | Seed | Vina kcal/mol | Status |",
             "|---|---|---|---|---|---|"]
    for task in report["tasks"]:
        score = (task["best_pose"] or {}).get("affinity_kcal_mol", "unavailable")
        lines.append(f"| {task['task_id']} | {task['state_id']} | {task['receptor_id']} | {task['seed']} | {score} | {task['status']} |")
    lines.extend(["", "## Molecular properties", "", "| Ligand | MW | cLogP | TPSA | QED | Lipinski violations | Veber | PAINS / Brenk |",
                  "|---|---|---|---|---|---|---|---|"])
    for ligand in report["ligands"]:
        props = ligand.get("properties")
        if not props:
            lines.append(f"| {ligand['ligand_id']} | unavailable | | | | assessment failed; see JSON | | |")
            continue
        lines.append(f"| {ligand['ligand_id']} | {props['mw']:.2f} | {props['logp']:.2f} | {props['tpsa']:.2f} | "
                     f"{props['qed']:.3f} | {len(props['lipinski_violations'])} | {props['veber_pass']} | "
                     f"{len(props['alerts']['pains'])} / {len(props['alerts']['brenk'])} |")
    redocking = report.get("redocking", {"status": "not_applicable", "reason": "no crystal reference ligand was supplied"})
    lines.extend(["", "## Redocking reference evaluation", "",
                  f"Status: **{redocking.get('status', 'unknown')}**."])
    if redocking.get("status") == "succeeded":
        lines.extend(["", "| Task | Top-1 score pose RMSD (Å) | Best RMSD (Å) | Top-k success |",
                      "|---|---:|---:|---|"])
        for task in redocking.get("tasks", []):
            if task.get("redocking_status") != "succeeded":
                lines.append(f"| {task.get('task_id')} | unavailable | unavailable | {task.get('reason', task.get('redocking_status'))} |")
                continue
            top1 = task["top_scoring_pose"]["symmetry_corrected_heavy_rmsd_A"]
            best = task["best_rmsd_pose"]["symmetry_corrected_heavy_rmsd_A"]
            hits = task.get("top_k", {})
            summary = ", ".join(f"Top-{k}: " + ", ".join(
                f"{name.removeprefix('success_at_')}: {'pass' if passed else 'fail'}"
                for name, passed in values.items() if name.startswith("success_at_"))
                for k, values in hits.items())
            lines.append(f"| {task.get('task_id')} | {top1:.3f} | {best:.3f} | {summary} |")
        lines.extend(["", redocking.get("method", ""), "",
                      "Ligands are not independently aligned; the reference and prepared receptor coordinate frames were checked first."])
        lines.extend(["", "Top-k success rates include failed applicable tasks in the denominator:", "",
                      "| Ranking depth | RMSD threshold | successes / tasks | Rate |",
                      "|---:|---:|---:|---:|"])
        for k, metrics in redocking.get("aggregates", {}).items():
            for threshold, values in metrics.items():
                lines.append(f"| {k} | {threshold.removeprefix('success_at_')} | "
                             f"{values['successes']} / {values['denominator_tasks']} | "
                             f"{values['rate'] if values['rate'] is not None else 'unavailable'} |")
    elif redocking.get("reason"):
        lines.extend(["", redocking["reason"]])
    lines.extend(["", "## Interpretation limits", ""] + ["- " + text for text in report["limitations"]])
    return "\n".join(lines) + "\n"


def _ligand_csv(ligands: list) -> str:
    buffer = io.StringIO(newline="")
    rows = []
    for item in ligands:
        row = {key: value for key, value in item.items() if key != "properties"}
        row.update(item.get("properties", {}))
        rows.append({key: json.dumps(value) if isinstance(value, (dict, list)) else value for key, value in row.items()})
    fields = list(dict.fromkeys(key for row in rows for key in row))
    writer = csv.DictWriter(buffer, fieldnames=fields)
    writer.writeheader()
    writer.writerows(rows)
    return buffer.getvalue()


def _redocking_csv(redocking: dict) -> str:
    fields = ["task_id", "ligand_id", "state_id", "seed", "redocking_status", "mode",
              "affinity_kcal_mol", "qc_pass", "symmetry_corrected_heavy_rmsd_A",
              "symmetry_mapping_count", "success_thresholds_A", "reason"]
    buffer = io.StringIO(newline="")
    writer = csv.DictWriter(buffer, fieldnames=fields)
    writer.writeheader()
    for task in redocking.get("tasks", []):
        poses = task.get("poses", [])
        if not poses:
            writer.writerow({key: task.get(key) for key in fields if key != "mode"})
            continue
        for pose in poses:
            row = {key: task.get(key) for key in fields}
            row.update(pose)
            row["success_thresholds_A"] = json.dumps(pose.get("success_thresholds_A", {}), sort_keys=True)
            writer.writerow(row)
    return buffer.getvalue()
