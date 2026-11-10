"""Detection front-end: RedactGate packs + rule NER + chat names + user terms.

New for the Hermes plugin:

* **Offset-preserving normalization.** RedactGate's server forwards NFKC-normalized text.
  Inside an agent that would silently rewrite code and file contents, so here detection runs
  on a normalized *view* (NFKC, zero-width / soft-hyphen stripped) and every hit is mapped
  back to the original offsets. Only the PII span is replaced; the rest of the text is
  byte-for-byte untouched, while full-width digits or a zero-width space inside a card number
  still get caught.
* **Existing placeholders are opaque.** ``[[EMAIL_3fa9c2]]`` is never re-detected.
* **User terms** (project code names, client names) are matched case-insensitively.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable
from dataclasses import dataclass, field

from .names_en import detect_people_en
from .redactgate_core import ner_rules
from .redactgate_core.entities import EntitySpan
from .redactgate_core.merge import merge_spans
from .redactgate_core.regex_packs import Pack, available_pack_codes, load_packs

DEFAULT_PACKS = ("GENERIC", "CA", "US", "EU", "UA", "IRCC")

_INVISIBLE = frozenset({0x200B, 0x200C, 0x200D, 0x2060, 0xFEFF, 0x00AD, 0x180E})

_INVISIBLE_CHARS = tuple(chr(c) for c in _INVISIBLE)

PLACEHOLDER_RE = re.compile(r"\[\[[A-Z][A-Z0-9_]*_[0-9a-f]{6}\]\]")

# Types produced by the rule NER that are too noisy for free-form agent traffic.
_RULE_TYPES_KEEP = {"PERSON", "ORG", "ADDRESS", "BANK_ACCOUNT", "PASSPORT", "CREDIT_CARD", "PHONE"}


@dataclass(slots=True)
class NormView:
    text: str
    # index in normalized text -> index in original text
    to_orig: list[int] = field(default_factory=list)
    identity: bool = True


def normalized_view(text: str) -> NormView:
    """NFKC + invisible-strip view of *text* with an offset map back to the original."""
    if text.isascii():
        return NormView(text=text, identity=True)
    if unicodedata.is_normalized("NFKC", text) and not any(ch in text for ch in _INVISIBLE_CHARS):
        return NormView(text=text, identity=True)
    out: list[str] = []
    to_orig: list[int] = []
    for i, ch in enumerate(text):
        if ord(ch) in _INVISIBLE:
            continue
        n = unicodedata.normalize("NFKC", ch) if ord(ch) > 127 else ch
        for c in n:
            out.append(c)
            to_orig.append(i)
    return NormView(text="".join(out), to_orig=to_orig, identity=False)


def _map_back(view: NormView, start: int, end: int) -> tuple[int, int]:
    if view.identity:
        return start, end
    if end <= start or start >= len(view.to_orig):
        return start, start
    return view.to_orig[start], view.to_orig[end - 1] + 1


@dataclass(slots=True)
class DetectorConfig:
    packs: tuple[str, ...] = DEFAULT_PACKS
    names: bool = True
    rule_ner: bool = True
    terms: tuple[str, ...] = ()
    allow: tuple[str, ...] = ()
    ignore_types: tuple[str, ...] = ()
    min_score: float = 0.6


class Detector:
    """Finds PII spans. Thread-safe after construction (no mutable state)."""

    def __init__(self, cfg: DetectorConfig | None = None):
        self.cfg = cfg or DetectorConfig()
        codes = [c.upper() for c in self.cfg.packs]
        if "ALL" in codes:
            codes = available_pack_codes()
        if "GENERIC" not in codes:
            codes = ["GENERIC", *codes]
        self.packs: list[Pack] = load_packs(codes)
        self.pack_codes = [p.code for p in self.packs]
        self._ignore = {t.upper() for t in self.cfg.ignore_types}
        self._allow = {a.casefold() for a in self.cfg.allow if a}
        terms = sorted({t.strip() for t in self.cfg.terms if t and len(t.strip()) >= 2}, key=len, reverse=True)
        self._terms_re = (
            re.compile(r"(?<!\w)(?:" + "|".join(re.escape(t) for t in terms) + r")(?!\w)", re.IGNORECASE)
            if terms
            else None
        )

    def find(self, text: str) -> list[EntitySpan]:
        """Non-overlapping PII spans with offsets into the ORIGINAL ``text``."""
        if not text or len(text) < 3:
            return []
        view = normalized_view(text)
        nt = view.text
        raw: list[EntitySpan] = []
        for pack in self.packs:
            raw.extend(pack.detect(nt))
        if self.cfg.rule_ner:
            raw.extend(s for s in ner_rules.detect_all(nt) if s.entity_type in _RULE_TYPES_KEEP)
        if self.cfg.names:
            raw.extend(detect_people_en(nt))
        if self._terms_re is not None:
            for m in self._terms_re.finditer(nt):
                raw.append(EntitySpan(m.start(), m.end(), "TERM", m.group(0), 0.99, "manual"))

        blocked = [(m.start(), m.end()) for m in PLACEHOLDER_RE.finditer(nt)]
        kept: list[EntitySpan] = []
        for s in raw:
            if s.score < self.cfg.min_score or s.entity_type.upper() in self._ignore:
                continue
            value = s.text.strip()
            if len(value) < 2 or value.casefold() in self._allow:
                continue
            if s.entity_type in ("PERSON", "ORG") and ("\n" in s.text or "," in s.text):
                continue
            if any(s.start < b1 and b0 < s.end for b0, b1 in blocked):
                continue
            kept.append(s)
        merged = merge_spans(kept)

        out: list[EntitySpan] = []
        for s in merged:
            a, b = _map_back(view, s.start, s.end)
            # strip surrounding whitespace the pattern may have eaten
            while a < b and text[a].isspace():
                a += 1
            while b > a and text[b - 1].isspace():
                b -= 1
            if b <= a:
                continue
            out.append(EntitySpan(a, b, s.entity_type, text[a:b], s.score, s.source, s.jurisdiction))
        return out

    def counts(self, texts: Iterable[str]) -> dict[str, int]:
        c: dict[str, int] = {}
        for t in texts:
            for s in self.find(t):
                c[s.entity_type] = c.get(s.entity_type, 0) + 1
        return c
