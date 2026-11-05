"""Composable ligand preparation stages."""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Iterable, List

from ..contracts import LigandSpec
from ..ports import LigandPreparer, ToolContext, ToolResult
from ..storage import ArtifactStore


@dataclass
class LigandPreparationPipeline:
    store: ArtifactStore
    stages: tuple[LigandPreparer, ...]

    def prepare(self, ligand: LigandSpec, context: ToolContext) -> ToolResult:
        current = ligand
        artifacts = []
        metadata = {}
        for stage in self.stages:
            result = stage.prepare(current, context)
            artifacts.extend(result.artifacts)
            metadata.update(result.metadata)
            if result.status != "succeeded":
                return ToolResult(status=result.status, artifacts=artifacts, metadata=metadata, error=result.error)
            # An optimizer publishes both initial and optimized coordinates.
            # Selection must not depend on their publication order.
            sdf = next((artifact for kind in ("prepared_ligand_sdf", "conformer_optimized_sdf", "conformer_initial_sdf")
                        for artifact in reversed(result.artifacts) if artifact.kind == kind), None)
            if sdf is not None:
                current = replace(current, input_path=str(self.store.path(sdf.path)), smiles=None)
        return ToolResult(status="succeeded", artifacts=artifacts, metadata=metadata)


__all__ = ["LigandPreparationPipeline"]
