"""Composable receptor repair and docking-format preparation."""

from __future__ import annotations

import json
import hashlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..contracts import ArtifactRef, TargetSpec
from ..ports import ReceptorPreparer, ToolContext, ToolResult
from ..storage import ArtifactStore
from .autodock_vina import _run
from .chemistry_environment import chemistry_command


@dataclass(frozen=True)
class ReceptorPreparationPolicy:
    chains: tuple[str, ...] = ()
    ph: float = 7.4
    replace_nonstandard_residues: bool = True
    add_missing_atoms: bool = True
    add_missing_residues: bool = False
    remove_heterogens: bool = True
    keep_residues: tuple[str, ...] = ()
    keep_waters: bool = False
    binding_site_residues: tuple[str, ...] = ()
    require_approval_for_binding_site_repairs: bool = True
    require_approval_for_nonstandard_residues: bool = True
    require_approval_for_retained_heterogens: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "chains": list(self.chains), "ph": self.ph,
            "replace_nonstandard_residues": self.replace_nonstandard_residues,
            "add_missing_atoms": self.add_missing_atoms,
            "add_missing_residues": self.add_missing_residues,
            "remove_heterogens": self.remove_heterogens,
            "keep_residues": list(self.keep_residues), "keep_waters": self.keep_waters,
            "binding_site_residues": list(self.binding_site_residues),
            "require_approval_for_binding_site_repairs": self.require_approval_for_binding_site_repairs,
            "require_approval_for_nonstandard_residues": self.require_approval_for_nonstandard_residues,
            "require_approval_for_retained_heterogens": self.require_approval_for_retained_heterogens,
        }


@dataclass
class PDBFixerReceptorPreparer:
    store: ArtifactStore
    policy: ReceptorPreparationPolicy = field(default_factory=ReceptorPreparationPolicy)
    runner: Path = Path(__file__).resolve().parents[1] / "scripts/run_receptor_env.sh"
    timeout_seconds: int = 900

    def __post_init__(self) -> None:
        self.runner = self.runner.resolve()

    def prepare(self, structure: ArtifactRef, target: TargetSpec, context: ToolContext) -> ToolResult:
        if structure.metadata.get("chemically_prepared") is True and context.configuration.get("force_repair") is not True:
            return ToolResult(status="succeeded", artifacts=[structure], metadata={"backend": "supplied_prepared_receptor", "skipped": True})
        source = self.store.path(structure.path)
        if not source.is_file():
            return ToolResult(status="failed", error=f"structure artifact does not exist: {source}")
        workdir = context.workdir
        workdir.mkdir(parents=True, exist_ok=True)
        policy = self.policy.to_dict()
        if not policy["binding_site_residues"] and target.binding_site_residues:
            policy["binding_site_residues"] = list(target.binding_site_residues)
        policy_path = workdir / "policy.json"
        repaired = workdir / "repaired_receptor.pdb"
        metadata_path = workdir / "receptor_repair.json"
        log_path = workdir / "pdbfixer.log"
        receipt_path = workdir / "repair_receipt.json"
        worker = Path(__file__).with_name("pdbfixer_bridge.py")
        request_hash = hashlib.sha256(json.dumps({
            "input_sha256": hashlib.sha256(source.read_bytes()).hexdigest(), "policy": policy,
            "worker_sha256": hashlib.sha256(worker.read_bytes()).hexdigest(),
        }, sort_keys=True).encode()).hexdigest()
        reuse_existing = repaired.is_file() and metadata_path.is_file()
        if not reuse_existing and any(workdir.iterdir()):
            return ToolResult(status="failed", error=f"receptor preparation work directory is not empty: {workdir}")
        try:
            if reuse_existing:
                receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
                if (receipt.get("request_sha256") != request_hash
                        or receipt.get("output_sha256") != hashlib.sha256(repaired.read_bytes()).hexdigest()
                        or receipt.get("metadata_sha256") != hashlib.sha256(metadata_path.read_bytes()).hexdigest()
                        or json.loads(policy_path.read_text()) != policy):
                    raise ValueError("receptor repair reuse rejected: input, policy, worker or output changed; use a new run")
            if not reuse_existing:
                policy_path.write_text(json.dumps(policy, indent=2) + "\n", encoding="utf-8")
                command = chemistry_command(Path(__file__).with_name("pdbfixer_bridge.py"),
                                            ["--input", str(source), "--output", str(repaired),
                                             "--metadata", str(metadata_path), "--policy", str(policy_path)],
                                            workdir, self.runner)
                _run(command, cwd=workdir, environment=context.environment, timeout=self.timeout_seconds, log_path=log_path)
                receipt_path.write_text(json.dumps({"request_sha256": request_hash,
                    "output_sha256": hashlib.sha256(repaired.read_bytes()).hexdigest(),
                    "metadata_sha256": hashlib.sha256(metadata_path.read_bytes()).hexdigest()}, indent=2) + "\n")
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
            review_reasons = []
            if metadata.get("binding_site_requires_review") and policy.get("require_approval_for_binding_site_repairs", True):
                review_reasons.append("sequence gaps require review: internal gap, unassessed site or observed flank within 6 Å; missing-residue positions are unknown")
            if metadata.get("nonstandard_residues") and policy.get("require_approval_for_nonstandard_residues", True):
                review_reasons.append("nonstandard residues were detected; inspect the replacement inventory")
            if metadata.get("retained_heterogen_residues") and policy.get("require_approval_for_retained_heterogens", True):
                review_reasons.append("named heterogens or waters were retained")
            if review_reasons and context.configuration.get("approve_receptor") is not True:
                repaired_artifact, metadata_artifact, log_artifact = self._publish_outputs(
                    target, repaired, metadata_path, log_path, metadata, structure.artifact_id)
                return ToolResult(status="waiting_approval", artifacts=[repaired_artifact, metadata_artifact, log_artifact],
                                  metadata=metadata, error="; ".join(review_reasons) + "; inspect receptor_repair.json and approve or revise the receptor policy")
            repaired_artifact, metadata_artifact, log_artifact = self._publish_outputs(
                target, repaired, metadata_path, log_path, metadata, structure.artifact_id)
            return ToolResult(status="succeeded", artifacts=[repaired_artifact, metadata_artifact, log_artifact], metadata=metadata)
        except Exception as exc:
            artifacts = []
            log_text = log_path.read_text(encoding="utf-8", errors="replace") if log_path.is_file() else ""
            if log_path.is_file():
                artifacts.append(self.store.publish_bytes(relative_path=f"results/receptor_repair/{target.target_id}/pdbfixer.log",
                                                         content=log_path.read_bytes(), kind="receptor_repair_log"))
            if "PDBFixer/OpenMM is not installed" in log_text or not self.runner.is_file():
                return ToolResult(status="waiting_input", artifacts=artifacts,
                                  error="PDBFixer/OpenMM is unavailable; install the managed receptor backend or provide --prepared-receptor")
            return ToolResult(status="failed", artifacts=artifacts, error=f"{type(exc).__name__}: {exc}")

    def _publish_outputs(self, target: TargetSpec, repaired: Path, metadata_path: Path,
                         log_path: Path, metadata: dict[str, Any], parent_id: str) -> tuple[ArtifactRef, ArtifactRef, ArtifactRef]:
        repaired_artifact = self.store.publish_bytes(
            relative_path=f"results/receptor_repair/{target.target_id}/repaired_receptor.pdb",
            content=repaired.read_bytes(), kind="repaired_receptor_pdb",
            parent_ids=[parent_id],
            metadata={"target_id": target.target_id, "chemically_prepared": True,
                      "protonation_status": metadata.get("protonation_status"),
                      "preparation_backend": "pdbfixer"})
        metadata_artifact = self.store.publish_bytes(
            relative_path=f"results/receptor_repair/{target.target_id}/receptor_repair.json",
            content=metadata_path.read_bytes(), kind="receptor_repair_metadata",
            parent_ids=[parent_id, repaired_artifact.artifact_id], metadata={"target_id": target.target_id})
        log_artifact = self.store.publish_bytes(
            relative_path=f"results/receptor_repair/{target.target_id}/pdbfixer.log",
            content=log_path.read_bytes() if log_path.is_file() else b"",
            kind="receptor_repair_log", parent_ids=[parent_id, repaired_artifact.artifact_id], metadata={"target_id": target.target_id})
        return repaired_artifact, metadata_artifact, log_artifact


@dataclass
class ReceptorPreparationPipeline:
    repairer: ReceptorPreparer
    formatter: ReceptorPreparer

    def prepare(self, structure: ArtifactRef, target: TargetSpec, context: ToolContext) -> ToolResult:
        repaired = self.repairer.prepare(structure, target, context)
        if repaired.status != "succeeded":
            return repaired
        coordinate = next((item for item in reversed(repaired.artifacts)
                           if item.kind in {"repaired_receptor_pdb", "supplied_structure", "experimental_structure", "predicted_structure"}), None)
        if coordinate is None:
            return ToolResult(status="failed", artifacts=repaired.artifacts, metadata=repaired.metadata,
                              error="receptor repair produced no coordinate artifact")
        formatted = self.formatter.prepare(coordinate, target, context)
        return ToolResult(status=formatted.status, artifacts=repaired.artifacts + formatted.artifacts,
                          metadata={"repair": repaired.metadata, "format": formatted.metadata}, error=formatted.error)
