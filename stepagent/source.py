"""Read-only session discovery and bounded, incremental JSONL tailing."""
from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from pathlib import Path


def expand_path(value: str | Path) -> Path:
    """Expand user/environment notation using the host OS, preserving spaces."""
    return Path(os.path.expandvars(str(value))).expanduser()


def default_sessions_dir() -> Path:
    """Resolve configuration at invocation time; never depend on a checkout."""
    explicit = os.environ.get("CODEX_SESSIONS_DIR")
    if explicit:
        return expand_path(explicit)
    home = os.environ.get("CODEX_HOME")
    return (expand_path(home) if home else Path.home() / ".codex") / "sessions"


@dataclass(frozen=True)
class Session:
    path: Path
    session_id: str
    title: str
    cwd: str
    mtime: float
    size: int
    source: str = ""

    @property
    def recent(self) -> bool:
        return time.time() - self.mtime < 120


class SessionCatalog:
    def __init__(self, root: Path):
        self.root = root
        self.cache: dict[Path, tuple[tuple[int, int], Session]] = {}
        self.error = ""

    def scan(self) -> list[Session]:
        self.error = ""
        if not self.root.is_dir():
            self.error = f"Brak katalogu sesji: {self.root}"
            return []
        titles = {}
        try:
            with (self.root.parent / "session_index.jsonl").open(encoding="utf-8") as stream:
                for line in stream:
                    try:
                        value = json.loads(line)
                        if isinstance(value, dict):
                            titles[value.get("id")] = str(value.get("thread_name") or "")
                    except (ValueError, TypeError):
                        continue
        except OSError:
            pass
        sessions = []
        live_paths = set()
        try:
            paths = self.root.rglob("*.jsonl")
            for path in paths:
                if path.name.startswith("._"):
                    continue
                try:
                    stat = path.stat()
                    live_paths.add(path)
                    signature = (stat.st_ino, stat.st_size)
                    cached = self.cache.get(path)
                    # Header data is immutable during ordinary appends.
                    if cached and cached[0][0] == signature[0] and signature[1] >= cached[0][1] and cached[1].session_id != path.stem:
                        old = cached[1]
                        session = Session(path, old.session_id, titles.get(old.session_id) or old.title, old.cwd, stat.st_mtime, stat.st_size, old.source)
                    else:
                        meta = {}
                        prompt = ""
                        with path.open("rb") as stream:
                            head = stream.read(256 * 1024)
                        for line in head.splitlines():
                            try:
                                record = json.loads(line)
                            except (ValueError, UnicodeDecodeError):
                                continue
                            if not isinstance(record, dict):
                                continue
                            payload = record.get("payload", record)
                            if not isinstance(payload, dict):
                                continue
                            if record.get("type") == "session_meta":
                                meta = payload
                            if payload.get("type") == "user_message" and not prompt:
                                prompt = str(payload.get("message", ""))[:160]
                        sid = str(meta.get("id") or meta.get("session_id") or path.stem)
                        session = Session(path, sid, titles.get(sid) or prompt or path.stem, str(meta.get("cwd", "")), stat.st_mtime, stat.st_size, str(meta.get("source", "")))
                    self.cache[path] = (signature, session)
                    sessions.append(session)
                except OSError as exc:
                    self.error = f"Nie można odczytać części sesji: {exc}"
        except OSError as exc:
            self.error = str(exc)
        self.cache = {p: v for p, v in self.cache.items() if p in live_paths}
        return sorted(sessions, key=lambda s: s.mtime, reverse=True)


class JsonlTail:
    """Never consume an unfinished line, including partial UTF-8 characters."""
    chunk_size = 512 * 1024

    def __init__(self, path: Path):
        self.path = path
        self.offset = 0
        self.buffer = bytearray()
        self.identity = None
        self.bad_lines = 0
        self.line_number = 0
        self.error = ""
        self.mtime = 0.0
        self.size = 0

    def poll(self) -> tuple[list[dict], bool]:
        self.error = ""
        try:
            with self.path.open("rb") as stream:
                stat = os.fstat(stream.fileno())
                identity = (stat.st_dev, stat.st_ino)
                reset = self.identity is not None and (identity != self.identity or stat.st_size < self.offset)
                if reset:
                    self.offset = 0
                    self.buffer = bytearray()
                    self.bad_lines = self.line_number = 0
                self.identity = identity
                self.mtime, self.size = stat.st_mtime, stat.st_size
                stream.seek(self.offset)
                chunk = stream.read(self.chunk_size)
                self.offset += len(chunk)
        except OSError as exc:
            self.error = f"Oczekiwanie na plik: {exc}"
            return [], False
        # Extend unfinished records in place: no quadratic copying or size
        # cutoff for image payloads, large tool outputs, or partial UTF-8.
        lines = chunk.split(b"\n")
        if len(lines) == 1:
            self.buffer.extend(chunk)
            return [], reset
        self.buffer.extend(lines[0])
        lines[0] = self.buffer
        self.buffer = bytearray(lines.pop())
        records = []
        for line in lines:
            self.line_number += 1
            if not line.strip():
                continue
            try:
                record = json.loads(line)
                if not isinstance(record, dict):
                    raise ValueError("record must be an object")
                records.append(record)
            except (ValueError, UnicodeDecodeError):
                self.bad_lines += 1
        return records, reset
