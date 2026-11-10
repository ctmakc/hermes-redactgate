"""Reversible placeholders: ``Jane Doe`` -> ``[[PERSON_3fa9c2]]`` and back.

Design (new for the Hermes plugin):

* A placeholder is ``[[TYPE_xxxxxx]]`` where ``xxxxxx`` = first 6 hex of
  HMAC-SHA256(profile_secret, session_key + type + canonical(value)).
  - same value, same session  -> same placeholder, on every request and after a restart,
    so the provider prompt-cache prefix stays stable and old tool-call arguments in history
    still resolve;
  - same value, other session -> different placeholder (no cross-session linkability).
* Raw values live **only in process memory** (``SessionVault``). Nothing with PII is
  written to disk: Hermes keeps the raw transcript locally, and every outgoing request is
  re-tokenized from it, so the map rebuilds itself after a restart.
* Restore is tolerant of the ways models mangle placeholders (single brackets, lower-case
  type, spaces), but only ever substitutes placeholders this session actually issued.
"""

from __future__ import annotations

import hashlib
import hmac
import re
import threading
from collections import OrderedDict
from dataclasses import dataclass, field

from .redactgate_core.entities import EntitySpan

# Types that share one identity space for canonicalisation (digits-only comparison).
_DIGIT_TYPES = {
    "PHONE", "CREDIT_CARD", "SIN", "SSN", "ITIN", "EIN", "IPN", "EDRPOU", "UCI",
    "BUSINESS_NUMBER", "GST_HST", "NEQ", "APPLICATION_NUMBER", "BANK_ACCOUNT",
}

STRICT_RE = re.compile(r"\[\[([A-Z][A-Z0-9_]*)_([0-9a-f]{6})\]\]")
# [[TYPE_hex]], [TYPE_hex], TYPE_hex, with optional inner spaces / lower-case type.
LOOSE_RE = re.compile(r"\[{0,2}\s*(?<![A-Za-z0-9])([A-Za-z][A-Za-z0-9_]*?)_([0-9a-fA-F]{6})(?![0-9A-Za-z])\s*\]{0,2}")


def canonical(entity_type: str, value: str) -> str:
    v = value.strip()
    if entity_type in _DIGIT_TYPES:
        d = "".join(ch for ch in v if ch.isdigit())
        return d or v.casefold()
    if entity_type in ("IBAN", "IBAN_UA", "VAT"):
        return re.sub(r"\s+", "", v).upper()
    return re.sub(r"\s+", " ", v).casefold()


def mask_value(value: str) -> str:
    """Human hint for /redact views: keeps first/last char, hides the rest."""
    v = value.strip()
    if len(v) <= 4:
        return "•" * len(v)
    if "@" in v:
        user, _, dom = v.partition("@")
        return f"{user[:1]}•••@{dom[:1]}•••"
    return f"{v[:1]}{'•' * min(len(v) - 2, 8)}{v[-1:]}"


@dataclass
class SessionVault:
    """Placeholder <-> value map for one session, memory only."""

    key: bytes
    by_token: "OrderedDict[str, tuple[str, str]]" = field(default_factory=OrderedDict)  # tok -> (type, value)
    max_entries: int = 20000
    lock: threading.Lock = field(default_factory=threading.Lock)
    # Names learned in this session (surface -> type): a name detected once, e.g. after a
    # cue word in the user message, is hidden wherever it shows up later, e.g. bare in a
    # tool result. Memory only, like by_token.
    known: dict[str, str] = field(default_factory=dict)
    known_version: int = 0
    _known_re: "tuple[int, re.Pattern[str] | None, re.Pattern[str] | None]" = (-1, None, None)

    def learn(self, entity_type: str, surface: str) -> None:
        surface = surface.strip()
        if len(surface) < 3 or surface in self.known:
            return
        with self.lock:
            if len(self.known) < 5000:
                self.known[surface] = entity_type
                self.known_version += 1

    def known_patterns(self) -> "tuple[re.Pattern[str] | None, re.Pattern[str] | None]":
        """(case-sensitive names regex, case-insensitive terms regex) for learned values."""
        ver, cs, ci = self._known_re
        if ver == self.known_version:
            return cs, ci
        with self.lock:
            items = list(self.known.items())
        names = sorted({k for k, t in items if t != "TERM"}, key=len, reverse=True)
        terms = sorted({k for k, t in items if t == "TERM"}, key=len, reverse=True)
        cs = re.compile(r"(?<![\w\[])(?:" + "|".join(map(re.escape, names)) + r")(?![\w\]])") if names else None
        ci = re.compile(r"(?<![\w\[])(?:" + "|".join(map(re.escape, terms)) + r")(?![\w\]])", re.IGNORECASE) if terms else None
        self._known_re = (self.known_version, cs, ci)
        return cs, ci

    def known_type(self, surface: str) -> str | None:
        t = self.known.get(surface)
        if t is not None:
            return t
        low = surface.casefold()
        for k, v in self.known.items():
            if v == "TERM" and k.casefold() == low:
                return v
        return None

    def token(self, entity_type: str, value: str) -> str:
        canon = canonical(entity_type, value)
        digest = hmac.new(self.key, f"{entity_type}\x00{canon}".encode(), hashlib.sha256).hexdigest()[:6]
        tok = f"[[{entity_type}_{digest}]]"
        with self.lock:
            if tok not in self.by_token:
                self.by_token[tok] = (entity_type, value.strip())
                if len(self.by_token) > self.max_entries:
                    self.by_token.popitem(last=False)
            else:
                self.by_token.move_to_end(tok)
        return tok

    def lookup(self, entity_type: str, hexpart: str) -> str | None:
        tok = f"[[{entity_type.upper()}_{hexpart.lower()}]]"
        with self.lock:
            hit = self.by_token.get(tok)
        return hit[1] if hit else None

    def __len__(self) -> int:
        return len(self.by_token)


class VaultRegistry:
    """Per-session vaults, keyed by Hermes session id. Memory only, LRU-bounded."""

    def __init__(self, secret: bytes, max_sessions: int = 256):
        self._secret = secret
        self._max = max_sessions
        self._vaults: "OrderedDict[str, SessionVault]" = OrderedDict()
        self._lock = threading.Lock()

    def _derive(self, session_key: str) -> bytes:
        return hmac.new(self._secret, b"session\x00" + session_key.encode(), hashlib.sha256).digest()

    def get(self, session_key: str) -> SessionVault:
        session_key = session_key or "_default"
        with self._lock:
            v = self._vaults.get(session_key)
            if v is None:
                v = SessionVault(key=self._derive(session_key))
                self._vaults[session_key] = v
                if len(self._vaults) > self._max:
                    self._vaults.popitem(last=False)
            else:
                self._vaults.move_to_end(session_key)
            return v

    def peek(self, session_key: str) -> SessionVault | None:
        with self._lock:
            return self._vaults.get(session_key or "_default")

    def alias(self, old: str, new: str) -> None:
        """After compaction the session id changes; keep resolving the old placeholders."""
        with self._lock:
            src = self._vaults.get(old)
            if src is None:
                return
        dst = self.get(new)
        with src.lock:
            items = list(src.by_token.items())
        with src.lock:
            known = dict(src.known)
        with dst.lock:
            for k, v in items:
                dst.by_token.setdefault(k, v)
            for k, v in known.items():
                dst.known.setdefault(k, v)
            dst.known_version += 1

    def drop(self, session_key: str) -> bool:
        with self._lock:
            return self._vaults.pop(session_key or "_default", None) is not None

    def sessions(self) -> int:
        with self._lock:
            return len(self._vaults)


@dataclass
class TokenizeResult:
    text: str
    spans: list[EntitySpan]


def tokenize(text: str, spans: list[EntitySpan], vault: SessionVault) -> str:
    """Replace each span (offsets into *text*, non-overlapping) with its placeholder."""
    if not spans:
        return text
    parts: list[str] = []
    pos = 0
    for s in sorted(spans, key=lambda x: x.start):
        if s.start < pos:
            continue
        parts.append(text[pos:s.start])
        parts.append(vault.token(s.entity_type, text[s.start:s.end]))
        pos = s.end
    parts.append(text[pos:])
    return "".join(parts)


def restore(text: str, vault: SessionVault | None) -> tuple[str, int]:
    """Swap issued placeholders back to real values. Returns (text, n_restored)."""
    if not text or vault is None or not len(vault):
        return text, 0
    count = 0

    def _sub(m: re.Match[str]) -> str:
        nonlocal count
        val = vault.lookup(m.group(1), m.group(2))
        if val is None:
            return m.group(0)
        count += 1
        return val

    out = LOOSE_RE.sub(_sub, text)
    return out, count


def find_placeholders(text: str) -> list[str]:
    return [m.group(0) for m in STRICT_RE.finditer(text or "")]
