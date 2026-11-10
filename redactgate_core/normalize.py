"""Text normalization applied before detection.

SECURITY: without this, an attacker evades the ASCII-oriented regex packs with Unicode
look-alikes — full-width digits (``１３０``), a NBSP/zero-width char inside a SIN/card
number, etc. — and the raw value sails through to the upstream LLM. Normalizing to NFKC
and stripping zero-width/format characters folds those tricks back to the canonical form
the packs match. The normalized text is what gets tokenized and forwarded, so the upstream
never sees the evasion form either.
"""

from __future__ import annotations

import unicodedata

# Zero-width / invisible format characters that can be injected mid-token to split a match.
_INVISIBLE = {
    0x200B: None,  # zero-width space
    0x200C: None,  # zero-width non-joiner
    0x200D: None,  # zero-width joiner
    0x2060: None,  # word joiner
    0xFEFF: None,  # zero-width no-break space / BOM
    0x00AD: None,  # soft hyphen
}


def normalize_text(text: str) -> str:
    """NFKC-normalize and strip invisible format chars (idempotent, never raises).

    NFKC folds full-width/compatibility forms to ASCII and converts NBSP (U+00A0) and
    other Unicode spaces to a regular space, so the jurisdiction regexes see canonical
    separators. Invisible characters are removed outright.
    """
    if not text:
        return text
    normalized = unicodedata.normalize("NFKC", text)
    return normalized.translate(_INVISIBLE)
