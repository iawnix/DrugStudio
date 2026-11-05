"""Stable, serializable contracts shared by all workflow modules.

These dataclasses deliberately avoid a LangChain or chemistry-library
dependency.  They are the boundary between orchestration and scientific
tools, and can later be mirrored by Pydantic models in an API layer.
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional


class RunStatus(str, Enum):
    CREATED = "created"
    RUNNING = "running"
    WAITING_APPROVAL = "waiting_approval"
    WAITING_INPUT = "waiting_input"
    WAITING_EXECUTION = "waiting_execution"
    PAUSED = "paused"
    CANCELLED = "cancelled"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


@dataclass(frozen=True)
class ArtifactRef:
    """A content-addressed-ish reference to a produced file.

    ``path`` is relative to the owning run directory.  Absolute paths are
    intentionally excluded from the contract so manifests remain portable.
    """

    artifact_id: str
    kind: str
    path: str
    sha256: str
    parent_ids: List[str] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class TargetSpec:
    target_id: str
    uniprot_id: Optional[str] = None
    sequence: Optional[str] = None
    organism: Optional[str] = None
    pdb_ids: List[str] = field(default_factory=list)
    search_query: Optional[str] = None
    requested_chains: List[str] = field(default_factory=list)
    binding_site_residues: List[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        _validate_identifier(self.target_id)


@dataclass(frozen=True)
class LigandSpec:
    ligand_id: str
    smiles: Optional[str] = None
    input_path: Optional[str] = None
    parent_id: Optional[str] = None
    requested_states: List[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        _validate_identifier(self.ligand_id)


@dataclass(frozen=True)
class DockingPolicy:
    """User-visible policy knobs; engine-specific settings live in metadata."""

    preferred_engine: str = "vina"
    allow_structure_prediction: bool = True
    experimental_structure_preferred: bool = True
    # New runs are autonomous by default.  Set the supervised policy flag when
    # a human must approve every proposed docking box before execution.
    require_human_approval_for_box: bool = False
    retain_failed_tasks: bool = True
    project_name: str = "default"


@dataclass
class DockingTask:
    task_id: str
    ligand_id: str
    ligand_state_id: str
    receptor_id: str
    engine: str
    status: str = "created"
    input_artifact_ids: List[str] = field(default_factory=list)
    output_artifact_ids: List[str] = field(default_factory=list)
    score: Optional[float] = None
    error: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class RunState:
    """Checkpointable state for one target/ligand workflow run."""

    run_id: str
    target: TargetSpec
    ligands: List[LigandSpec]
    policy: DockingPolicy = field(default_factory=DockingPolicy)
    status: RunStatus = RunStatus.CREATED
    artifacts: Dict[str, List[ArtifactRef]] = field(default_factory=dict)
    decisions: List[Dict[str, Any]] = field(default_factory=list)
    # Human decisions are kept separately from model/tool traces so a review
    # can be audited and applied again without parsing LLM messages.
    human_decisions: List[Dict[str, Any]] = field(default_factory=list)
    # Active overrides are interpreted by the owning domain agent.  They are
    # intentionally small, JSON values rather than opaque callback state.
    overrides: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    tasks: List[DockingTask] = field(default_factory=list)
    approvals: List[Dict[str, Any]] = field(default_factory=list)
    failures: List[Dict[str, Any]] = field(default_factory=list)
    current_node: Optional[str] = None
    stage_results: Dict[str, Dict[str, Any]] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _validate_identifier(self.run_id)
        ids = [ligand.ligand_id for ligand in self.ligands]
        if len(ids) != len(set(ids)):
            raise ValueError("ligand IDs must be unique within a run")

    @classmethod
    def from_dict(cls, value: Dict[str, Any]) -> "RunState":
        data = dict(value)
        data["target"] = TargetSpec(**data["target"])
        data["ligands"] = [LigandSpec(**item) for item in data["ligands"]]
        data["policy"] = DockingPolicy(**data.get("policy", {}))
        data["status"] = RunStatus(data.get("status", "created"))
        data["artifacts"] = {
            stage: [ArtifactRef(**item) for item in items]
            for stage, items in data.get("artifacts", {}).items()
        }
        data["tasks"] = [DockingTask(**item) for item in data.get("tasks", [])]
        return cls(**data)

    def add_artifact(self, stage: str, artifact: ArtifactRef) -> None:
        self.artifacts.setdefault(stage, []).append(artifact)

    def add_failure(self, stage: str, message: str, **details: Any) -> None:
        self.failures.append({"stage": stage, "message": message, **details})

    def to_dict(self) -> Dict[str, Any]:
        return _jsonable(asdict(self))

    def to_json(self, *, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent, sort_keys=True)


def _jsonable(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    return value


def _validate_identifier(value: str) -> None:
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", value):
        raise ValueError("identifiers must be 1–128 letters, digits, dots, underscores or hyphens, starting with a letter or digit")
