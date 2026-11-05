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
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional


class EventWriter:
    """Write small, JSON-serializable events below one run directory."""

    # How many recent events stay parsed in memory.  Live followers only ever
    # ask for the tail, so this bounds both memory and the cost of a poll.
    TAIL_WINDOW = 5000

    def __init__(self, run_root: Path, run_id: str):
        self.path = Path(run_root).resolve() / "logs" / "events.jsonl"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.run_id = run_id
        self._lock = threading.RLock()
        # Incremental read state.  The log is append-only and is also written
        # by the workflow subprocess, so each sync seeks to the byte offset it
        # last consumed and parses only what arrived since.
        self._offset = 0
        self._identity: tuple[int, int] | None = None
        self._pending = b""
        self._tail: deque[dict[str, Any]] = deque(maxlen=self.TAIL_WINDOW)
        self._last_id = 0
        self._usage: dict[str, dict[str, int]] = {}
        self._sync()
        self._next_id = self._last_id + 1

    def _reset_scan(self) -> None:
        self._offset = 0
        self._pending = b""
        self._tail.clear()
        self._last_id = 0
        self._usage = {}

    def _sync(self) -> None:
        """Parse whatever has been appended since the last sync."""
        with self._lock:
            try:
                stat = self.path.stat()
            except OSError:
                self._reset_scan()
                self._identity = None
                return
            identity = (stat.st_ino, stat.st_dev)
            # A rotated, replaced or truncated log invalidates our offset.
            if identity != self._identity or stat.st_size < self._offset:
                self._reset_scan()
                self._identity = identity
            if stat.st_size == self._offset:
                return
            try:
                with self.path.open("rb") as handle:
                    handle.seek(self._offset)
                    chunk = handle.read()
            except OSError:
                return
            self._offset += len(chunk)
            buffer = self._pending + chunk
            # A reader can catch the writer mid-line; hold the remainder back
            # until its newline arrives rather than dropping the event.
            lines = buffer.split(b"\n")
            self._pending = lines.pop()
            for raw in lines:
                if not raw.strip():
                    continue
                try:
                    item = json.loads(raw.decode("utf-8"))
                except (UnicodeDecodeError, json.JSONDecodeError):
                    continue
                if not isinstance(item, dict):
                    continue
                self._observe(item)

    def _observe(self, item: dict[str, Any]) -> None:
        try:
            self._last_id = max(self._last_id, int(item.get("event_id", 0)))
        except (TypeError, ValueError):
            pass
        self._tail.append(item)
        if item.get("type") != "model_finished" or item.get("status") != "completed":
            return
        # Token totals are folded in as events arrive so cost reporting never
        # has to re-read the whole log.
        usage = item.get("usage") or {}
        row = self._usage.setdefault(str(item.get("model") or "unknown"),
                                     {"calls": 0, "input_tokens": 0, "output_tokens": 0,
                                      "reported_calls": 0})
        row["calls"] += 1
        prompt = usage.get("input_tokens", usage.get("prompt_tokens"))
        completion = usage.get("output_tokens", usage.get("completion_tokens"))
        if prompt is None or completion is None:
            return
        try:
            row["input_tokens"] += int(prompt)
            row["output_tokens"] += int(completion)
        except (TypeError, ValueError):
            return
        row["reported_calls"] += 1

    def usage_by_model(self) -> dict[str, dict[str, int]]:
        """Per-model token totals, accumulated incrementally."""
        with self._lock:
            self._sync()
            return {model: dict(row) for model, row in self._usage.items()}

    def last_event_id(self) -> int:
        with self._lock:
            self._sync()
            return self._last_id

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
        with self._lock:
            self._sync()
            # The in-memory tail answers live polling without touching disk.
            # Only a request reaching back past the window re-reads the file,
            # which keeps the result identical to a full scan.
            if self._tail and since >= self._first_tail_id() - 1:
                # Ids are monotonic, so walk back from the newest event and
                # stop at the cursor: the cost is the number of new events,
                # not the size of the window.
                events = []
                for item in reversed(self._tail):
                    if _event_id(item) <= since:
                        break
                    events.append(item)
                events.reverse()
            else:
                events = self._full_scan(since)
        return events[-limit:]

    def _first_tail_id(self) -> int:
        return _event_id(self._tail[0]) if self._tail else 0

    def _full_scan(self, since: int) -> list[dict[str, Any]]:
        events: list[dict[str, Any]] = []
        try:
            text = self.path.read_text(encoding="utf-8")
        except OSError:
            return events
        for line in text.splitlines():
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(item, dict) and _event_id(item) > since:
                events.append(item)
        return events

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
        # Incremental: picks up appends from the workflow subprocess without
        # re-parsing the log, which used to make every emit cost O(events).
        with self._lock:
            self._sync()
            return self._last_id


def _event_id(item: dict[str, Any]) -> int:
    try:
        return int(item.get("event_id", 0))
    except (TypeError, ValueError):
        return 0


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
