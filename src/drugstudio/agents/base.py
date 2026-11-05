"""Small agent protocol with explicit state and tool boundaries."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Protocol

from ..contracts import RunState
from ..events import EventWriter
from ..ports import ToolContext
from ..storage import ArtifactStore


@dataclass(frozen=True)
class AgentContext:
    """Runtime dependencies supplied by the supervisor, never hidden globals."""

    store: ArtifactStore
    tools: ToolContext
    configuration: Dict[str, Any] = field(default_factory=dict)
    events: EventWriter | None = None


@dataclass
class AgentDecision:
    """Structured explanation recorded alongside a state transition."""

    agent: str
    action: str
    rationale: str
    inputs: Dict[str, Any] = field(default_factory=dict)
    outputs: Dict[str, Any] = field(default_factory=dict)
    requires_approval: bool = False


class DomainAgent(Protocol):
    """Every domain module exposes the same state transition shape."""

    name: str

    def invoke(self, state: RunState, context: AgentContext) -> RunState:
        ...


def ensure_workdir(context: AgentContext, relative_path: str) -> Path:
    """Create and return a tool-specific directory below the run root."""

    workdir = context.store.path(f"work/{relative_path}")
    workdir.mkdir(parents=True, exist_ok=True)
    return workdir
