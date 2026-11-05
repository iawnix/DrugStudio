"""Append-only run events used by the workflow monitor.

The manifest is the recoverable scientific state.  This file is the live
operational timeline and is intentionally append-only so a browser can follow
the run without interpreting transient manifest writes.
"""

from __future__ import annotations

import json
import fcntl
import os
import tempfile
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional


class EventWriter:
    """Write small, JSON-serializable events below one run directory."""

    def __init__(self, run_root: Path, run_id: str):
        self.path = Path(run_root).resolve() / "logs" / "events.jsonl"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.run_id = run_id
        self._lock = threading.RLock()
        self._next_id = self._read_last_id() + 1

    def emit(self, event_type: str, *, stage: Optional[str] = None,
             status: Optional[str] = None, **metadata: Any) -> dict[str, Any]:
        with self._lock:
            lock_path = self.path.with_suffix(".lock")
            with lock_path.open("a", encoding="utf-8") as lock:
                fcntl.flock(lock, fcntl.LOCK_EX)
                self._next_id = max(self._next_id, self._read_last_id() + 1)
                event = self._build_event(event_type, stage=stage, status=status, metadata=metadata)
                with self.path.open("a", encoding="utf-8") as handle:
                    handle.write(json.dumps(event, ensure_ascii=False, separators=(",", ":")) + "\n")
                    handle.flush()
                self._next_id += 1
                fcntl.flock(lock, fcntl.LOCK_UN)
                return event

    def _build_event(self, event_type: str, *, stage: Optional[str], status: Optional[str],
                     metadata: dict[str, Any]) -> dict[str, Any]:
        event: dict[str, Any] = {
            "event_id": self._next_id,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "run_id": self.run_id,
            "type": event_type,
        }
        if stage is not None:
            event["stage"] = stage
        if status is not None:
            event["status"] = status
        event.update(_bounded(metadata))
        return event

    def read_since(self, since: int = 0, *, limit: int = 500) -> list[dict[str, Any]]:
        if not self.path.is_file():
            return []
        events: list[dict[str, Any]] = []
        with self._lock:
            for line in self.path.read_text(encoding="utf-8").splitlines():
                try:
                    item = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if int(item.get("event_id", 0)) > since:
                    events.append(item)
        return events[-limit:]

    def control_path(self) -> Path:
        return self.path.parent / "control.json"

    def control_request(self) -> Optional[str]:
        try:
            value = json.loads(self.control_path().read_text(encoding="utf-8"))
            action = value.get("action")
            return action if action in {"pause", "cancel"} else None
        except (OSError, json.JSONDecodeError, AttributeError):
            return None

    def set_control_request(self, action: Optional[str]) -> None:
        path = self.control_path()
        if action is None:
            try:
                path.unlink()
            except FileNotFoundError:
                pass
            return
        if action not in {"pause", "cancel"}:
            raise ValueError("control action must be pause, cancel or None")
        payload = {"action": action, "requested_at": datetime.now(timezone.utc).isoformat()}
        fd, temporary = tempfile.mkstemp(prefix=".control.", dir=str(path.parent))
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(payload, handle)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
        except Exception:
            try:
                os.unlink(temporary)
            except FileNotFoundError:
                pass
            raise

    def _read_last_id(self) -> int:
        if not self.path.is_file():
            return 0
        last = 0
        try:
            for line in self.path.read_text(encoding="utf-8").splitlines():
                try:
                    last = max(last, int(json.loads(line).get("event_id", 0)))
                except (TypeError, ValueError, json.JSONDecodeError):
                    continue
        except OSError:
            return 0
        return last


def _bounded(value: Any, depth: int = 0) -> Any:
    if depth > 5:
        return "[detail omitted]"
    if isinstance(value, dict):
        return {str(key): _bounded(item, depth + 1) for key, item in list(value.items())[:40]}
    if isinstance(value, (list, tuple)):
        return [_bounded(item, depth + 1) for item in list(value)[:40]]
    if isinstance(value, str) and len(value) > 1200:
        return value[:1200] + " [truncated]"
    return value


__all__ = ["EventWriter"]
