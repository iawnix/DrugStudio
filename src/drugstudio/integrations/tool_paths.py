"""Locate optional tool wrappers without baking in one checkout layout."""

from __future__ import annotations

import os
import shutil
from pathlib import Path


def external_runner(environment_variable: str, relative_path: str) -> Path:
    """Return a configured runner, or the first runner in a known checkout.

    The scientific wrappers live outside the Python package and are therefore
    not included in the wheel.  Older code assumed they lived below
    ``src/``; that made every installed or source checkout fail before the
    first stage.  An explicit environment variable remains the portable
    option, while source checkouts can discover the workspace's shared skills.
    """
    configured = os.environ.get(environment_variable, "").strip()
    if configured:
        return Path(configured).expanduser().resolve()

    relative = Path(relative_path)
    candidates: list[Path] = []
    source = Path(__file__).resolve()
    for parent in source.parents:
        candidates.append(parent / relative)
    candidates.extend([
        Path.home() / ".codex" / "skills" / relative,
        Path("/opt/drugstudio/skills") / relative,
    ])
    for candidate in candidates:
        if candidate.is_file():
            return candidate.resolve()
    # Keep a deterministic path for the eventual diagnostic.  It is better
    # than resolving relative to the caller's current directory.
    return candidates[0].resolve()


def external_executable(environment_variable: str, names: tuple[str, ...],
                        relative_paths: tuple[str, ...] = ()) -> Path:
    """Find an optional executable in PATH, a configured location, or a checkout."""
    configured = os.environ.get(environment_variable, "").strip()
    if configured:
        return Path(configured).expanduser().resolve()
    for name in names:
        found = shutil.which(name)
        if found:
            return Path(found).resolve()
    source = Path(__file__).resolve()
    candidates = [parent / relative for parent in source.parents for relative in map(Path, relative_paths)]
    candidates.extend(Path(path).expanduser() for path in (
        "/home/agent/soft/p2rank/2.5.1/prank",
        "/home/agent/soft/p2rank/bin/prank",
    ))
    for candidate in candidates:
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return candidate.resolve()
    return candidates[0].resolve() if candidates else Path(names[0])


__all__ = ["external_executable", "external_runner"]
