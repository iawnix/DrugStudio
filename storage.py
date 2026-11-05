"""Run-local artifact storage and manifest persistence."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from pathlib import Path
from typing import Any, Iterable, Optional, Union

from .contracts import ArtifactRef, RunState


class ArtifactStore:
    """Keep every run's work, logs, cache, and published results isolated.

    The caller supplies an owning project/run directory.  The store never
    writes to the workspace root and rejects paths that escape that directory.
    """

    _SUBDIRECTORIES = ("inputs", "work", "results", "logs", "cache")

    def __init__(self, run_root: Union[Path, str]):
        self.root = Path(run_root).expanduser().resolve()
        for name in self._SUBDIRECTORIES:
            (self.root / name).mkdir(parents=True, exist_ok=True)

    def path(self, relative_path: str) -> Path:
        candidate = (self.root / relative_path).resolve()
        try:
            candidate.relative_to(self.root)
        except ValueError as exc:
            raise ValueError("artifact path escapes the run directory") from exc
        return candidate

    def publish_bytes(
        self,
        *,
        relative_path: str,
        content: bytes,
        kind: str,
        parent_ids: Optional[Iterable[str]] = None,
        metadata: Optional[dict[str, Any]] = None,
    ) -> ArtifactRef:
        destination = self.path(relative_path)
        destination.parent.mkdir(parents=True, exist_ok=True)
        digest = hashlib.sha256(content).hexdigest()
        if destination.exists():
            existing_digest = _sha256(destination)
            if existing_digest != digest:
                raise FileExistsError(
                    f"refusing to replace an existing artifact: {destination}"
                )
        else:
            _atomic_write(destination, content)
        artifact_id = f"{kind}:{digest[:16]}"
        return ArtifactRef(
            artifact_id=artifact_id,
            kind=kind,
            path=destination.relative_to(self.root).as_posix(),
            sha256=digest,
            parent_ids=list(parent_ids or []),
            metadata=dict(metadata or {}),
        )

    def publish_json(self, *, relative_path: str, value: Any, kind: str, **kwargs: Any) -> ArtifactRef:
        content = (json.dumps(value, indent=2, sort_keys=True) + "\n").encode("utf-8")
        return self.publish_bytes(relative_path=relative_path, content=content, kind=kind, **kwargs)

    def save_manifest(self, state: RunState) -> Path:
        destination = self.path("manifest.json")
        _atomic_write(destination, (state.to_json() + "\n").encode("utf-8"))
        return destination

    def load_manifest(self) -> RunState:
        state = RunState.from_dict(json.loads(self.path("manifest.json").read_text(encoding="utf-8")))
        for items in state.artifacts.values():
            for artifact in items:
                source = self.path(artifact.path)
                if not source.is_file() or _sha256(source) != artifact.sha256:
                    raise ValueError(f"missing or modified artifact: {artifact.path}")
        return state

    def iter_artifacts(self) -> Iterable[ArtifactRef]:
        """Yield manifest artifacts without exposing the run's absolute path."""
        state = self.load_manifest()
        for items in state.artifacts.values():
            yield from items

    def find_artifact(self, *, artifact_id: str = "", relative_path: str = "") -> Optional[ArtifactRef]:
        """Find one verified artifact by its stable ID or run-relative path."""
        if not artifact_id and not relative_path:
            raise ValueError("artifact_id or relative_path is required")
        for artifact in self.iter_artifacts():
            if artifact_id and artifact.artifact_id == artifact_id:
                return artifact
            if relative_path and artifact.path == relative_path:
                return artifact
        return None


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _atomic_write(destination: Path, content: bytes) -> None:
    fd, temporary_name = tempfile.mkstemp(prefix=f".{destination.name}.", dir=str(destination.parent))
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_name, destination)
    except Exception:
        try:
            os.unlink(temporary_name)
        except FileNotFoundError:
            pass
        raise
