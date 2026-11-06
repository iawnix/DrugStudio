"""xTB geometry optimization adapter for prepared ligand states."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Optional

from ..contracts import ArtifactRef, LigandSpec
from ..ports import LigandPreparer, ToolContext, ToolResult
from ..storage import ArtifactStore
from .chemistry_environment import chemistry_command
from .tool_paths import external_executable


XTB_EXECUTABLE = external_executable(
    "DOCK_AGENT_XTB", ("xtb",), ("xtb/current/bin/xtb", "xtb/6.7.1/bin/xtb",
                                  "/home/agent/soft/xtb/current/bin/xtb",
                                  "/home/agent/soft/xtb/6.7.1/bin/xtb"))


@dataclass
class XTBConformerPreparer:
    store: ArtifactStore
    xtb: Path = XTB_EXECUTABLE
    python_executable: Optional[Path] = None
    seed: int = 20261016
    opt_level: str = "tight"
    uhf: int = 0
    solvent: Optional[str] = "water"
    timeout_seconds: int = 3600

    def __post_init__(self) -> None:
        configured_xtb = str(self.xtb)
        resolved_xtb = shutil.which(configured_xtb) if not self.xtb.is_absolute() else None
        self.xtb = Path(resolved_xtb or configured_xtb).expanduser().resolve()
        if self.python_executable is not None:
            self.python_executable = self.python_executable.resolve()
        self.bridge = Path(__file__).resolve().parent / "rdkit_bridge.py"

    def prepare(self, ligand: LigandSpec, context: ToolContext) -> ToolResult:
        if not ligand.input_path and not ligand.smiles:
            return ToolResult(status="unsupported", error="xTB preparation requires LigandSpec.input_path or smiles")
        stage = context.workdir / "xtb"
        if stage.exists():
            return ToolResult(status="failed", error="xTB work directory already exists; preserve it and use a new run")
        stage.mkdir(parents=True, exist_ok=False)
        initial_sdf = stage / "initial.sdf"
        metadata_path = stage / "initial.metadata.json"
        initial_xyz = stage / "initial.xyz"
        optimized_xyz = stage / "xtbopt.xyz"
        optimized_sdf = stage / "optimized.sdf"
        stdout_path = stage / "xtb.out"
        stderr_path = stage / "xtb.err"
        bridge_initial_stdout = stage / "bridge-initial.out"
        bridge_initial_stderr = stage / "bridge-initial.err"
        bridge_optimized_stdout = stage / "bridge-optimized.out"
        bridge_optimized_stderr = stage / "bridge-optimized.err"
        try:
            initial_command = self._bridge_command(["initial", "--output", str(initial_sdf), "--metadata", str(metadata_path), "--seed", str(self.seed)], stage)
            if ligand.input_path:
                initial_command.extend(["--sdf", str(Path(ligand.input_path).expanduser().resolve())])
            else:
                initial_command.extend(["--smiles", str(ligand.smiles)])
            _run(initial_command, cwd=stage, environment=context.environment, timeout=300,
                 stdout_path=bridge_initial_stdout, stderr_path=bridge_initial_stderr)
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
            electrons = metadata["total_nuclear_charge"] - metadata["formal_charge"]
            if self.uhf < 0 or self.uhf > electrons or (electrons - self.uhf) % 2:
                raise ValueError("xTB electron count and requested unpaired electrons are inconsistent")
            command = [str(self.xtb), str(initial_xyz), "--gfn", "2", "--opt", self.opt_level,
                       "--chrg", str(metadata["formal_charge"]), "--uhf", str(self.uhf)]
            if self.solvent:
                command.extend(["--alpb", self.solvent])
            result = _run(command, cwd=stage, environment=context.environment, timeout=self.timeout_seconds)
            if (not optimized_xyz.is_file() or "normal termination" not in result.lower()
                    or "GEOMETRY OPTIMIZATION CONVERGED" not in result.upper()
                    or (stage / "NOT_CONVERGED").exists()):
                raise RuntimeError("xTB did not produce a converged, normally terminated geometry optimization")
            _run(self._bridge_command(["optimized", "--reference", str(initial_sdf),
                                      "--xyz", str(optimized_xyz), "--output", str(optimized_sdf)], stage), cwd=stage,
                 environment=context.environment, timeout=300, stdout_path=bridge_optimized_stdout,
                 stderr_path=bridge_optimized_stderr)
            artifacts = [
                _publish(self.store, optimized_sdf, "conformer_optimized_sdf", ligand.ligand_id,
                         extra={"ligand_id": ligand.ligand_id, "formal_charge": metadata["formal_charge"]}),
                _publish(self.store, initial_sdf, "conformer_initial_sdf", ligand.ligand_id),
                _publish(self.store, metadata_path, "conformer_metadata", ligand.ligand_id),
                _publish(self.store, optimized_xyz, "conformer_optimized_xyz", ligand.ligand_id),
                _publish(self.store, stdout_path, "xtb_stdout", ligand.ligand_id),
                _publish(self.store, stderr_path, "xtb_stderr", ligand.ligand_id),
                _publish(self.store, bridge_initial_stdout, "rdkit_bridge_stdout", ligand.ligand_id,
                         extra={"stage": "initial"}),
                _publish(self.store, bridge_initial_stderr, "rdkit_bridge_stderr", ligand.ligand_id,
                         extra={"stage": "initial"}),
                _publish(self.store, bridge_optimized_stdout, "rdkit_bridge_stdout", ligand.ligand_id,
                         extra={"stage": "optimized"}),
                _publish(self.store, bridge_optimized_stderr, "rdkit_bridge_stderr", ligand.ligand_id,
                         extra={"stage": "optimized"}),
            ]
            return ToolResult(status="succeeded", artifacts=artifacts, metadata={"xtb_command": command, **metadata})
        except (OSError, ValueError, json.JSONDecodeError, subprocess.SubprocessError, RuntimeError) as exc:
            artifacts = []
            for path, kind in ((stdout_path, "xtb_stdout"), (stderr_path, "xtb_stderr"),
                               (bridge_initial_stdout, "rdkit_bridge_stdout"),
                               (bridge_initial_stderr, "rdkit_bridge_stderr"),
                               (bridge_optimized_stdout, "rdkit_bridge_stdout"),
                               (bridge_optimized_stderr, "rdkit_bridge_stderr"),
                               (optimized_xyz, "conformer_optimized_xyz")):
                if path.is_file():
                    artifacts.append(_publish(self.store, path, kind, ligand.ligand_id,
                                              extra={"source_name": path.name}))
            return ToolResult(status="failed", artifacts=artifacts, error=str(exc))

    def _bridge_command(self, arguments: list[str], cwd: Path) -> list[str]:
        if self.python_executable is not None:
            return [str(self.python_executable), str(self.bridge), *arguments]
        return chemistry_command(self.bridge, arguments, cwd)


def _run(command: list[str], *, cwd: Path, environment: Dict[str, str], timeout: int,
         stdout_path: Optional[Path] = None, stderr_path: Optional[Path] = None) -> str:
    env = os.environ.copy()
    env.update(environment)
    temporary = cwd / "tmp"
    temporary.mkdir(parents=True, exist_ok=True)
    env["TMPDIR"] = str(temporary.resolve())
    env["OMP_NUM_THREADS"] = "1"
    env["MKL_NUM_THREADS"] = "1"
    try:
        completed = subprocess.run(command, cwd=cwd, env=env, capture_output=True, text=True, timeout=timeout, check=False)
    except subprocess.TimeoutExpired as exc:
        for output_path, value in ((stdout_path or cwd / "xtb.out", exc.stdout), (stderr_path or cwd / "xtb.err", exc.stderr)):
            content = value.decode(errors="replace") if isinstance(value, bytes) else (value or "")
            output_path.write_text(content + "\nProcess timed out.\n", encoding="utf-8")
        raise
    stdout = completed.stdout or ""
    stderr = completed.stderr or ""
    (stdout_path or (cwd / "xtb.out")).write_text(stdout, encoding="utf-8")
    (stderr_path or (cwd / "xtb.err")).write_text(stderr, encoding="utf-8")
    if completed.returncode != 0:
        raise subprocess.CalledProcessError(completed.returncode, command, output=stdout, stderr=stderr)
    return stdout + stderr


def _publish(store: ArtifactStore, source: Path, kind: str, identifier: str, *, extra: Optional[dict] = None) -> ArtifactRef:
    return store.publish_bytes(relative_path=f"results/{kind}/{identifier}/{source.name}", content=source.read_bytes(), kind=kind,
                               metadata={"source_work_path": source.resolve().relative_to(store.root).as_posix(), "identifier": identifier, **(extra or {})})


__all__ = ["XTBConformerPreparer"]
