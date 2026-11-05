"""Locate optional tool wrappers without baking in one checkout layout."""

from __future__ import annotations

import os
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


__all__ = ["external_runner"]
