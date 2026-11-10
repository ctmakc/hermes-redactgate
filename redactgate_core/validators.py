"""Pure check-digit / format validators for regulated identifiers.

Every validator is a pure function ``(str) -> bool``. They are referenced *by name*
from the YAML jurisdiction packs (the ``validator`` key on a pattern) and resolved at
load time by ``regex_packs.py``. A validator receives the raw regex match text; it MUST
strip its own separators (spaces / hyphens) before checking.

SECURITY: validators never log, print, or persist their input.
"""

from __future__ import annotations

import re
from collections.abc import Callable

Validator = Callable[[str], bool]

_NON_DIGIT_RE = re.compile(r"[\s-]")


def _digits(s: str) -> str:
    """Strip spaces and hyphens, returning only what remains (kept as-is otherwise)."""
    return _NON_DIGIT_RE.sub("", s)


def luhn(s: str) -> bool:
    """Luhn (mod-10) check over the digits in ``s``.

    Used for credit cards and (over 9 digits) the Canadian SIN. Returns False unless the
    cleaned string is all digits and length >= 2.
    """
    cleaned = _digits(s)
    if len(cleaned) < 2 or not cleaned.isdigit():
        return False
    total = 0
    # Walk right-to-left; double every second digit.
    for i, ch in enumerate(reversed(cleaned)):
        d = ord(ch) - 48
        if i % 2 == 1:
            d *= 2
            if d > 9:
                d -= 9
        total += d
    return total % 10 == 0


def sin_check(s: str) -> bool:
    """Canadian Social Insurance Number: exactly 9 digits passing the Luhn checksum."""
    cleaned = _digits(s)
    if len(cleaned) != 9 or not cleaned.isdigit():
        return False
    # A SIN never starts with 0 or 8 in the real allocation scheme.
    if cleaned[0] in {"0", "8"}:
        return False
    return luhn(cleaned)


# IBAN: move the 4 leading chars to the end, map letters A..Z -> 10..35, mod-97 == 1.
_IBAN_RE = re.compile(r"^[A-Z]{2}[0-9]{2}[A-Z0-9]{1,30}$")


def iban_mod97(s: str) -> bool:
    """ISO 13616 IBAN check: structural shape + mod-97 == 1 over the rearranged string."""
    cleaned = _digits(s).upper()
    if not _IBAN_RE.match(cleaned):
        return False
    rearranged = cleaned[4:] + cleaned[:4]
    converted_chars: list[str] = []
    for ch in rearranged:
        if ch.isdigit():
            converted_chars.append(ch)
        else:
            converted_chars.append(str(ord(ch) - 55))  # 'A' (65) -> 10
    try:
        return int("".join(converted_chars)) % 97 == 1
    except ValueError:  # pragma: no cover - guarded by the regex above
        return False


def ssn_check(s: str) -> bool:
    """US SSN format sanity: 9 digits, none of the never-issued area/group/serial codes."""
    cleaned = _digits(s)
    if len(cleaned) != 9 or not cleaned.isdigit():
        return False
    area, group, serial = cleaned[:3], cleaned[3:5], cleaned[5:]
    if area in {"000", "666"} or area[0] == "9":
        return False
    if group == "00" or serial == "0000":
        return False
    return True


def ein_check(s: str) -> bool:
    """US EIN format sanity: 9 digits with a valid IRS campus prefix (first two digits)."""
    cleaned = _digits(s)
    if len(cleaned) != 9 or not cleaned.isdigit():
        return False
    invalid_prefixes = {"07", "08", "09", "17", "18", "19", "28", "29", "49", "78", "79", "89"}
    return cleaned[:2] not in invalid_prefixes


def itin_check(s: str) -> bool:
    """US ITIN format sanity: 9 digits starting with 9; group digit in the IRS range."""
    cleaned = _digits(s)
    if len(cleaned) != 9 or not cleaned.isdigit():
        return False
    if cleaned[0] != "9":
        return False
    group = int(cleaned[3:5])
    # ITIN group ranges historically allocated by the IRS.
    return 50 <= group <= 65 or 70 <= group <= 88 or 90 <= group <= 92 or 94 <= group <= 99


def edrpou_check(s: str) -> bool:
    """Ukrainian ЄДРПОУ (legal-entity code): exactly 8 digits."""
    cleaned = _digits(s)
    return len(cleaned) == 8 and cleaned.isdigit()


def ipn_check(s: str) -> bool:
    """Ukrainian ІПН / РНОКПП (individual tax number): exactly 10 digits."""
    cleaned = _digits(s)
    return len(cleaned) == 10 and cleaned.isdigit()


def uci_check(s: str) -> bool:
    """IRCC UCI / client ID: 8 to 10 digits."""
    cleaned = _digits(s)
    return 8 <= len(cleaned) <= 10 and cleaned.isdigit()


# Registry consumed by regex_packs.py to resolve a pattern's ``validator`` name.
VALIDATORS: dict[str, Validator] = {
    "luhn": luhn,
    "sin_check": sin_check,
    "iban_mod97": iban_mod97,
    "ssn_check": ssn_check,
    "ein_check": ein_check,
    "itin_check": itin_check,
    "edrpou_check": edrpou_check,
    "ipn_check": ipn_check,
    "uci_check": uci_check,
}


def get_validator(name: str) -> Validator | None:
    """Resolve a validator function by its pack-referenced name, or None if unknown."""
    return VALIDATORS.get(name)
