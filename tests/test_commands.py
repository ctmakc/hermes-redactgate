import json

from redactgate_plugin.commands import make_handler


def _handler(engine, store):
    api = {
        "add": lambda t: store.append(t) or engine.set_terms(tuple(store)),
        "remove": lambda t: (store.remove(t) or engine.set_terms(tuple(store)) or True) if t in store else False,
        "list": lambda: list(store),
    }
    return make_handler(lambda: engine, api)


def test_status_last_test_audit_verify_terms(engine):
    store: list[str] = []
    h = _handler(engine, store)
    assert "RedactGate is ON" in h("")
    assert "No model request yet" in h("last", session_id="s")
    engine.on_llm_request({"messages": [{"role": "user", "content": "email jane.doe@acme-corp.com now"}]},
                          session_id="s", base_url="https://api.example.com")
    last = h("last", session_id="s")
    assert "api.example.com" in last and "[[EMAIL_" in last and "jane.doe@acme-corp.com" not in last
    t = h("test call +1 (613) 555-0142")
    assert "[[PHONE_" in t and "555-0142" not in t
    assert "llm_request" in h("audit 5")
    assert h("verify").startswith("✓")
    assert "Added" in h("terms add Nightjar")
    assert store == ["Nightjar"]
    assert "Nightjar" not in h("terms")  # masked in listings
    assert h("terms rm Nightjar") == "Removed."
    assert "/redact last" in h("help")


def test_last_never_prints_raw_values(engine):
    h = _handler(engine, [])
    engine.on_llm_request({"messages": [{"role": "user", "content": "SIN 130 692 544, card 4111 1111 1111 1111"}]}, session_id="z")
    out = h("last", session_id="z")
    assert "130 692 544" not in out and "4111 1111 1111 1111" not in out
    json.dumps(out)
