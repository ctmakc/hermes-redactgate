import pytest
from redactgate_plugin.detect import Detector, DetectorConfig, normalized_view


def types(text, **cfg):
    return [(s.entity_type, s.text) for s in Detector(DetectorConfig(**cfg)).find(text)]


@pytest.mark.parametrize(
    "text,etype,value",
    [
        ("write to jane.doe@acme-corp.com today", "EMAIL", "jane.doe@acme-corp.com"),
        ("call me at +1 (613) 555-0142 tomorrow", "PHONE", "+1 (613) 555-0142"),
        ("card 4111 1111 1111 1111 exp 12/29", "CREDIT_CARD", "4111 1111 1111 1111"),
        ("my SIN is 130 692 544", "SIN", "130 692 544"),
        ("SSN 123-45-6789 on file", "SSN", "123-45-6789"),
        ("pay to DE89 3704 0044 0532 0130 00 please", "IBAN", "DE89 3704 0044 0532 0130 00"),
        ("server at 10.20.30.40 is down", "IP_ADDRESS", "10.20.30.40"),
    ],
)
def test_regulated_identifiers(text, etype, value):
    assert (etype, value) in types(text)


def test_luhn_rejects_random_digits():
    assert not [t for t in types("order 4111 1111 1111 1112 shipped") if t[0] == "CREDIT_CARD"]


def test_sin_checksum_rejects_invalid():
    assert not [t for t in types("ref 130 692 545") if t[0] == "SIN"]


@pytest.mark.parametrize(
    "text,name",
    [
        ("Email Jane Doe about the lease", "Jane Doe"),
        ("my client Omar Haddad wants a refund", "Omar Haddad"),
        ("Dr. Patel signed the form", "Patel"),
        ("Dear Mr. Okafor, thanks for writing", "Okafor"),
        ("My name is Priya Raman.", "Priya Raman"),
    ],
)
def test_person_names_in_chat(text, name):
    assert ("PERSON", name) in types(text)


@pytest.mark.parametrize(
    "text",
    [
        "Run the tests and fix the failing import in main.py",
        "The Monday meeting moved to Friday at 10am.",
        "def parse(line):\n    return line.split(',')",
        "Use Python 3.12 and FastAPI 0.115 for the API layer.",
        "Dear team, the release is ready.",
    ],
)
def test_no_false_positives_on_ordinary_text(text):
    assert [t for t in types(text) if t[0] in ("PERSON", "EMAIL", "PHONE", "CREDIT_CARD", "SIN")] == []


def test_fullwidth_and_zero_width_evasion_is_caught_and_text_outside_is_untouched():
    text = "ｃａｆé note: card 4111​1111 1111 1111 ok"
    spans = Detector().find(text)
    cards = [s for s in spans if s.entity_type == "CREDIT_CARD"]
    assert len(cards) == 1
    s = cards[0]
    assert text[s.start : s.end] == "4111​1111 1111 1111"
    # characters outside the span keep their original (full-width) form
    assert text[: s.start].startswith("ｃａｆé")


def test_fullwidth_digits_email():
    text = "reach ｊａｎｅ@example.com now"
    spans = Detector().find(text)
    assert [s.entity_type for s in spans] == ["EMAIL"]
    assert text[spans[0].start : spans[0].end] == "ｊａｎｅ@example.com"


def test_normalized_view_maps_offsets():
    v = normalized_view("a​b１")
    assert v.text == "ab1"
    assert v.to_orig == [0, 2, 3]


def test_existing_placeholders_are_opaque():
    assert types("send [[EMAIL_3fa9c2]] to [[PERSON_00aa11]]") == []


def test_custom_terms_and_allow_and_ignore():
    assert ("TERM", "Project Nightjar") in types("status of Project Nightjar?", terms=("Project Nightjar",))
    assert types("mail support@mmix.ca", allow=("support@mmix.ca",)) == []
    assert types("host 10.0.0.1", ignore_types=("IP_ADDRESS",)) == []


@pytest.mark.parametrize("text", ["TDD: RED-GREEN-REFACTOR", "set DEBUG=1 and FRANCE", "ATTENTION please"])
def test_vat_needs_digits(text):
    assert [t for t in types(text) if t[0] == "VAT"] == []


def test_real_vat_still_detected():
    assert ("VAT", "DE123456789") in types("Supplier VAT DE123456789 on the invoice")


def test_generic_iban_requires_checksum():
    assert [t for t in types("part number GB12 ABCD 1234 5678 90") if t[0].startswith("IBAN")] == []
