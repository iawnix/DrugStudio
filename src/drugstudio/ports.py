"""Dependency-inversion ports for scientific backends."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Protocol

from .contracts import ArtifactRef, DockingTask, LigandSpec, TargetSpec


@dataclass(frozen=True)
class ToolContext:
    run_id: str
    workdir: Path
    timeout_seconds: int = 7200
    environment: Dict[str, str] = field(default_factory=dict)
    configuration: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ToolResult:
    status: str
    artifacts: List[ArtifactRef] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)
    error: Optional[str] = None


class StructureProvider(Protocol):
    def search(self, target: TargetSpec, context: ToolContext) -> ToolResult:
        ...

    def predict(self, target: TargetSpec, context: ToolContext) -> ToolResult:
        ...


class ReceptorPreparer(Protocol):
    def prepare(self, structure: ArtifactRef, target: TargetSpec, context: ToolContext) -> ToolResult:
        ...


class PocketFinder(Protocol):
    def predict(self, receptor: ArtifactRef, *, is_predicted: bool, context: ToolContext) -> ToolResult:
        ...


class LigandPreparer(Protocol):
    def prepare(self, ligand: LigandSpec, context: ToolContext) -> ToolResult:
        ...


class DockingEngine(Protocol):
    name: str

    def plan(self, receptor: ArtifactRef, ligands: Iterable[ArtifactRef], context: ToolContext) -> List[DockingTask]:
        ...

    def run(self, tasks: Iterable[DockingTask], context: ToolContext) -> ToolResult:
        ...


class Assessor(Protocol):
    def assess(self, artifacts: Iterable[ArtifactRef], context: ToolContext) -> ToolResult:
        ...
