"""Small, file-backed workbench metadata and provenance helpers.

The scientific manifest remains the source of truth.  This module stores only
the relationships needed by the web workbench, so deployments do not need a
database before they have multiple users or a larger run history.
"""

from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


METADATA_VERSION = 1
GRAPH_VERSION = "dock-agent-scientific-pipeline-v1"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def atomic_json_write(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(value, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except Exception:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise


def read_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default


def project_id_for_run(run_root: Path, runs_root: Path) -> str:
    """Return the stable project key represented by a run directory."""
    relative = run_root.resolve().relative_to(runs_root.resolve())
    if len(relative.parts) >= 2:
        return relative.parts[0]
    # A direct run-root deployment has no project folder.  Keep a useful key
    # rather than inventing a path outside the configured runs root.
    return runs_root.name if runs_root.name not in {"", ".", "runs"} else "."


def fallback_run_metadata(run_root: Path, runs_root: Path, manifest: dict[str, Any]) -> dict[str, Any]:
    project_id = project_id_for_run(run_root, runs_root)
    events_path = run_root / "logs" / "events.jsonl"
    created_at = None
    if events_path.is_file():
        for line in events_path.read_text(encoding="utf-8", errors="replace").splitlines():
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if event.get("type") in {"run_created", "run_started"}:
                created_at = event.get("timestamp")
                break
    if not created_at:
        try:
            created_at = datetime.fromtimestamp(run_root.stat().st_mtime, timezone.utc).isoformat()
        except OSError:
            created_at = utc_now()
    decisions = manifest.get("decisions") or []
    parent_run_id = None
    for decision in reversed(decisions):
        if decision.get("action") == "branch_rerun":
            parent_run_id = decision.get("parent_run_id")
            break
    target = manifest.get("target") or {}
    ligands = manifest.get("ligands") or []
    policy = manifest.get("policy") or {}
    return {
        "metadata_version": METADATA_VERSION,
        "run_id": manifest.get("run_id", run_root.name),
        "project_id": project_id,
        "project_name": project_id if project_id != "." else runs_root.name,
        "conversation_id": None,
        "parent_run_id": parent_run_id,
        "created_at": created_at,
        "updated_at": created_at,
        "graph_version": GRAPH_VERSION,
        "params_snapshot": {"target": target, "ligands": ligands, "policy": policy},
    }


def load_run_metadata(run_root: Path, runs_root: Path, manifest: dict[str, Any]) -> dict[str, Any]:
    fallback = fallback_run_metadata(run_root, runs_root, manifest)
    value = read_json(run_root / "run-meta.json", {})
    if not isinstance(value, dict):
        value = {}
    merged = dict(fallback)
    merged.update({key: item for key, item in value.items() if item is not None})
    merged["run_id"] = manifest.get("run_id", run_root.name)
    merged["metadata_version"] = METADATA_VERSION
    return merged


def save_run_metadata(run_root: Path, runs_root: Path, manifest: dict[str, Any], **updates: Any) -> dict[str, Any]:
    value = load_run_metadata(run_root, runs_root, manifest)
    for key, item in updates.items():
        if item is not None:
            value[key] = item
    value["updated_at"] = utc_now()
    atomic_json_write(run_root / "run-meta.json", value)
    return value


def build_project_index(runs_root: Path, runs: list[tuple[Path, dict[str, Any], dict[str, Any]]]) -> dict[str, Any]:
    projects: dict[str, dict[str, Any]] = {}
    for path, manifest, metadata in runs:
        project_id = str(metadata.get("project_id") or project_id_for_run(path, runs_root))
        entry = projects.setdefault(project_id, {
            "project_id": project_id,
            "name": metadata.get("project_name") or project_id,
            "created_at": metadata.get("created_at"),
            "updated_at": metadata.get("updated_at"),
            "run_keys": [],
            "run_count": 0,
        })
        entry["run_keys"].append(path.resolve().relative_to(runs_root.resolve()).as_posix())
        entry["run_count"] += 1
        dates = [item for item in (entry.get("created_at"), metadata.get("created_at")) if item]
        entry["created_at"] = min(dates) if dates else None
        updated = [item for item in (entry.get("updated_at"), metadata.get("updated_at")) if item]
        entry["updated_at"] = max(updated) if updated else None
    return {"metadata_version": METADATA_VERSION, "updated_at": utc_now(), "projects": projects}


__all__ = [
    "GRAPH_VERSION",
    "METADATA_VERSION",
    "atomic_json_write",
    "build_project_index",
    "load_run_metadata",
    "project_id_for_run",
    "read_json",
    "save_run_metadata",
    "utc_now",
]
