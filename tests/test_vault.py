from redactgate_plugin.vault import SessionVault, VaultRegistry, canonical, restore


def test_same_value_same_session_same_placeholder():
    reg = VaultRegistry(b"s" * 32)
    a = reg.get("sess-1").token("EMAIL", "jane@acme.com")
    b = reg.get("sess-1").token("EMAIL", "JANE@acme.com ")
    assert a == b and a.startswith("[[EMAIL_") and len(a) == len("[[EMAIL_abcdef]]")


def test_placeholder_survives_restart_but_not_across_sessions():
    a = VaultRegistry(b"s" * 32).get("sess-1").token("PHONE", "+1 613 555 0142")
    b = VaultRegistry(b"s" * 32).get("sess-1").token("PHONE", "(613) 555-0142".replace("(", "+1 ("))
    c = VaultRegistry(b"s" * 32).get("sess-2").token("PHONE", "+1 613 555 0142")
    assert a == b  # restart: same secret, same session, same digits
    assert a != c  # another session: unlinkable


def test_canonical_digits_and_iban():
    assert canonical("CREDIT_CARD", "4111-1111 1111 1111") == "4111111111111111"
    assert canonical("IBAN", "de89 3704 0044") == "DE8937040044"


def test_restore_tolerates_model_mangling_but_only_known_placeholders():
    v = SessionVault(key=b"x" * 32)
    tok = v.token("PERSON", "Jane Doe")
    hexpart = tok[len("[[PERSON_"):-2]
    text = f"Hi {tok}, [PERSON_{hexpart}] and person_{hexpart.upper()} and [[ PERSON_{hexpart} ]]; [[PERSON_000000]] stays"
    out, n = restore(text, v)
    assert n == 4
    assert out.count("Jane Doe") == 4
    assert "[[PERSON_000000]]" in out


def test_restore_does_not_touch_similar_identifiers():
    v = SessionVault(key=b"x" * 32)
    tok = v.token("EMAIL", "a@b.co")
    hexpart = tok[len("[[EMAIL_"):-2]
    text = f"commit_{hexpart}ff and EMAIL_{hexpart}00 stay"
    out, n = restore(text, v)
    assert n == 0 and out == text


def test_registry_alias_and_drop():
    reg = VaultRegistry(b"s" * 32)
    tok = reg.get("old").token("EMAIL", "a@b.co")
    reg.alias("old", "new")
    out, n = restore(tok, reg.peek("new"))
    assert (out, n) == ("a@b.co", 1)
    assert reg.drop("old") and reg.peek("old") is None
