"""Adapters for the repository's validated Meeko/AutoDock Vina workflow."""

from __future__ import annotations

import json
import math
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from ..contracts import ArtifactRef, DockingTask, LigandSpec, TargetSpec
from ..ports import DockingEngine, LigandPreparer, ReceptorPreparer, ToolContext, ToolResult
from ..storage import ArtifactStore
from .tool_paths import external_runner


VINA_RUNNER = external_runner(
    "DOCK_AGENT_VINA_RUNNER", "skill-development/autodock-vina/scripts/run_in_env.sh")


@dataclass
class AutoDockVinaLigandPreparer:
    store: ArtifactStore
    runner: Path = VINA_RUNNER
    timeout_seconds: int = 900

    def __post_init__(self) -> None:
        self.runner = self.runner.resolve()

    def prepare(self, ligand: LigandSpec, context: ToolContext) -> ToolResult:
        if not ligand.input_path:
            return ToolResult(
                status="unsupported",
                error="AutoDock Vina preparation requires an explicit 3D SDF input_path; SMILES preparation belongs to a separate chemistry adapter",
            )
        source = Path(ligand.input_path).expanduser().resolve()
        if not source.is_file():
            return ToolResult(status="failed", error=f"ligand input does not exist: {source}")
        output = context.workdir / "prepared"
        log_path = context.workdir / "prepare.log"
        try:
            _run(
                ["bash", str(self.runner), "vina_workflow.py", "prepare-ligand", "--sdf", str(source), "--out", str(output)],
                cwd=context.workdir,
                environment=context.environment,
                timeout=self.timeout_seconds,
                log_path=log_path,
            )
            metadata_path = output / "metadata.json"
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
            metadata["ligand_id"] = ligand.ligand_id
            metadata["parent_id"] = ligand.parent_id
            metadata_path.write_text(json.dumps(metadata, indent=2) + "\n", encoding="utf-8")
            artifacts = [
                _publish(self.store, output / "ligand.pdbqt", "prepared_ligand_pdbqt", ligand.ligand_id,
                         extra={"ligand_id": ligand.ligand_id, "metadata_relpath": f"results/ligand_metadata/{ligand.ligand_id}/metadata.json"}),
                _publish(self.store, output / "source.sdf", "prepared_ligand_sdf", ligand.ligand_id),
                _publish(self.store, metadata_path, "ligand_metadata", ligand.ligand_id),
            ]
            return ToolResult(status="succeeded", artifacts=artifacts, metadata=metadata)
        except (OSError, ValueError, json.JSONDecodeError, subprocess.SubprocessError) as exc:
            artifacts = [_publish_if_present(self.store, log_path, "ligand_prepare_log", ligand.ligand_id)]
            return ToolResult(status="failed", artifacts=[item for item in artifacts if item], error=str(exc))


@dataclass
class AutoDockVinaReceptorPreparer:
    store: ArtifactStore
    runner: Path = VINA_RUNNER
    timeout_seconds: int = 900

    def __post_init__(self) -> None:
        self.runner = self.runner.resolve()

    def prepare(self, structure: ArtifactRef, target: TargetSpec, context: ToolContext) -> ToolResult:
        if structure.metadata.get("chemically_prepared") is not True:
            return ToolResult(status="waiting_input", error=(
                "Chemical receptor preparation is required before Meeko conversion. "
                "Supply a complete, protonated receptor through --prepared-receptor; "
                "downloaded coordinates alone are not docking-ready."
            ))
        source = self.store.path(structure.path)
        if not source.is_file():
            return ToolResult(status="failed", error=f"structure artifact does not exist: {source}")
        output = context.workdir / "prepared"
        log_path = context.workdir / "prepare.log"
        try:
            _run(
                ["bash", str(self.runner), "vina_workflow.py", "prepare-receptor", "--pdb", str(source), "--out", str(output)],
                cwd=context.workdir,
                environment=context.environment,
                timeout=self.timeout_seconds,
                log_path=log_path,
            )
            metadata_path = output / "metadata.json"
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
            metadata["target_id"] = target.target_id
            metadata["structure_artifact_id"] = structure.artifact_id
            metadata_path.write_text(json.dumps(metadata, indent=2) + "\n", encoding="utf-8")
            artifacts = [
                _publish(self.store, output / "prepared.pdb", "prepared_receptor_pdb", target.target_id),
                _publish(self.store, output / "receptor.pdbqt", "prepared_receptor_pdbqt", target.target_id,
                         extra={"target_id": target.target_id, "metadata_relpath": f"results/receptor_metadata/{target.target_id}/metadata.json"}),
                _publish(self.store, metadata_path, "receptor_metadata", target.target_id),
            ]
            return ToolResult(status="succeeded", artifacts=artifacts, metadata=metadata)
        except (OSError, ValueError, json.JSONDecodeError, subprocess.SubprocessError) as exc:
            artifacts = [_publish_if_present(self.store, log_path, "receptor_prepare_log", target.target_id)]
            return ToolResult(status="failed", artifacts=[item for item in artifacts if item], error=str(exc))


@dataclass
class AutoDockVinaEngine:
    store: ArtifactStore
    center: Optional[tuple[float, float, float]] = None
    size: Optional[tuple[float, float, float]] = None
    runner: Path = VINA_RUNNER
    workers: int = 1
    allocated_cpus: int = 1
    exhaustiveness: int = 8
    num_modes: int = 9
    min_rmsd: float = 1.0
    energy_range: float = 3.0
    seed: int = 20261016
    timeout_seconds: int = 7200
    name: str = "vina"

    def __post_init__(self) -> None:
        self.runner = self.runner.resolve()
        if (self.center is None) != (self.size is None):
            raise ValueError("center and size must either both be supplied or both be omitted")
        if self.center is not None and self.size is not None:
            for name, values in (("center", self.center), ("size", self.size)):
                if len(values) != 3 or not all(math.isfinite(float(v)) for v in values):
                    raise ValueError(f"{name} must contain three finite coordinates")
            if any(float(value) <= 0 for value in self.size):
                raise ValueError("box sizes must be positive full side lengths in Å")
        if not 1 <= self.workers <= self.allocated_cpus:
            raise ValueError("workers must be positive and fit allocated_cpus")
        if self.exhaustiveness < 1 or self.num_modes < 1 or self.timeout_seconds < 1 or self.seed < 1:
            raise ValueError("exhaustiveness, num_modes, timeout and seed must be positive")

    def plan(self, receptor: ArtifactRef, ligands: Iterable[ArtifactRef], context: ToolContext) -> List[DockingTask]:
        if self.center is None or self.size is None:
            raise ValueError("docking box is unresolved; supply a box or run pocket identification first")
        receptor_metadata = receptor.metadata.get("metadata_relpath")
        if not receptor_metadata:
            raise ValueError("prepared receptor artifact is missing metadata_relpath")
        receptor_id = str(receptor.metadata.get("target_id", "receptor"))
        tasks: List[DockingTask] = []
        for index, ligand in enumerate(ligands):
            ligand_metadata = ligand.metadata.get("metadata_relpath")
            if not ligand_metadata:
                raise ValueError(f"prepared ligand artifact is missing metadata_relpath: {ligand.artifact_id}")
            ligand_id = str(ligand.metadata.get("ligand_id", f"ligand-{index + 1}"))
            task_id = f"{ligand_id}-{receptor_id}-{index + 1}"
            tasks.append(
                DockingTask(
                    task_id=task_id,
                    ligand_id=ligand_id,
                    ligand_state_id=ligand_id,
                    receptor_id=receptor_id,
                    engine=self.name,
                    input_artifact_ids=[receptor.artifact_id, ligand.artifact_id],
                    metadata={
                        "ligand_metadata": str(self.store.path(str(ligand_metadata))),
                        "receptor_metadata": str(self.store.path(str(receptor_metadata))),
                        "center": list(self.center),
                        "size": list(self.size),
                        "seed": self.seed + index,
                        "cpu": 1,
                        "exhaustiveness": self.exhaustiveness,
                        "num_modes": self.num_modes,
                        "min_rmsd": self.min_rmsd,
                        "energy_range": self.energy_range,
                        "box_tolerance_A": 1.0,
                        "timeout_seconds": self.timeout_seconds,
                    },
                )
            )
        return tasks

    def run(self, tasks: Iterable[DockingTask], context: ToolContext) -> ToolResult:
        task_list = list(tasks)
        task_manifest = context.workdir / "tasks.json"
        output = context.workdir / "results"
        log_path = context.workdir / "vina-batch.log"
        payload = [{"task_id": task.task_id, "ligand_id": task.ligand_id, "state_id": task.ligand_state_id,
                    "receptor_id": task.receptor_id, **task.metadata} for task in task_list]
        task_manifest.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        try:
            stdout = _run(
                ["bash", str(self.runner), "vina_workflow.py", "batch", "--tasks", str(task_manifest), "--out", str(output),
                 "--workers", str(self.workers), "--allocated-cpus", str(self.allocated_cpus)],
                cwd=context.workdir,
                environment=context.environment,
                timeout=self.timeout_seconds * max(1, len(task_list)),
                log_path=log_path,
            )
            summary = json.loads((output / "summary.json").read_text(encoding="utf-8"))
            attempt = int(context.configuration.get("docking_attempt", 1) or 1)
            result_prefix = "docking_output" if attempt == 1 else f"docking_output-attempt-{attempt}"
            artifacts = _publish_tree(self.store, output, "docking_output",
                                      destination_prefix=result_prefix,
                                      metadata_extra={"docking_attempt": attempt})
            if (not all(key in summary for key in ("tasks", "completed", "failed"))
                    or summary["tasks"] != len(task_list)
                    or summary["completed"] + summary["failed"] != len(task_list)):
                raise ValueError("Vina summary does not cover every planned task")
            task_results = []
            for task in task_list:
                record = json.loads((output / task.task_id / "result.json").read_text(encoding="utf-8"))
                task.status = "succeeded" if record.get("status") == "complete" else "failed"
                task.score = record.get("best_pose", {}).get("affinity_kcal_mol")
                task.error = record.get("error")
                task.output_artifact_ids = [item.artifact_id for item in artifacts
                                           if item.metadata["relative_output"].startswith(task.task_id + "/")]
                task_results.append({"task_id": task.task_id, "ligand_id": task.ligand_id,
                                     "status": task.status, "score_kcal_mol": task.score, "error": task.error})
            status = "succeeded" if summary.get("failed") == 0 and all(task.status == "succeeded" for task in task_list) else "failed"
            error = None if status == "succeeded" else "one or more docking tasks failed; see task results"
            return ToolResult(status=status, artifacts=artifacts,
                              metadata={"summary": summary, "task_results": task_results,
                                        "attempt": attempt, "result_prefix": result_prefix}, error=error)
        except (OSError, ValueError, json.JSONDecodeError, subprocess.SubprocessError) as exc:
            attempt = int(context.configuration.get("docking_attempt", 1) or 1)
            result_prefix = "docking_output" if attempt == 1 else f"docking_output-attempt-{attempt}"
            artifacts = (_publish_tree(self.store, output, "docking_output",
                                       destination_prefix=result_prefix,
                                       metadata_extra={"docking_attempt": attempt})
                         if output.exists() else [])
            artifacts.append(_publish_if_present(self.store, log_path, "docking_log", "batch"))
            return ToolResult(status="failed", artifacts=[item for item in artifacts if item], error=str(exc))


def _run(command: List[str], *, cwd: Path, environment: Dict[str, str], timeout: int, log_path: Path) -> str:
    env = os.environ.copy()
    env.update(environment)
    temporary = cwd / "tmp"
    temporary.mkdir(parents=True, exist_ok=True)
    env["TMPDIR"] = str(temporary.resolve())
    for variable, relative in (("XDG_CACHE_HOME", "cache"), ("MPLCONFIGDIR", "cache/matplotlib"),
                               ("XDG_CONFIG_HOME", "config"), ("XDG_DATA_HOME", "data")):
        directory = cwd / relative
        directory.mkdir(parents=True, exist_ok=True)
        env[variable] = str(directory.resolve())
    log_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        completed = subprocess.run(command, cwd=cwd, env=env, text=True, capture_output=True,
                                   timeout=timeout, check=False)
    except subprocess.TimeoutExpired as exc:
        chunks = [chunk.decode(errors="replace") if isinstance(chunk, bytes) else (chunk or "")
                  for chunk in (exc.stdout, exc.stderr)]
        log_path.write_text("".join(chunks) + "\nProcess timed out.\n", encoding="utf-8")
        raise
    output = (completed.stdout or "") + (completed.stderr or "")
    log_path.write_text(output, encoding="utf-8")
    if completed.returncode != 0:
        raise subprocess.CalledProcessError(completed.returncode, command, output=output)
    return output


def _publish(store: ArtifactStore, source: Path, kind: str, identifier: str, *, extra: Optional[Dict[str, Any]] = None) -> ArtifactRef:
    metadata = {"source_work_path": _relative(store, source), "identifier": identifier, **(extra or {})}
    return store.publish_bytes(relative_path=f"results/{kind}/{identifier}/{source.name}", content=source.read_bytes(), kind=kind, metadata=metadata)


def _publish_if_present(store: ArtifactStore, source: Path, kind: str, identifier: str) -> Optional[ArtifactRef]:
    return _publish(store, source, kind, identifier) if source.is_file() else None


def _publish_tree(store: ArtifactStore, source_root: Path, kind: str, *,
                  destination_prefix: Optional[str] = None,
                  metadata_extra: Optional[Dict[str, Any]] = None) -> List[ArtifactRef]:
    artifacts: List[ArtifactRef] = []
    if not source_root.is_dir():
        return artifacts
    for source in sorted(path for path in source_root.rglob("*") if path.is_file()):
        relative = source.relative_to(source_root).as_posix()
        destination = destination_prefix or kind
        metadata = {"source_work_path": _relative(store, source), "relative_output": relative,
                    **(metadata_extra or {})}
        artifacts.append(store.publish_bytes(relative_path=f"results/{destination}/{relative}", content=source.read_bytes(), kind=kind,
                                              metadata=metadata))
    return artifacts


def _relative(store: ArtifactStore, path: Path) -> str:
    try:
        return path.resolve().relative_to(store.root).as_posix()
    except ValueError:
        return str(path.resolve())


__all__ = ["AutoDockVinaEngine", "AutoDockVinaLigandPreparer", "AutoDockVinaReceptorPreparer"]
