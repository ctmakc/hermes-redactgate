"""Tamper-evident audit journal: what was hidden, where, never the values.

Each line of ``audit.jsonl`` is one JSON event with ``prev`` (hash of the previous line)
and ``hash`` = SHA-256 over the canonical JSON of the event without ``hash``. Editing,
inserting or deleting a line breaks the chain at that point; ``verify()`` reports where.
(Someone who rewrites the whole tail can recompute it — the chain shows tampering, it does
not prevent it.) Events carry entity *type counts*, tool names, provider host and a
truncated hash of the session id. Raw values and placeholders are never written.
"""

from __future__ import annotations

import hashlib
import json
import os
import threading
import time
from pathlib import Path
from typing import Any

GENESIS = "0" * 64


def _digest(event: dict[str, Any]) -> str:
    body = {k: v for k, v in event.items() if k != "hash"}
    return hashlib.sha256(json.dumps(body, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def session_ref(session_id: str) -> str:
    return hashlib.sha256((session_id or "").encode()).hexdigest()[:12] if session_id else ""


class AuditLog:
    def __init__(self, path: Path, max_bytes: int = 20 * 1024 * 1024):
        self.path = Path(path)
        self.max_bytes = max_bytes
        self._lock = threading.Lock()
        self._head: str | None = None

    def _load_head(self) -> str:
        if self._head is not None:
            return self._head
        head = GENESIS
        if self.path.is_file():
            try:
                with self.path.open("rb") as fh:
                    fh.seek(0, os.SEEK_END)
                    size = fh.tell()
                    fh.seek(max(0, size - 65536))
                    lines = fh.read().splitlines()
                for raw in reversed(lines):
                    raw = raw.strip()
                    if raw:
                        head = json.loads(raw).get("hash", GENESIS)
                        break
            except (OSError, ValueError):
                head = GENESIS
        self._head = head
        return head

    def _rotate_if_needed(self) -> None:
        try:
            if self.path.is_file() and self.path.stat().st_size > self.max_bytes:
                rotated = self.path.with_suffix(f".{int(time.time())}.jsonl")
                self.path.rename(rotated)
                # the new file starts by pointing at the rotated file's head
        except OSError:
            pass

    def append(self, kind: str, **fields: Any) -> dict[str, Any]:
        with self._lock:
            self._rotate_if_needed()
            prev = self._load_head()
            event: dict[str, Any] = {"ts": round(time.time(), 3), "kind": kind, **fields, "prev": prev}
            event["hash"] = _digest(event)
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(event, sort_keys=True, separators=(",", ":")) + "\n")
            self._head = event["hash"]
            return event

    def tail(self, n: int = 10) -> list[dict[str, Any]]:
        if not self.path.is_file():
            return []
        try:
            lines = self.path.read_text(encoding="utf-8").splitlines()
        except OSError:
            return []
        out = []
        for raw in lines[-n:]:
            try:
                out.append(json.loads(raw))
            except ValueError:
                out.append({"kind": "corrupt-line"})
        return out

    def verify(self) -> tuple[bool, int, str]:
        """(ok, n_events, message)."""
        if not self.path.is_file():
            return True, 0, "no events yet"
        prev = None
        n = 0
        with self.path.open(encoding="utf-8") as fh:
            for lineno, raw in enumerate(fh, 1):
                raw = raw.strip()
                if not raw:
                    continue
                try:
                    ev = json.loads(raw)
                except ValueError:
                    return False, n, f"line {lineno}: not JSON"
                if prev is not None and ev.get("prev") != prev:
                    return False, n, f"line {lineno}: chain broken (prev does not match line {lineno - 1})"
                if _digest(ev) != ev.get("hash"):
                    return False, n, f"line {lineno}: content edited (hash mismatch)"
                prev = ev["hash"]
                n += 1
        return True, n, f"{n} events, chain intact"
