"""Initial 3D conformer generation independent of optional xTB optimization."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from ..contracts import LigandSpec
from ..ports import ToolContext, ToolResult
from ..storage import ArtifactStore
from .autodock_vina import _run
from .chemistry_environment import chemistry_command
from .tool_paths import external_runner


RDKIT_RUNNER = external_runner(
    "DOCK_AGENT_CHEMISTRY_RUNNER", ".agents/skills/rdkit/scripts/run_in_env.sh")


@dataclass
class RDKitConformerPreparer:
    store: ArtifactStore
    runner: Path = RDKIT_RUNNER
    seed: int = 20261016

    def prepare(self, ligand: LigandSpec, context: ToolContext) -> ToolResult:
        stage = context.workdir / "rdkit"
        if stage.exists():
            return ToolResult(status="failed", error="RDKit work directory already exists; use a new run")
        stage.mkdir(parents=True, exist_ok=False)
        output, metadata_path, log = stage / "initial.sdf", stage / "metadata.json", stage / "prepare.log"
        try:
            command = chemistry_command(Path(__file__).with_name("rdkit_bridge.py"),
                                        ["initial", "--output", str(output), "--metadata", str(metadata_path),
                                         "--seed", str(self.seed)], stage, self.runner)
            if ligand.input_path:
                command.extend(["--sdf", str(Path(ligand.input_path).expanduser().resolve())])
            elif ligand.smiles:
                command.extend(["--smiles", ligand.smiles])
            else:
                raise ValueError("provide an SDF or SMILES for the ligand")
            _run(command, cwd=stage, environment=context.environment, timeout=300, log_path=log)
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
            artifact = self.store.publish_bytes(relative_path=f"results/conformer_initial_sdf/{ligand.ligand_id}/initial.sdf",
                                                content=output.read_bytes(), kind="conformer_initial_sdf",
                                                metadata={"ligand_id": ligand.ligand_id, **metadata})
            return ToolResult(status="succeeded", artifacts=[artifact], metadata=metadata)
        except Exception as exc:
            artifacts = []
            if log.is_file():
                artifacts.append(self.store.publish_bytes(relative_path=f"results/rdkit_logs/{ligand.ligand_id}/prepare.log",
                                                         content=log.read_bytes(), kind="ligand_prepare_log"))
            return ToolResult(status="failed", artifacts=artifacts, error=f"{type(exc).__name__}: {exc}")
