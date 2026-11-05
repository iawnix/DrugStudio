"""Small dependency-free web server for a collection of sibling DrugStudio runs.

The browser receives a curated manifest snapshot and can only read files below
the selected run root. Long-running workflow execution stays in the existing
CLI, so the same process lock, manifest persistence and stage semantics apply.
"""

from __future__ import annotations

import argparse
import fcntl
import ipaddress
import json
import math
import mimetypes
import os
import pty
import select
import secrets
import signal
import shutil
import struct
import subprocess
import sys
import threading
import termios
import uuid
import fcntl as file_control
import hmac
from http.cookies import SimpleCookie
from datetime import datetime, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from ..events import EventWriter
from ..workbench import (
    atomic_json_write,
    build_project_index,
    load_run_metadata,
    read_json,
    save_run_metadata,
)


STATIC_ROOT = Path(__file__).with_name("static")


class WebApplication:
    def __init__(self, run_root: Path, workspace: Path, runs_root: Path | None = None,
                 workspace_root: Path | None = None, web_token: str | None = None):
        # These two are *defaults* for requests that name no scope.  They are
        # never reassigned: every request resolves its own project and run from
        # its own parameters, so two tabs (or two people) cannot clobber each
        # other's selection.
        self.default_run_root = run_root.resolve()
        self.workspace = workspace.resolve()
        self.runs_root = (runs_root or run_root.parent).resolve()
        self.workspace_root = (workspace_root or self.workspace).resolve()
        self.default_run_root.relative_to(self.runs_root)
        self.web_token = web_token
        # Host values this server will answer to.  Without this check a page on
        # any origin can reach a loopback bind through DNS rebinding, and the
        # terminal endpoints hand out a shell.
        self.allowed_hosts: set[str] = set()
        # Session identifiers are random and unrelated to the access token, so
        # a leaked cookie cannot be replayed as the token itself.
        self._sessions: set[str] = set()
        self._session_lock = threading.Lock()
        self.chat_store = self.runs_root / ".workbench" / "conversations.json"
        self._chat_lock = threading.Lock()
        self._llm_config = {
            "api_key": os.environ.get("OPENAI_API_KEY", ""),
            "base_url": os.environ.get("OPENAI_BASE_URL", ""),
            "model": os.environ.get("DOCK_AGENT_MODEL", ""),
        }
        self._llm_config_lock = threading.Lock()
        self._metadata_lock = threading.Lock()
        self.workbench_root = self.runs_root / ".workbench"
        self.projects_index = self.workbench_root / "projects.json"
        self.default_project_root = self._initial_project_root()
        raw_manifest = self.manifest(self.default_run_root)
        self.ensure_run_metadata(self.default_run_root, raw_manifest)
        # One startup pass also materializes sidecars for older sibling runs.
        with self._metadata_lock:
            self._refresh_project_index_locked()
        self._event_writers: dict[Path, EventWriter] = {}
        self._event_lock = threading.Lock()
        # One workflow process per run, not one per server.
        self._processes: dict[Path, subprocess.Popen[str]] = {}
        self._process_lock = threading.Lock()
        self._terminal_lock = threading.Lock()
        self._terminals: dict[str, dict] = {}

    # ----- scope resolution -------------------------------------------------

    def resolve_run(self, run_key: str = "") -> Path:
        """Resolve a request-supplied run key to a directory under runs_root."""
        if not run_key:
            return self.default_run_root
        candidate = (self.runs_root / run_key).resolve()
        try:
            candidate.relative_to(self.runs_root)
        except ValueError as exc:
            raise ValueError("run key escapes the configured runs root") from exc
        if not (candidate / "manifest.json").is_file():
            # Fall back to matching on run_id for older clients and links.
            match = next((item for item in self.list_runs() if item["run_id"] == run_key), None)
            if match is None:
                raise ValueError(f"run not found in {self.runs_root}: {run_key}")
            candidate = (self.runs_root / match["run_key"]).resolve()
        return candidate

    def resolve_project(self, project_id: str = "") -> Path:
        if not project_id:
            return self.default_project_root
        return self._project_for_id(project_id)

    def project_id_of(self, project_root: Path) -> str:
        return ("." if project_root == self.workspace_root
                else project_root.relative_to(self.workspace_root).as_posix())

    def events_for(self, run_root: Path) -> EventWriter:
        run_root = run_root.resolve()
        with self._event_lock:
            writer = self._event_writers.get(run_root)
            if writer is None:
                try:
                    run_id = str(self.manifest(run_root).get("run_id", run_root.name))
                except (OSError, json.JSONDecodeError, FileNotFoundError):
                    run_id = run_root.name
                writer = EventWriter(run_root, run_id)
                self._event_writers[run_root] = writer
            return writer

    def _initial_project_root(self) -> Path:
        try:
            relative = self.default_run_root.relative_to(self.workspace_root)
            first = relative.parts[0]
            candidate = self.workspace_root / first
            return candidate if candidate.is_dir() else self.workspace_root
        except (ValueError, IndexError):
            return self.workspace_root

    def list_projects(self, project_root: Path | None = None) -> list[dict]:
        selected = (project_root or self.default_project_root).resolve()
        rows = []
        for path in sorted(self.workspace_root.iterdir(), key=lambda item: item.name.casefold()):
            if path.name.startswith(".") or not path.is_dir() or path.is_symlink():
                continue
            rows.append({"project_id": path.relative_to(self.workspace_root).as_posix(),
                         "name": path.name, "selected": path.resolve() == selected})
        if self.workspace_root.resolve() == selected:
            rows.insert(0, {"project_id": ".", "name": self.workspace_root.name, "selected": True})
        return rows

    def project_identity(self, project_root: Path | None = None) -> dict:
        root = project_root or self.default_project_root
        return {"project_id": self.project_id_of(root), "name": root.name}

    def ensure_run_metadata(self, run_root: Path, manifest: dict | None = None, **updates: object) -> dict:
        manifest = manifest or self.manifest(run_root)
        with self._metadata_lock:
            metadata_path = run_root / "run-meta.json"
            existing = load_run_metadata(run_root, self.runs_root, manifest)
            changed = not metadata_path.is_file()
            for key, item in updates.items():
                if item is not None and existing.get(key) != item:
                    changed = True
            metadata = save_run_metadata(run_root, self.runs_root, manifest, **updates) if changed else existing
            if changed or not self.projects_index.is_file():
                self._refresh_project_index_locked()
            return metadata

    def run_metadata(self, path: Path, manifest: dict | None = None) -> dict:
        manifest = manifest or json.loads((path / "manifest.json").read_text(encoding="utf-8"))
        return load_run_metadata(path, self.runs_root, manifest)

    def _refresh_project_index_locked(self) -> dict:
        rows = []
        for manifest_path in self.runs_root.rglob("manifest.json"):
            path = manifest_path.parent
            try:
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            metadata = self.run_metadata(path, manifest)
            if not (path / "run-meta.json").is_file():
                metadata = save_run_metadata(path, self.runs_root, manifest)
            rows.append((path, manifest, metadata))
        index = build_project_index(self.runs_root, rows)
        atomic_json_write(self.projects_index, index)
        return index

    def workbench_projects(self) -> list[dict]:
        with self._metadata_lock:
            index = read_json(self.projects_index, None)
            if not isinstance(index, dict) or not isinstance(index.get("projects"), dict):
                index = self._refresh_project_index_locked()
            return sorted(index["projects"].values(), key=lambda item: str(item.get("name", "")).casefold())

    def provenance(self, run_root: Path, *, artifact_id: str = "", path: str = "", stage: str = "") -> dict:
        """Return a bounded upstream artifact chain for the requested run."""
        manifest = self.manifest(run_root)
        entries: dict[str, dict] = {}
        for stage_name, items in (manifest.get("artifacts") or {}).items():
            for item in items or []:
                if not isinstance(item, dict):
                    continue
                record = dict(item)
                record["stage"] = stage_name
                record["exists"] = self._artifact_exists(run_root, record.get("path", ""))
                entries[str(record.get("artifact_id"))] = record
        target = None
        if artifact_id:
            target = entries.get(artifact_id)
        if target is None and path:
            target = next((item for item in entries.values() if item.get("path") == path), None)
        if target is None and stage:
            target = next((item for item in reversed(list(entries.values())) if item.get("stage") == stage), None)
        if target is None and entries:
            target = next(reversed(entries.values()))
        chain: list[dict] = []
        visited: set[str] = set()

        def visit(item: dict) -> None:
            item_id = str(item.get("artifact_id") or "")
            if item_id in visited:
                return
            visited.add(item_id)
            for parent_id in item.get("parent_ids") or []:
                parent = entries.get(str(parent_id))
                if parent is not None:
                    visit(parent)
            chain.append(self._public_provenance_item(item))

        if target is not None:
            visit(target)
        stages = []
        for stage_name, result in (manifest.get("stage_results") or {}).items():
            if not isinstance(result, dict):
                continue
            if not chain or stage_name in {item.get("stage") for item in chain}:
                stages.append({"stage": stage_name, "status": result.get("status"),
                               "attempt": result.get("attempt"),
                               "metadata": _small_metadata(result.get("metadata", {})),
                               "error": result.get("error")})
        chain_ids = {item.get("artifact_id") for item in chain}
        chain_stages = {item.get("stage") for item in chain}
        events = []
        for event in self.events_for(run_root).read_since(0, limit=100000):
            if event.get("artifact_id") in chain_ids or event.get("stage") in chain_stages:
                events.append(_small_value(event))
        return {"run_id": manifest.get("run_id"), "run_key": self.run_key(run_root),
                "run_meta": self.run_metadata(run_root, manifest),
                "target": self._public_provenance_item(target) if target else None,
                "chain": chain, "stages": stages, "events": events[-100:]}

    def _artifact_exists(self, run_root: Path, relative: str) -> bool:
        if not relative:
            return False
        candidate = (run_root / relative).resolve()
        try:
            candidate.relative_to(run_root.resolve())
        except ValueError:
            return False
        return candidate.is_file()

    @staticmethod
    def _public_provenance_item(item: dict | None) -> dict | None:
        if item is None:
            return None
        return {"artifact_id": item.get("artifact_id"), "kind": item.get("kind"),
                "stage": item.get("stage"), "path": item.get("path"),
                "sha256": item.get("sha256"), "parent_ids": item.get("parent_ids", []),
                "exists": bool(item.get("exists")), "metadata": _small_metadata(item.get("metadata", {}))}

    def host_allowed(self, host_header: str) -> bool:
        if not self.allowed_hosts:
            return True
        return (host_header or "").strip().lower() in self.allowed_hosts

    def open_session(self) -> str:
        session_id = secrets.token_urlsafe(32)
        with self._session_lock:
            if len(self._sessions) > 256:
                self._sessions.clear()
            self._sessions.add(session_id)
        return session_id

    def session_valid(self, session_id: str) -> bool:
        with self._session_lock:
            return any(hmac.compare_digest(session_id, known) for known in self._sessions)

    def model_config(self, run_root: Path | None = None) -> dict:
        run_model = _public_model_info(run_root or self.default_run_root) or {}
        with self._llm_config_lock:
            return {
                "base_url": self._llm_config["base_url"] or run_model.get("base_url", ""),
                "model": self._llm_config["model"] or run_model.get("model", "") or "gpt-4.1-mini",
                "api_key_configured": bool(self._llm_config["api_key"]),
            }

    def update_model_config(self, payload: dict) -> dict:
        base_url = str(payload.get("base_url") or "").strip().rstrip("/")
        model = str(payload.get("model") or "").strip()
        api_key = str(payload.get("api_key") or "").strip()
        if len(base_url) > 2048 or len(model) > 160 or len(api_key) > 4096:
            raise ValueError("model settings exceed the allowed length")
        if base_url:
            parsed = urlparse(base_url)
            if (parsed.scheme not in {"http", "https"} or not parsed.netloc or parsed.username or
                    parsed.password or parsed.query or parsed.fragment):
                raise ValueError("API URL must be an http(s) base URL without credentials or query data")
        if not model:
            raise ValueError("model name is required")
        with self._llm_config_lock:
            self._llm_config["base_url"] = base_url
            self._llm_config["model"] = model
            if api_key:
                self._llm_config["api_key"] = api_key
            elif payload.get("clear_api_key"):
                self._llm_config["api_key"] = ""
        return self.model_config()

    def _project_for_id(self, project_id: str) -> Path:
        source = self.workspace_root if project_id in {"", "."} else self.workspace_root / project_id
        if source.is_symlink():
            raise ValueError("project symlinks are not supported")
        candidate = source.resolve()
        candidate.relative_to(self.workspace_root)
        if not candidate.is_dir():
            raise ValueError("project directory not found")
        return candidate

    def _read_chat_store(self) -> dict:
        try:
            value = json.loads(self.chat_store.read_text(encoding="utf-8"))
            return value if isinstance(value, dict) else {}
        except FileNotFoundError:
            return {}

    def _write_chat_store(self, value: dict) -> None:
        self.chat_store.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.chat_store.with_name(f".{self.chat_store.name}.{uuid.uuid4().hex}.tmp")
        try:
            temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            os.replace(temporary, self.chat_store)
        finally:
            temporary.unlink(missing_ok=True)

    def list_chats(self, project_id: str = "") -> dict:
        project_root = self._project_for_id(project_id)
        key = "." if project_root == self.workspace_root else project_root.relative_to(self.workspace_root).as_posix()
        with self._chat_lock:
            rows = self._read_chat_store().get(key, [])
        return {"project_id": key, "chats": sorted(rows, key=lambda item: item.get("updated_at", ""), reverse=True)}

    def create_chat(self, project_id: str = "") -> dict:
        project_root = self._project_for_id(project_id)
        key = "." if project_root == self.workspace_root else project_root.relative_to(self.workspace_root).as_posix()
        now = datetime.now(timezone.utc).isoformat()
        chat = {"chat_id": uuid.uuid4().hex, "title": "新对话", "created_at": now,
                "updated_at": now, "messages": []}
        with self._chat_lock:
            store = self._read_chat_store()
            store.setdefault(key, []).append(chat)
            self._write_chat_store(store)
        return chat

    def save_chat(self, project_id: str, chat_id: str, messages: list) -> dict:
        project_root = self._project_for_id(project_id)
        key = "." if project_root == self.workspace_root else project_root.relative_to(self.workspace_root).as_posix()
        clean = []
        for item in messages[-100:]:
            if not isinstance(item, dict) or item.get("role") not in {"user", "assistant"}:
                continue
            message = {"role": item["role"], "content": str(item.get("content") or "")[:20_000]}
            if isinstance(item.get("actions"), list):
                message["actions"] = item["actions"][:20]
            clean.append(message)
        with self._chat_lock:
            store = self._read_chat_store()
            chat = next((item for item in store.get(key, []) if item.get("chat_id") == chat_id), None)
            if chat is None:
                raise ValueError("conversation not found")
            chat["messages"] = clean
            chat["updated_at"] = datetime.now(timezone.utc).isoformat()
            first_user = next((item["content"].strip() for item in clean if item["role"] == "user" and item["content"].strip()), "")
            if chat.get("title") in {"New conversation", "新对话"} and first_user:
                chat["title"] = first_user[:64]
            self._write_chat_store(store)
        return chat

    def archive_chat(self, project_id: str, chat_id: str, archived: bool = True) -> dict:
        project_root = self._project_for_id(project_id)
        key = "." if project_root == self.workspace_root else project_root.relative_to(self.workspace_root).as_posix()
        with self._chat_lock:
            store = self._read_chat_store()
            chat = next((item for item in store.get(key, []) if item.get("chat_id") == chat_id), None)
            if chat is None:
                raise ValueError("conversation not found")
            chat["archived_at"] = datetime.now(timezone.utc).isoformat() if archived else None
            chat["updated_at"] = datetime.now(timezone.utc).isoformat()
            self._write_chat_store(store)
        return chat

    def select_project(self, project_id: str) -> dict:
        """Validate a project the client is switching to.

        Selection lives in the browser now: the server keeps no "current
        project", so this only confirms the id resolves and echoes its name.
        """
        candidate = self._project_for_id(project_id)
        return {"project_id": self.project_id_of(candidate), "name": candidate.name}

    def project_file(self, relative: str, *, allow_missing: bool = False, root: Path | None = None) -> Path:
        project_root = root or self.default_project_root
        candidate = (project_root / relative).resolve()
        try:
            candidate.relative_to(project_root)
        except ValueError as exc:
            raise ValueError("path escapes the selected project") from exc
        if not allow_missing and not candidate.is_file():
            raise FileNotFoundError(relative or ".")
        return candidate

    def list_project_files(self, relative: str = "", *, root: Path | None = None) -> dict:
        project_root = root or self.default_project_root
        directory = self.project_file(relative, allow_missing=True, root=project_root)
        if not directory.is_dir():
            raise NotADirectoryError(relative)
        entries = []
        children = sorted(directory.iterdir(), key=lambda item: (not item.is_dir(), item.name.casefold()))
        for path in children[:500]:
            if path.is_symlink():
                continue
            info = {"name": path.name, "path": path.relative_to(project_root).as_posix(),
                    "kind": "directory" if path.is_dir() else "file"}
            if path.is_file():
                size = path.stat().st_size
                info["size"] = size
                info["previewable"] = False
                if size > 1_000_000:
                    info["preview_reason"] = "File exceeds the 1 MiB preview limit"
                else:
                    with path.open("rb") as handle:
                        sample = handle.read(8192)
                    try:
                        sample[:-4].decode("utf-8") if len(sample) == 8192 else sample.decode("utf-8")
                        info["previewable"] = b"\0" not in sample
                    except UnicodeDecodeError:
                        pass
                    if not info["previewable"]:
                        info["preview_reason"] = "Binary or non-UTF-8 file"
            entries.append(info)
        return {"path": relative, "entries": entries, "truncated": len(children) > 500}

    def read_project_file(self, relative: str, *, root: Path | None = None) -> dict:
        path = self.project_file(relative, root=root)
        if path.stat().st_size > 1_000_000:
            raise ValueError("file exceeds the 1 MiB editor limit")
        try:
            content = path.read_text(encoding="utf-8")
        except UnicodeDecodeError as exc:
            raise ValueError("file is not UTF-8 text") from exc
        return {"path": relative, "content": content}

    def write_project_file(self, relative: str, content: str, *, root: Path | None = None) -> dict:
        if not relative or len(content.encode("utf-8")) > 1_000_000:
            raise ValueError("provide a file path and content below 1 MiB")
        path = self.project_file(relative, allow_missing=True, root=root)
        if path.is_dir():
            raise ValueError("file path points to a directory")
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
        try:
            temporary.write_text(content, encoding="utf-8")
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)
        return {"path": relative, "bytes": path.stat().st_size, "saved": True}

    def run_project_command(self, command: str, cwd: str = "", *, redact_environment: bool = False,
                            root: Path | None = None) -> dict:
        if not command.strip() or len(command) > 20_000:
            raise ValueError("command must contain 1 to 20,000 characters")
        project_root = root or self.default_project_root
        working_directory = self.project_file(cwd, allow_missing=False, root=project_root) if cwd else project_root
        if not working_directory.is_dir():
            raise NotADirectoryError(cwd)
        environment = os.environ.copy()
        if redact_environment:
            for name in list(environment):
                if any(marker in name.upper() for marker in ("KEY", "TOKEN", "SECRET", "PASSWORD", "CREDENTIAL")):
                    environment.pop(name, None)
        marker = f"__DOCK_AGENT_CWD_{uuid.uuid4().hex}__"
        wrapped_command = f'{command}\n__dock_status=$?; printf "\\n{marker}%s\\n" "$PWD"; exit $__dock_status'
        completed = subprocess.run(["/bin/bash", "-lc", wrapped_command], cwd=working_directory,
                                   capture_output=True, text=True, timeout=60,
                                   env=environment)
        stdout = completed.stdout
        next_cwd = working_directory
        if marker in stdout:
            stdout, reported_cwd = stdout.rsplit(marker, 1)
            try:
                candidate_cwd = Path(reported_cwd.strip()).resolve()
                candidate_cwd.relative_to(project_root)
                next_cwd = candidate_cwd
            except ValueError:
                next_cwd = working_directory
        return {"cwd": next_cwd.relative_to(project_root).as_posix(),
                "returncode": completed.returncode,
                "stdout": stdout[-80_000:], "stderr": completed.stderr[-80_000:]}

    def start_terminal(self, cwd: str = "", *, project_root: Path | None = None) -> dict:
        project_root = project_root or self.default_project_root
        working_directory = (self.project_file(cwd, allow_missing=False, root=project_root)
                             if cwd else project_root)
        if not working_directory.is_dir():
            raise NotADirectoryError(cwd)
        with self._terminal_lock:
            for session_id, session in self._terminals.items():
                if session["project"] == project_root and session["process"].poll() is None:
                    return {"session_id": session_id, "cwd": session["cwd"]}
            master, slave = pty.openpty()
            file_control.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", 32, 110, 0, 0))
            environment = os.environ.copy()
            environment["TERM"] = "xterm-256color"
            environment["PS1"] = "\\[\\e[1;32m\\]drugstudio:\\w\\$\\[\\e[0m\\] "
            environment["HISTFILE"] = "/dev/null"
            process = subprocess.Popen(["/bin/bash", "--noprofile", "--norc", "-i"],
                                       cwd=working_directory, stdin=slave, stdout=slave, stderr=slave,
                                       start_new_session=True, env=environment, close_fds=True)
            os.close(slave)
            os.set_blocking(master, False)
            session_id = uuid.uuid4().hex
            self._terminals[session_id] = {"process": process, "fd": master, "project": project_root,
                                           "cwd": working_directory.relative_to(project_root).as_posix(),
                                           "buffer": "", "base": 0}
            return {"session_id": session_id, "cwd": self._terminals[session_id]["cwd"]}

    def terminal_output(self, session_id: str, since: int = 0, *, project_root: Path | None = None) -> dict:
        with self._terminal_lock:
            session = self._terminal(session_id, project_root)
            chunks = []
            while select.select([session["fd"]], [], [], 0)[0]:
                try:
                    data = os.read(session["fd"], 65536)
                except (BlockingIOError, OSError):
                    break
                if not data:
                    break
                chunks.append(data.decode("utf-8", errors="replace"))
            if chunks:
                session["buffer"] += "".join(chunks)
                if len(session["buffer"]) > 1_000_000:
                    trim = len(session["buffer"]) - 1_000_000
                    session["buffer"] = session["buffer"][trim:]
                    session["base"] += trim
            try:
                cwd = Path(os.readlink(f"/proc/{session['process'].pid}/cwd")).resolve()
                session["cwd"] = cwd.relative_to(session["project"]).as_posix()
            except (OSError, ValueError):
                pass
            cursor = session["base"] + len(session["buffer"])
            reset = since < session["base"]
            start = 0 if reset else max(0, since - session["base"])
            running = session["process"].poll() is None
            result = {"data": session["buffer"][start:], "cursor": cursor,
                      "running": running, "reset": reset, "cwd": session["cwd"]}
            if not running:
                os.close(session["fd"])
                self._terminals.pop(session_id, None)
            return result

    def terminal_input(self, session_id: str, value: str, *, project_root: Path | None = None) -> dict:
        if len(value) > 20_000:
            raise ValueError("terminal input is too large")
        with self._terminal_lock:
            session = self._terminal(session_id, project_root)
            os.write(session["fd"], value.encode("utf-8"))
            return {"sent": len(value)}

    def resize_terminal(self, session_id: str, columns: int, rows: int, *, project_root: Path | None = None) -> dict:
        columns = max(20, min(int(columns), 300))
        rows = max(5, min(int(rows), 120))
        with self._terminal_lock:
            session = self._terminal(session_id, project_root)
            file_control.ioctl(session["fd"], termios.TIOCSWINSZ, struct.pack("HHHH", rows, columns, 0, 0))
            return {"columns": columns, "rows": rows}

    def stop_terminal(self, session_id: str, *, project_root: Path | None = None) -> dict:
        with self._terminal_lock:
            session = self._terminal(session_id, project_root)
            process = session["process"]
            if process.poll() is None:
                try:
                    os.killpg(process.pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            os.close(session["fd"])
            self._terminals.pop(session_id, None)
            return {"stopped": True}

    def close(self) -> None:
        for session_id in list(self._terminals):
            try:
                self.stop_terminal(session_id)
            except (OSError, ValueError):
                pass

    def _terminal(self, session_id: str, project_root: Path | None = None) -> dict:
        """Look up a session; with a project_root, also check it owns the session.

        Internal callers (shutdown) pass None because they act on every
        session regardless of which project opened it.
        """
        session = self._terminals.get(session_id)
        if session is None:
            raise ValueError("terminal session is missing")
        if project_root is not None and session["project"] != project_root:
            raise ValueError("terminal session belongs to another project")
        return session

    def chat(self, payload: dict, *, project_root: Path | None = None,
             run_root: Path | None = None) -> dict:
        with self._llm_config_lock:
            config = dict(self._llm_config)
        if not config["api_key"]:
            raise RuntimeError("AI chat needs an API key; configure it in model settings")
        project_root = project_root or self.default_project_root
        run_root = run_root or self.default_run_root
        project_name = project_root.name
        try:
            from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
            from langchain_core.tools import StructuredTool
            from langchain_openai import ChatOpenAI
        except ImportError as exc:
            raise RuntimeError("AI chat requires the DrugStudio LLM dependencies") from exc

        history = payload.get("messages")
        if not isinstance(history, list) or not history:
            raise ValueError("messages must contain the conversation")
        messages = [SystemMessage(content=(
            f"You are the DrugStudio research workbench assistant. The active project is {project_name!r}. "
            f"Project-relative file and command tools are rooted at {project_root}. "
            "Use tools to inspect actual files or run commands before making claims. "
            "Ask before destructive or expensive operations. Never expose credentials. "
            "Summarize each tool action and its result clearly. Reply in the user's language, preferably Chinese when the user writes Chinese."
        ))]
        for item in history[-24:]:
            if not isinstance(item, dict) or item.get("role") not in {"user", "assistant"}:
                continue
            content = str(item.get("content") or "")
            if len(content) > 20_000:
                content = content[:20_000]
            messages.append(HumanMessage(content=content) if item["role"] == "user" else AIMessage(content=content))
        actions = []

        def list_files(path: str = "") -> str:
            """List files and directories at a project-relative path."""
            return json.dumps(self.list_project_files(path, root=project_root), ensure_ascii=False)

        def read_file(path: str) -> str:
            """Read a UTF-8 project file smaller than 1 MiB."""
            return self.read_project_file(path, root=project_root)["content"]

        def write_file(path: str, content: str) -> str:
            """Write a UTF-8 file inside the active project."""
            return json.dumps(self.write_project_file(path, content, root=project_root), ensure_ascii=False)

        def run_command(command: str, cwd: str = "") -> str:
            """Run a shell command in the active project and return stdout/stderr."""
            result = self.run_project_command(command, cwd, redact_environment=True, root=project_root)
            actions.append({"tool": "run_command", "command": command, "returncode": result["returncode"]})
            return json.dumps(result, ensure_ascii=False)

        tools = [StructuredTool.from_function(list_files), StructuredTool.from_function(read_file),
                 StructuredTool.from_function(write_file), StructuredTool.from_function(run_command)]
        tool_map = {tool.name: tool for tool in tools}
        run_model = _public_model_info(run_root) or {}
        requested_model = str(payload.get("model") or config["model"] or run_model.get("model") or "gpt-4.1-mini").strip()
        if len(requested_model) > 160:
            raise ValueError("model name is too long")
        temperature = float(payload.get("temperature", 0.2))
        if not 0 <= temperature <= 2:
            raise ValueError("temperature must be between 0 and 2")
        model = ChatOpenAI(model=requested_model,
                           base_url=config["base_url"] or run_model.get("base_url") or None,
                           api_key=config["api_key"],
                           temperature=temperature,
                           timeout=90, max_retries=1)
        bound_model = model.bind_tools(tools, parallel_tool_calls=False)
        for _ in range(8):
            response = bound_model.invoke(messages)
            messages.append(response)
            calls = getattr(response, "tool_calls", []) or []
            if not calls:
                return {"reply": str(response.content), "actions": actions}
            for call in calls:
                tool = tool_map.get(call.get("name"))
                if tool is None:
                    result = "Unknown tool"
                else:
                    try:
                        result = tool.invoke(call.get("args", {}))
                        if call["name"] != "run_command":
                            actions.append({"tool": call["name"], "path": call.get("args", {}).get("path", "")})
                    except Exception as exc:
                        result = f"{type(exc).__name__}: {exc}"
                messages.append(ToolMessage(content=str(result)[:80_000], tool_call_id=call["id"]))
        return {"reply": "Reached the tool-call limit for one response. Continue with a follow-up.", "actions": actions}

    def manifest(self, run_root: Path | None = None) -> dict:
        root = run_root or self.default_run_root
        path = root / "manifest.json"
        if not path.is_file():
            raise FileNotFoundError(f"manifest.json not found under {root}")
        return json.loads(path.read_text(encoding="utf-8"))

    def snapshot(self, run_root: Path | None = None, project_root: Path | None = None) -> dict:
        run_root = run_root or self.default_run_root
        project_root = project_root or self.default_project_root
        raw = self.manifest(run_root)
        run_meta = self.ensure_run_metadata(run_root, raw)
        model_info = _public_model_info(run_root)
        artifacts = {
            stage: [
                {
                    "artifact_id": item.get("artifact_id"),
                    "kind": item.get("kind"),
                    "path": item.get("path"),
                    "sha256": item.get("sha256"),
                    "metadata": _small_metadata(item.get("metadata", {})),
                }
                for item in items
            ]
            for stage, items in raw.get("artifacts", {}).items()
        }
        report = _latest_json_artifact(run_root, raw, "assessment_data")
        model_config = self.model_config(run_root)
        return {
            "project": self.project_identity(project_root),
            "chat_available": model_config["api_key_configured"],
            "model_config": model_config,
            "run_key": self.run_key(run_root),
            "run_id": raw.get("run_id"),
            "run_meta": run_meta,
            "target": raw.get("target", {}),
            "ligands": raw.get("ligands", []),
            "policy": raw.get("policy", {}),
            "status": raw.get("status"),
            "current_node": raw.get("current_node"),
            "stage_results": _small_value(raw.get("stage_results", {})),
            "artifacts": artifacts,
            "tasks": raw.get("tasks", []),
            "approvals": raw.get("approvals", []),
            "human_decisions": raw.get("human_decisions", []),
            "overrides": raw.get("overrides", {}),
            "decisions": raw.get("decisions", [])[-30:],
            "failures": raw.get("failures", []),
            "report": report,
            "model": model_info,
            "costs": self.cost_summary(run_root),
            "control_request": self.events_for(run_root).control_request(),
            "server": {"workflow_running": self.workflow_running(run_root)},
        }

    def owning_project_id(self, run_root: Path, metadata: dict | None = None) -> str:
        """Which selectable project a run belongs to.

        The sidecar's own project_id is derived relative to runs_root, which is
        a different namespace from the project ids the UI uses (relative to
        workspace_root).  Walking up from the run directory reconciles the two,
        so run lists and project lists agree.
        """
        try:
            relative = run_root.resolve().relative_to(self.workspace_root)
        except ValueError:
            return str((metadata or {}).get("project_id") or ".")
        return relative.parts[0] if relative.parts else "."

    def list_runs(self, project_id: str = "", selected_run_root: Path | None = None) -> list[dict]:
        runs = []
        for manifest_path in sorted(self.runs_root.rglob("manifest.json")):
            path = manifest_path.parent
            try:
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            metadata = self.run_metadata(path, manifest)
            owner = self.owning_project_id(path, metadata)
            if project_id and project_id != "." and owner != project_id:
                continue
            runs.append({"run_key": self.run_key(path), "run_id": manifest.get("run_id", path.name),
                         "status": manifest.get("status"),
                         "target_id": manifest.get("target", {}).get("target_id"), "name": path.name,
                         "selected": selected_run_root is not None and path.resolve() == selected_run_root.resolve(),
                         "project_id": owner,
                         "conversation_id": metadata.get("conversation_id"),
                         "parent_run_id": metadata.get("parent_run_id"),
                         "created_at": metadata.get("created_at")})
        return runs

    def run_key(self, path: Path) -> str:
        return path.resolve().relative_to(self.runs_root).as_posix()

    def select_run(self, run_key: str) -> dict:
        """Validate a run the client is switching to.

        As with projects, the selection itself lives in the browser; the server
        only confirms the key resolves to a real run.
        """
        candidate = self.resolve_run(run_key)
        manifest = self.manifest(candidate)
        self.ensure_run_metadata(candidate, manifest)
        return {"selected": self.run_key(candidate), "run_id": manifest.get("run_id"),
                "project_id": self.owning_project_id(candidate)}

    def request_control(self, action: str, run_root: Path | None = None) -> dict:
        run_root = run_root or self.default_run_root
        if action not in {"pause", "cancel"}:
            raise ValueError("control action must be pause or cancel")
        if not self.workflow_running(run_root):
            raise RuntimeError("there is no active web-started workflow to control")
        events = self.events_for(run_root)
        events.set_control_request(action)
        event = events.emit("control_requested", status=f"{action}_requested", action=action)
        return {"status": f"{action}_requested", "event_id": event["event_id"]}

    def rerun_from(self, stage: str, run_root: Path | None = None) -> dict:
        run_root = run_root or self.default_run_root
        if stage not in {"structure", "receptor", "ligand", "pocket", "docking", "assessment"}:
            raise ValueError("unsupported rerun stage")
        if self.workflow_running(run_root):
            raise RuntimeError("wait for the active workflow to reach a safe checkpoint before branching")
        source = run_root
        lock_path = source / ".run.lock"
        with lock_path.open("a") as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise RuntimeError("another process is using this run") from exc
            stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
            branch = source.parent / f"{source.name}-rerun-{stage}-{stamp}-{uuid.uuid4().hex[:6]}"
            shutil.copytree(source, branch, copy_function=os.link,
                            ignore=shutil.ignore_patterns(".run.lock", "control.json", "events.lock"))
        state_path = branch / "manifest.json"
        state = json.loads(state_path.read_text(encoding="utf-8"))
        parent_run_id = state.get("run_id", source.name)
        state["run_id"] = branch.name
        order = ["structure", "receptor", "ligand", "pocket", "docking", "assessment"]
        selected = order.index(stage)
        invalidated = set(order[selected:])
        stage_aliases = {"structure": {"structure", "structure_prediction"},
                         "receptor": {"receptor", "receptor_repair"},
                         "ligand": {key for key in state.get("artifacts", {}) if key == "ligand" or key.startswith("ligand:")},
                         "pocket": {"pocket"}, "docking": {"docking"}, "assessment": {"assessment"}}
        remove_keys = set().union(*(stage_aliases[key] for key in invalidated))
        removed_paths = []
        for key in list(state.get("artifacts", {})):
            if key not in remove_keys:
                continue
            retained = []
            for artifact in state["artifacts"][key]:
                if artifact.get("path", "").startswith("inputs/") or artifact.get("kind") == "supplied_structure":
                    retained.append(artifact)
                else:
                    removed_paths.append(artifact.get("path"))
            if retained:
                state["artifacts"][key] = retained
            else:
                del state["artifacts"][key]
        referenced = {artifact.get("path") for items in state.get("artifacts", {}).values() for artifact in items}
        for relative in set(removed_paths) - referenced:
            if not relative or relative.startswith("inputs/"):
                continue
            target = (branch / relative).resolve()
            try:
                target.relative_to(branch.resolve())
                target.unlink(missing_ok=True)
            except (ValueError, OSError):
                continue
        for work_stage in order[selected:]:
            directory = branch / "work" / work_stage
            if work_stage == "ligand":
                for ligand_dir in (branch / "work" / "ligand").glob("*"):
                    if ligand_dir.is_dir():
                        shutil.rmtree(ligand_dir)
            elif directory.exists():
                shutil.rmtree(directory)
        for key in list(state.get("stage_results", {})):
            normalized = key.split(":", 1)[0]
            if normalized in invalidated:
                state["stage_results"][key] = {"status": "stale", "previous_status": state["stage_results"][key].get("status"),
                                                "rerun_from": stage}
        state["tasks"] = [] if selected <= order.index("docking") else state.get("tasks", [])
        state["status"] = "created"
        state["current_node"] = None
        state.setdefault("decisions", []).append({"agent": "human", "action": "branch_rerun",
            "stage": stage, "parent_run_id": parent_run_id, "timestamp": datetime.now(timezone.utc).isoformat()})
        replacement = branch / "manifest.next.json"
        replacement.write_text(json.dumps(state, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        os.replace(replacement, state_path)
        try:
            (branch / "logs" / "events.jsonl").unlink()
        except FileNotFoundError:
            pass
        for trace in (branch / "logs").glob("react-*.jsonl"):
            trace.unlink(missing_ok=True)
        branch_events = EventWriter(branch, branch.name)
        branch_events.emit("run_branched", status="created", stage=stage,
                           parent_run_id=parent_run_id, invalidated_stages=order[selected:])
        branch_manifest = json.loads(state_path.read_text(encoding="utf-8"))
        source_meta = self.run_metadata(source, json.loads((source / "manifest.json").read_text(encoding="utf-8")))
        save_run_metadata(branch, self.runs_root, branch_manifest,
                          project_id=source_meta.get("project_id"),
                          project_name=source_meta.get("project_name"),
                          conversation_id=source_meta.get("conversation_id"),
                          parent_run_id=parent_run_id)
        with self._metadata_lock:
            self._refresh_project_index_locked()
        return {"run_id": branch.name, "run_key": self.run_key(branch),
                "run_root": str(branch), "rerun_from": stage}

    def save_pricing(self, payload: dict, run_root: Path | None = None) -> dict:
        run_root = run_root or self.default_run_root
        model = str(payload.get("model") or "").strip()
        if not model:
            raise ValueError("model name is required")
        rates = {key: float(payload[key]) for key in ("input_usd_per_million", "output_usd_per_million")}
        if any(value < 0 or not math.isfinite(value) for value in rates.values()):
            raise ValueError("token rates must be non-negative")
        path = run_root / "inputs" / "model-pricing.json"
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            data = {}
        data[model] = rates
        path.parent.mkdir(parents=True, exist_ok=True)
        temp = path.with_suffix(".tmp")
        temp.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        os.replace(temp, path)
        self.events_for(run_root).emit("pricing_updated", model=model, **rates)
        return self.cost_summary(run_root)

    def _usage_from_traces(self, run_root: Path) -> dict[str, dict]:
        """Fall back to the ReAct traces for runs that predate usage events."""
        model_default = (_public_model_info(run_root) or {}).get("model", "unknown")
        models: dict[str, dict] = {}
        for trace in (run_root / "logs").glob("react-*.jsonl"):
            try:
                lines = trace.read_text(encoding="utf-8").splitlines()
            except OSError:
                continue
            for line in lines:
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if record.get("type") != "ai":
                    continue
                model = str(record.get("response_metadata", {}).get("model_name", model_default))
                usage = record.get("usage_metadata") or {}
                row = models.setdefault(model, {"calls": 0, "input_tokens": 0, "output_tokens": 0,
                                                "reported_calls": 0, "estimated_usd": None})
                row["calls"] += 1
                prompt = usage.get("input_tokens", usage.get("prompt_tokens"))
                completion = usage.get("output_tokens", usage.get("completion_tokens"))
                if prompt is None or completion is None:
                    continue
                row["reported_calls"] += 1
                row["input_tokens"] += int(prompt)
                row["output_tokens"] += int(completion)
        return models

    def cost_summary(self, run_root: Path | None = None) -> dict:
        run_root = run_root or self.default_run_root
        # Token totals are folded in as events are appended, so this no longer
        # re-parses events.jsonl on every /api/state.
        models: dict[str, dict] = {model: {**row, "estimated_usd": None}
                                   for model, row in self.events_for(run_root).usage_by_model().items()}
        if not models:
            models = self._usage_from_traces(run_root)
        try:
            pricing = json.loads((run_root / "inputs" / "model-pricing.json").read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            pricing = {}
        for model, row in models.items():
            rate = pricing.get(model)
            if rate and row["reported_calls"] == row["calls"] and row["reported_calls"]:
                row["estimated_usd"] = (row["input_tokens"] * rate["input_usd_per_million"] +
                                         row["output_tokens"] * rate["output_usd_per_million"]) / 1_000_000
        return {"models": models, "pricing": pricing, "total_calls": sum(row["calls"] for row in models.values()),
                "input_tokens": sum(row["input_tokens"] for row in models.values()),
                "output_tokens": sum(row["output_tokens"] for row in models.values()),
                "estimated_usd": (sum(row["estimated_usd"] for row in models.values()
                                       if row["estimated_usd"] is not None)
                                  if models and all(row["estimated_usd"] is not None for row in models.values()) else None)}

    def workflow_running(self, run_root: Path | None = None) -> bool:
        key = (run_root or self.default_run_root).resolve()
        with self._process_lock:
            process = self._processes.get(key)
            return process is not None and process.poll() is None

    def any_workflow_running(self) -> bool:
        with self._process_lock:
            return any(process.poll() is None for process in self._processes.values())

    def events_since(self, since: int = 0, run_root: Path | None = None) -> list[dict]:
        return self.events_for(run_root or self.default_run_root).read_since(since)

    def start_workflow(self, payload: dict, run_root: Path | None = None) -> dict:
        run_root = (run_root or self.default_run_root).resolve()
        with self._process_lock:
            process = self._processes.get(run_root)
            if process is not None and process.poll() is None:
                return {"status": "already_running"}
            current_status = self.manifest(run_root).get("status")
            if current_status == "cancelled":
                raise RuntimeError("this run was cancelled; create a stage rerun branch to continue")
            if current_status == "succeeded":
                return {"status": "already_complete"}
            prompt = str(payload.get("prompt") or
                         "Inspect the run, complete the remaining workflow stages, and summarize the evidence in Chinese.")
            model_info = _public_model_info(run_root) or {}
            model_config = self.model_config(run_root)
            model = payload.get("model") or model_config["model"]
            self.events_for(run_root).set_control_request(None)
            conversation_id = str(payload.get("conversation_id") or "").strip() or None
            if conversation_id:
                self.ensure_run_metadata(run_root, self.manifest(run_root),
                                         conversation_id=conversation_id)
            command = [sys.executable, "-m", "drugstudio.cli", "run",
                       "--run-root", str(run_root), "--prompt", prompt]
            if model:
                command.extend(["--model", str(model)])
            log_path = run_root / "logs" / f"web-run-{uuid.uuid4().hex}.log"
            log_path.parent.mkdir(parents=True, exist_ok=True)
            handle = log_path.open("x", encoding="utf-8")
            environment = os.environ.copy()
            with self._llm_config_lock:
                if self._llm_config["api_key"]:
                    environment["OPENAI_API_KEY"] = self._llm_config["api_key"]
                else:
                    environment.pop("OPENAI_API_KEY", None)
                if self._llm_config["base_url"]:
                    environment["OPENAI_BASE_URL"] = self._llm_config["base_url"]
                if self._llm_config["model"]:
                    environment["DOCK_AGENT_MODEL"] = self._llm_config["model"]
            if not environment.get("OPENAI_BASE_URL") and model_info.get("base_url"):
                environment["OPENAI_BASE_URL"] = str(model_info["base_url"])
            try:
                process = subprocess.Popen(command, cwd=self.workspace,
                                           stdout=handle, stderr=subprocess.STDOUT,
                                           text=True, env=environment)
                self._processes[run_root] = process
            finally:
                handle.close()
            return {"status": "started", "pid": process.pid,
                    "log_path": str(log_path.relative_to(run_root))}

    def record_review(self, payload: dict, run_root: Path | None = None) -> dict:
        run_root = run_root or self.default_run_root
        stage = str(payload.get("stage") or "")
        action = str(payload.get("action") or "")
        reason = str(payload.get("reason") or "").strip()
        actor = str(payload.get("actor") or "web-reviewer").strip()
        if stage not in {"docking_box", "receptor"}:
            raise ValueError("unsupported review stage")
        if action not in {"approve", "select", "override", "reject"}:
            raise ValueError("unsupported review action")
        if not reason:
            raise ValueError("a scientific reason is required")
        command = [sys.executable, "-m", "drugstudio.cli", "review",
                   "--run-root", str(run_root), "--stage", stage,
                   "--action", action, "--reason", reason, "--actor", actor]
        pocket_rank = payload.get("pocket_rank")
        center = payload.get("center")
        size = payload.get("size")
        if pocket_rank is not None:
            command.extend(["--pocket-rank", str(int(pocket_rank))])
        if center is not None or size is not None:
            if not isinstance(center, list) or not isinstance(size, list) or len(center) != 3 or len(size) != 3:
                raise ValueError("center and size must each contain three values")
            command.extend(["--center", *[str(float(value)) for value in center],
                            "--size", *[str(float(value)) for value in size]])
        completed = subprocess.run(command, cwd=self.workspace, text=True,
                                   capture_output=True, env=os.environ.copy(), timeout=30)
        if completed.returncode:
            raise RuntimeError(completed.stderr.strip() or completed.stdout.strip() or "review command failed")
        return json.loads(completed.stdout)

    def file_path(self, relative: str, run_root: Path | None = None) -> Path:
        run_root = run_root or self.default_run_root
        candidate = (run_root / relative).resolve()
        try:
            candidate.relative_to(run_root)
        except ValueError as exc:
            raise ValueError("file path escapes the selected run") from exc
        if not candidate.is_file():
            raise FileNotFoundError(relative)
        return candidate


class Handler(BaseHTTPRequestHandler):
    server_version = "DockAgentWeb/0.1"

    @property
    def app(self) -> WebApplication:
        return self.server.app  # type: ignore[attr-defined]

    def _reject_foreign_host(self) -> bool:
        """Refuse requests whose Host header this server was not bound for."""
        if self.app.host_allowed(self.headers.get("Host", "")):
            return False
        self._json({"error": "unrecognized Host header"}, status=HTTPStatus.MISDIRECTED_REQUEST)
        return True

    def _run_scope(self, source: dict) -> Path:
        """Resolve the run this request addresses, falling back to the default.

        Scope travels with the request so two browser tabs can sit on
        different runs without overwriting one another.
        """
        value = source.get("run_key", source.get("run_id", ""))
        if isinstance(value, list):
            value = value[0] if value else ""
        return self.app.resolve_run(str(value or ""))

    def _project_scope(self, source: dict) -> Path:
        value = source.get("project_id", "")
        if isinstance(value, list):
            value = value[0] if value else ""
        return self.app.resolve_project(str(value or ""))

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        if self._reject_foreign_host():
            return
        try:
            if parsed.path == "/api/auth":
                self._json({"required": self.app.web_token is not None, "authorized": self._authorized()})
                return
            if parsed.path.startswith("/api/") and parsed.path not in {"/api/health", "/api/auth"} and not self._authorized():
                self._json({"error": "authentication required"}, status=HTTPStatus.UNAUTHORIZED)
                return
            query = parse_qs(parsed.query)
            if parsed.path == "/api/state":
                run_root = self._run_scope(query)
                self._json(self.app.snapshot(run_root, self._project_scope(query)))
            elif parsed.path == "/api/model-config":
                self._json(self.app.model_config(self._run_scope(query)))
            elif parsed.path == "/api/runs":
                self._json({"runs": self.app.list_runs(query.get("project_id", [""])[0],
                                                       self._run_scope(query))})
            elif parsed.path == "/api/projects":
                project_root = self._project_scope(query)
                self._json({"projects": self.app.list_projects(project_root),
                            "selected": self.app.project_identity(project_root),
                            "workbench_projects": self.app.workbench_projects()})
            elif parsed.path == "/api/provenance":
                self._json(self.app.provenance(
                    self._run_scope(query),
                    artifact_id=query.get("artifact_id", [""])[0],
                    path=query.get("path", [""])[0],
                    stage=query.get("stage", [""])[0]))
            elif parsed.path == "/api/chats":
                self._json(self.app.list_chats(query.get("project_id", [""])[0]))
            elif parsed.path == "/api/project/files":
                self._json(self.app.list_project_files(query.get("path", [""])[0],
                                                       root=self._project_scope(query)))
            elif parsed.path == "/api/project/file":
                self._json(self.app.read_project_file(query.get("path", [""])[0],
                                                      root=self._project_scope(query)))
            elif parsed.path == "/api/project/terminal/output":
                self._json(self.app.terminal_output(query.get("session_id", [""])[0],
                                                    int(query.get("since", ["0"])[0]),
                                                    project_root=self._project_scope(query)))
            elif parsed.path == "/api/events":
                self._events(parsed)
            elif parsed.path == "/api/file":
                relative = query.get("path", [""])[0]
                self._file(self.app.file_path(relative, self._run_scope(query)))
            elif parsed.path == "/api/health":
                self._json({"status": "ok", "workflow_running": self.app.any_workflow_running()})
            else:
                self._static(parsed.path)
        except Exception as exc:
            self._json({"error": f"{type(exc).__name__}: {exc}"}, status=HTTPStatus.BAD_REQUEST)

    def do_POST(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        if self._reject_foreign_host():
            return
        try:
            payload = self._read_json()
            if parsed.path == "/api/auth":
                if self.app.web_token and hmac.compare_digest(str(payload.get("token") or ""), self.app.web_token):
                    secure = "; Secure" if self.headers.get("X-Forwarded-Proto", "").lower() == "https" else ""
                    session_id = self.app.open_session()
                    cookie = f"drugstudio_session={session_id}; HttpOnly; SameSite=Strict; Path=/; Max-Age=86400{secure}"
                    self._json({"authorized": True}, headers={"Set-Cookie": cookie})
                else:
                    self._json({"error": "invalid access token"}, status=HTTPStatus.UNAUTHORIZED)
            elif parsed.path.startswith("/api/") and not self._authorized():
                self._json({"error": "authentication required"}, status=HTTPStatus.UNAUTHORIZED)
            elif parsed.path == "/api/select-project":
                self._json(self.app.select_project(str(payload.get("project_id") or "")))
            elif parsed.path == "/api/model-config":
                self._json(self.app.update_model_config(payload))
            elif parsed.path == "/api/chats":
                self._json(self.app.create_chat(str(payload.get("project_id") or "")), status=HTTPStatus.CREATED)
            elif parsed.path == "/api/chats/save":
                messages = payload.get("messages")
                if not isinstance(messages, list):
                    raise ValueError("messages must be a list")
                self._json(self.app.save_chat(str(payload.get("project_id") or ""),
                                              str(payload.get("chat_id") or ""), messages))
            elif parsed.path == "/api/chats/archive":
                self._json(self.app.archive_chat(
                    str(payload.get("project_id") or ""),
                    str(payload.get("chat_id") or ""),
                    bool(payload.get("archived", True))))
            elif parsed.path == "/api/project/file":
                self._json(self.app.write_project_file(str(payload.get("path") or ""), str(payload.get("content") or ""),
                                                       root=self._project_scope(payload)))
            elif parsed.path == "/api/project/terminal":
                self._json(self.app.run_project_command(str(payload.get("command") or ""), str(payload.get("cwd") or ""),
                                                        root=self._project_scope(payload)))
            elif parsed.path == "/api/project/terminal/start":
                self._json(self.app.start_terminal(str(payload.get("cwd") or ""),
                                                   project_root=self._project_scope(payload)))
            elif parsed.path == "/api/project/terminal/input":
                self._json(self.app.terminal_input(str(payload.get("session_id") or ""), str(payload.get("input") or ""),
                                                   project_root=self._project_scope(payload)))
            elif parsed.path == "/api/project/terminal/resize":
                self._json(self.app.resize_terminal(str(payload.get("session_id") or ""), int(payload.get("columns") or 110), int(payload.get("rows") or 32),
                                                    project_root=self._project_scope(payload)))
            elif parsed.path == "/api/project/terminal/stop":
                self._json(self.app.stop_terminal(str(payload.get("session_id") or ""),
                                                  project_root=self._project_scope(payload)))
            elif parsed.path == "/api/project/chat":
                self._json(self.app.chat(payload, project_root=self._project_scope(payload),
                                         run_root=self._run_scope(payload)))
            elif parsed.path == "/api/continue":
                self._json(self.app.start_workflow(payload, self._run_scope(payload)), status=HTTPStatus.ACCEPTED)
            elif parsed.path == "/api/control":
                self._json(self.app.request_control(str(payload.get("action") or ""), self._run_scope(payload)), status=HTTPStatus.ACCEPTED)
            elif parsed.path == "/api/select-run":
                self._json(self.app.select_run(str(payload.get("run_key") or payload.get("run_id") or "")))
            elif parsed.path == "/api/rerun":
                self._json(self.app.rerun_from(str(payload.get("stage") or ""), self._run_scope(payload)), status=HTTPStatus.ACCEPTED)
            elif parsed.path == "/api/pricing":
                self._json(self.app.save_pricing(payload, self._run_scope(payload)))
            elif parsed.path == "/api/review":
                self._json(self.app.record_review(payload, self._run_scope(payload)))
            else:
                self._json({"error": "unknown endpoint"}, status=HTTPStatus.NOT_FOUND)
        except Exception as exc:
            self._json({"error": f"{type(exc).__name__}: {exc}"}, status=HTTPStatus.BAD_REQUEST)

    def _read_json(self) -> dict:
        length = int(self.headers.get("Content-Length", "0"))
        if length > 1_000_000:
            raise ValueError("request body is too large")
        value = json.loads(self.rfile.read(length) or b"{}")
        if not isinstance(value, dict):
            raise ValueError("request body must be a JSON object")
        return value

    def _authorized(self) -> bool:
        if not self.app.web_token:
            return True
        cookie = SimpleCookie()
        try:
            cookie.load(self.headers.get("Cookie", ""))
            session = cookie.get("drugstudio_session")
            return session is not None and self.app.session_valid(session.value)
        except Exception:
            return False

    def _events(self, parsed) -> None:
        query = parse_qs(parsed.query)
        run_root = self._run_scope(query)
        try:
            since = int(query.get("since", [self.headers.get("Last-Event-ID", "0")])[0] or 0)
        except ValueError:
            since = 0
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.end_headers()
        deadline = __import__("time").monotonic() + 25.0
        try:
            while __import__("time").monotonic() < deadline:
                events = self.app.events_since(since, run_root)
                for event in events:
                    event_id = int(event.get("event_id", since))
                    self.wfile.write(f"id: {event_id}\nevent: {event.get('type', 'message')}\ndata: ".encode("utf-8"))
                    self.wfile.write((json.dumps(event, ensure_ascii=False) + "\n\n").encode("utf-8"))
                    self.wfile.flush()
                    since = max(since, event_id)
                if not events:
                    self.wfile.write(b": heartbeat\n\n")
                    self.wfile.flush()
                __import__("time").sleep(0.5)
        except (BrokenPipeError, ConnectionResetError):
            return

    def _static(self, path: str) -> None:
        relative = "index.html" if path in {"", "/"} else path.lstrip("/")
        if relative.startswith("static/"):
            relative = relative.removeprefix("static/")
        candidate = (STATIC_ROOT / relative).resolve()
        try:
            candidate.relative_to(STATIC_ROOT.resolve())
        except ValueError as exc:
            raise FileNotFoundError(path) from exc
        self._file(candidate, inline=True)

    def _file(self, path: Path, *, inline: bool = False) -> None:
        content = path.read_bytes()
        content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(content)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        if not inline:
            # Run artifacts are untrusted output from external tools.  Serving
            # an .html or .svg artifact inline on this origin would let it read
            # the session cookie, so everything but our own static assets is
            # handed to the browser as a download.  The 3D viewers and editor
            # read these through fetch(), which is unaffected.
            self.send_header("Content-Disposition", f'attachment; filename="{path.name}"')
            self.send_header("Content-Security-Policy", "sandbox; default-src 'none'")
        self.end_headers()
        self.wfile.write(content)

    def _json(self, value: dict, *, status: HTTPStatus = HTTPStatus.OK,
              headers: dict[str, str] | None = None) -> None:
        content = json.dumps(value, ensure_ascii=False, allow_nan=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(content)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        for key, item in (headers or {}).items():
            self.send_header(key, item)
        self.end_headers()
        self.wfile.write(content)

    def log_message(self, format: str, *args: object) -> None:
        print(f"[web] {self.address_string()} - {format % args}", flush=True)


def _latest_json_artifact(run_root: Path, manifest: dict, kind: str) -> dict | None:
    matches = [item for items in manifest.get("artifacts", {}).values() for item in items if item.get("kind") == kind]
    for item in reversed(matches):
        path = (run_root / str(item.get("path", ""))).resolve()
        try:
            path.relative_to(run_root.resolve())
            if path.is_file():
                return json.loads(path.read_text(encoding="utf-8"))
        except (ValueError, OSError, json.JSONDecodeError):
            continue
    return None


def _public_model_info(run_root: Path) -> dict | None:
    path = run_root / "inputs" / "llm-config-public.json"
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return {key: value[key] for key in ("model", "base_url", "live_llm") if key in value}


def _small_metadata(value: object) -> object:
    if not isinstance(value, dict):
        return value
    selected = {}
    for key, item in value.items():
        if key in {"stdout", "stderr", "builder_output", "request", "molsetup", "template", "log"}:
            continue
        selected[str(key)] = _small_value(item)
    return selected


def _small_value(value: object, depth: int = 0) -> object:
    if depth > 7:
        return "[detail omitted]"
    if isinstance(value, dict):
        entries = list(value.items())[:80]
        return {str(key): _small_value(item, depth + 1) for key, item in entries}
    if isinstance(value, list):
        return [_small_value(item, depth + 1) for item in value[:100]]
    if isinstance(value, str) and len(value) > 4000:
        return value[:4000] + " [truncated]"
    return value


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Serve the persistent DrugStudio workbench")
    scope = parser.add_mutually_exclusive_group(required=True)
    scope.add_argument("--run-root", help="legacy mode: run directory; sibling runs are discoverable")
    scope.add_argument("--runs-root", help="workspace directory; discover run manifests recursively")
    parser.add_argument("--workspace-root", help="parent directory containing selectable project folders")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--allowed-host", action="append", default=[],
                        help="extra Host header value to accept, e.g. workbench.lab:443 "
                             "(repeatable; required when serving behind a proxy)")
    return parser


def _allowed_hosts(host: str, port: int, extra: list[str]) -> set[str]:
    """Host header values this server answers to.

    Anything else is refused, which is what stops a page on another origin
    from reaching a loopback bind by resolving its own name to 127.0.0.1.
    """
    names = {"127.0.0.1", "localhost", "[::1]", "::1"}
    if host not in {"0.0.0.0", "::", ""}:
        names.add(host)
        if ":" in host and not host.startswith("["):
            names.add(f"[{host}]")
    allowed = {f"{name}:{port}" for name in names}
    allowed |= names if port in {80, 443} else set()
    allowed |= {item.strip().lower() for item in extra if item.strip()}
    return allowed


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    runs_root = Path(args.runs_root).expanduser().resolve() if args.runs_root else None
    run_root = Path(args.run_root).expanduser().resolve() if args.run_root else None
    if runs_root:
        candidates = list(runs_root.rglob("manifest.json"))
        if not candidates:
            raise FileNotFoundError(f"no run manifest found under {runs_root}")
        run_root = max(candidates, key=lambda item: item.stat().st_mtime_ns).parent
    else:
        runs_root = run_root.parent
    # The workbench no longer lives inside the user's workspace, so derive the
    # subprocess working directory from the configured roots instead of from
    # the installed package location.
    workspace_root = (Path(args.workspace_root).expanduser().resolve()
                      if args.workspace_root else runs_root.parent)
    workspace = workspace_root
    web_token = os.environ.get("DOCK_AGENT_WEB_TOKEN")
    try:
        loopback_bind = ipaddress.ip_address(args.host).is_loopback
    except ValueError:
        loopback_bind = args.host.lower() == "localhost"
    if not loopback_bind and not web_token:
        web_token = secrets.token_urlsafe(32)
    app = WebApplication(run_root, workspace, runs_root, workspace_root, web_token)
    app.allowed_hosts = _allowed_hosts(args.host, args.port, args.allowed_host)
    app.manifest()
    httpd = ThreadingHTTPServer((args.host, args.port), Handler)
    httpd.app = app  # type: ignore[attr-defined]
    print(f"DrugStudio workbench: http://{args.host}:{args.port}/", flush=True)
    print(f"Runs root: {runs_root}", flush=True)
    print(f"Workspace root: {workspace_root}", flush=True)
    print(f"Selected run: {run_root}", flush=True)
    if web_token:
        print(f"Web access token: {web_token}", flush=True)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        return 0
    finally:
        app.close()
        httpd.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
