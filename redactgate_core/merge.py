"""Span overlap resolution.

The multi-pass detector (regex packs, Presidio NER, LLM-NER) produces a flat list of
:class:`~app.schemas.entities.EntitySpan` objects that may overlap or nest. ``merge_spans``
reduces that to a clean, start-sorted, non-overlapping list by picking a single winner for
each overlapping cluster.

Pure, no I/O, no optional dependencies.
"""

from __future__ import annotations

from .entities import EntitySpan

# Source priority — higher wins. Regulated-identifier regex packs are the most
# trustworthy signal, then the LLM-NER pass, then generic Presidio NER. Unknown
# sources fall to the bottom.
_SOURCE_PRIORITY: dict[str, int] = {
    "manual": 4,
    "regex": 3,
    "llm": 2,
    "presidio": 1,
}


def _source_rank(source: str) -> int:
    return _SOURCE_PRIORITY.get(source, 0)


def _better(a: EntitySpan, b: EntitySpan) -> EntitySpan:
    """Return the winner between two overlapping spans.

    Ranking, in order: higher score, then longer span, then higher source priority.
    Deterministic and total so a cluster always collapses to one winner.
    """
    a_key = (a.score, a.length, _source_rank(a.source))
    b_key = (b.score, b.length, _source_rank(b.source))
    return a if a_key >= b_key else b


def merge_spans(spans: list[EntitySpan]) -> list[EntitySpan]:
    """Resolve overlaps and return a start-sorted, non-overlapping list of spans.

    When two spans overlap (including full containment), the winner is chosen by
    ``(score desc, length desc, source priority regex>llm>presidio)`` and the loser is
    dropped. Spans fully contained in a kept span are dropped. Identical-position
    duplicates collapse to one.
    """
    if not spans:
        return []

    # Stable order: by start, then by descending "quality" so the strongest candidate in
    # a cluster is encountered first.
    ordered = sorted(
        spans,
        key=lambda s: (s.start, -s.score, -s.length, -_source_rank(s.source)),
    )

    kept: list[EntitySpan] = []
    for span in ordered:
        # Compare against the spans already accepted; only the tail can overlap because
        # the input is start-sorted, but nested/earlier-starting winners can still cover
        # this span, so scan back while there is positional overlap.
        replaced = False
        drop = False
        for i in range(len(kept) - 1, -1, -1):
            existing = kept[i]
            if existing.end <= span.start:
                # No earlier kept span can overlap once we pass a non-overlapping one,
                # because kept stays start-sorted and end-bounded.
                break
            if not existing.overlaps(span):
                continue
            winner = _better(existing, span)
            if winner is existing:
                drop = True
                break
            # New span beats this kept one — remove the loser and keep scanning, since
            # the new span may dominate several smaller kept spans.
            kept.pop(i)
            replaced = True
        if drop:
            continue
        kept.append(span)
        if replaced:
            kept.sort(key=lambda s: s.start)

    kept.sort(key=lambda s: (s.start, s.end))
    return kept
