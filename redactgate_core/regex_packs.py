"""Regex-only jurisdiction detection — the zero-dependency detection floor.

A *pack* is a YAML file under ``app/packs/`` describing regulated-identifier patterns for
one jurisdiction (CA, US, EU, UA, IRCC, ...). This module loads those files, compiles
their patterns, resolves any named validator from ``validators.py``, and emits
``EntitySpan`` objects. It MUST work with zero optional dependencies (no presidio/spacy):
only PyYAML and the stdlib are required.

SECURITY: raw matched values live only inside ``EntitySpan.text`` for downstream
tokenization; this module never logs, prints, or persists them.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from functools import cache
from pathlib import Path
from typing import Any

import json

from .validators import Validator, get_validator
from .entities import EntitySpan

# app/packs lives one directory up from app/redaction.
PACKS_DIR = Path(__file__).resolve().parent / "packs"

_FLAG_MAP: dict[str, int] = {
    "IGNORECASE": re.IGNORECASE,
    "I": re.IGNORECASE,
    "MULTILINE": re.MULTILINE,
    "M": re.MULTILINE,
    "DOTALL": re.DOTALL,
    "S": re.DOTALL,
    "UNICODE": re.UNICODE,
    "U": re.UNICODE,
    "VERBOSE": re.VERBOSE,
    "X": re.VERBOSE,
}


def _parse_flags(spec: str | None) -> int:
    """Translate a pack's ``flags`` string (e.g. ``"IGNORECASE"`` or ``"I|M"``) to a bitmask."""
    if not spec:
        return 0
    flags = 0
    for token in re.split(r"[|, ]+", spec.strip()):
        if not token:
            continue
        flags |= _FLAG_MAP.get(token.upper(), 0)
    return flags


@dataclass(slots=True)
class CompiledPattern:
    """A single compiled detection rule within a pack."""

    entity_type: str
    regex: re.Pattern[str]
    validator: Validator | None = None
    validator_name: str | None = None
    description: str | None = None


@dataclass(slots=True)
class Pack:
    """A loaded jurisdiction pack: metadata plus compiled detection patterns."""

    code: str
    name: str
    entity_types: list[str]
    patterns: list[CompiledPattern] = field(default_factory=list)
    version: str = "1.0.0"
    # Raw pattern dicts as authored in YAML — used by the DB loader for ``definition``.
    raw_patterns: list[dict[str, Any]] = field(default_factory=list)

    def detect(self, text: str) -> list[EntitySpan]:
        """Run every pattern over ``text`` and emit validated ``EntitySpan`` matches.

        If a pattern defines a named group ``value``, the emitted span and the validator
        input cover ONLY that group — so a keyword-anchored rule (e.g. ``ІПН 1234567890``)
        redacts and validates just the identifier, never the surrounding cue word.
        """
        spans: list[EntitySpan] = []
        for pat in self.patterns:
            for m in pat.regex.finditer(text):
                has_value = "value" in pat.regex.groupindex and m.group("value") is not None
                if has_value:
                    start, end = m.span("value")
                    value = m.group("value")
                else:
                    start, end = m.start(), m.end()
                    value = m.group(0)
                if pat.validator is not None and not pat.validator(value):
                    continue
                spans.append(
                    EntitySpan(
                        start=start,
                        end=end,
                        entity_type=pat.entity_type,
                        text=value,
                        score=0.95,
                        source="regex",
                        jurisdiction=self.code,
                    )
                )
        return spans

    def meta(self) -> dict[str, Any]:
        """Serializable metadata for persistence (no compiled objects)."""
        return {
            "code": self.code,
            "name": self.name,
            "entity_types": list(self.entity_types),
            "version": self.version,
            "definition": {"patterns": [dict(p) for p in self.raw_patterns]},
        }


def _compile_pack(data: dict[str, Any]) -> Pack:
    """Build a :class:`Pack` from a parsed YAML mapping."""
    code = str(data["code"]).strip().upper()
    name = str(data.get("name", code))
    entity_types = [str(t) for t in (data.get("entity_types") or [])]
    version = str(data.get("version", "1.0.0"))
    raw_patterns: list[dict[str, Any]] = list(data.get("patterns") or [])

    compiled: list[CompiledPattern] = []
    for raw in raw_patterns:
        validator_name = raw.get("validator")
        validator = get_validator(validator_name) if validator_name else None
        compiled.append(
            CompiledPattern(
                entity_type=str(raw["type"]),
                regex=re.compile(str(raw["regex"]), _parse_flags(raw.get("flags"))),
                validator=validator,
                validator_name=validator_name,
                description=raw.get("description"),
            )
        )
    return Pack(
        code=code,
        name=name,
        entity_types=entity_types,
        patterns=compiled,
        version=version,
        raw_patterns=raw_patterns,
    )


@cache
def _load_pack(code: str) -> Pack | None:
    """Load and compile a single pack by code (cached). Returns None if no file exists."""
    path = PACKS_DIR / f"{code.lower()}.json"
    if not path.is_file():
        return None
    with path.open("r", encoding="utf-8") as fh:
        data = json.load(fh) or {}
    return _compile_pack(data)


def load_packs(codes: list[str]) -> list[Pack]:
    """Load the named jurisdiction packs (case-insensitive). Unknown codes are skipped."""
    packs: list[Pack] = []
    seen: set[str] = set()
    for code in codes:
        norm = code.strip().upper()
        if not norm or norm in seen:
            continue
        seen.add(norm)
        pack = _load_pack(norm)
        if pack is not None:
            packs.append(pack)
    return packs


def available_pack_codes() -> list[str]:
    """Discover all pack codes shipped under ``app/packs/`` (by filename)."""
    if not PACKS_DIR.is_dir():
        return []
    return sorted(p.stem.upper() for p in PACKS_DIR.glob("*.json"))


def all_pack_meta() -> list[dict[str, Any]]:
    """Return serializable metadata for every shipped pack (consumed by the DB loader)."""
    return [pack.meta() for pack in load_packs(available_pack_codes())]


def clear_cache() -> None:
    """Drop the compiled-pack cache (used in tests when packs change on disk)."""
    _load_pack.cache_clear()
