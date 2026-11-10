"""EntitySpan — the detection contract shared by the vendored RedactGate modules.

Trimmed copy of ``app/schemas/entities.py`` from github.com/ctmakc/redactgate
(only the span type the detectors emit).
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(slots=True)
class EntitySpan:
    """A detected sensitive entity within a single text string.

    ``start``/``end`` are character offsets (Python slice semantics) into the text the
    detector was given. ``entity_type`` is an UPPER_SNAKE code (e.g. SIN, IBAN, PERSON).
    """

    start: int
    end: int
    entity_type: str
    text: str
    score: float = 1.0
    source: str = "regex"
    jurisdiction: str | None = None

    @property
    def length(self) -> int:
        return self.end - self.start

    def overlaps(self, other: "EntitySpan") -> bool:
        return self.start < other.end and other.start < self.end
