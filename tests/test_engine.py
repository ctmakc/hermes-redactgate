import json

from redactgate_plugin.engine import WITHHELD

PII = {
    "name": "Jane Doe",
    "email": "jane.doe@acme-corp.com",
    "phone": "+1 (613) 555-0142",
    "card": "4111 1111 1111 1111",
    "sin": "130 692 544",
}

USER = (
    f"My client {PII['name']} ({PII['email']}, {PII['phone']}) disputes a charge on card "
    f"{PII['card']}. Her SIN is {PII['sin']}. Draft a reply and save it to reply.txt."
)


def chat_request():
    return {
        "model": "m",
        "messages": [
            {"role": "system", "content": f"Memory: user works with {PII['email']}"},
            {"role": "user", "content": USER},
            {"role": "assistant", "content": None, "tool_calls": [{"id": "c1", "type": "function", "function": {
                "name": "terminal", "arguments": json.dumps({"command": f"grep '{PII['phone']}' crm.csv"})}}]},
            {"role": "tool", "tool_call_id": "c1", "content": f"row: {PII['name']},{PII['card']}"},
        ],
    }


def test_no_raw_value_leaves_in_any_field(engine):
    res = engine.on_llm_request(chat_request(), session_id="s1", provider="openrouter", base_url="https://openrouter.ai/api/v1")
    wire = json.dumps(res["request"], ensure_ascii=False)
    for v in PII.values():
        assert v not in wire, v
    assert "[[EMAIL_" in wire and "[[PERSON_" in wire and "[[CREDIT_CARD_" in wire and "[[SIN_" in wire
    # same email in system prompt and user message -> one placeholder
    sys_ph = [w for w in res["request"]["messages"][0]["content"].split() if w.startswith("[[EMAIL_")][0]
    assert sys_ph in res["request"]["messages"][1]["content"]
    assert res["source"] == "redactgate"


def test_reply_is_restored_for_the_user(engine):
    res = engine.on_llm_request(chat_request(), session_id="s1")
    tokenized_user = res["request"]["messages"][1]["content"]
    reply = "Draft: Dear " + tokenized_user.split("My client ")[1].split(" (")[0] + ", we reviewed your card."
    out = engine.on_llm_output(reply, session_id="s1")
    assert out == "Draft: Dear Jane Doe, we reviewed your card."


def test_reply_without_placeholders_is_left_to_other_plugins(engine):
    engine.on_llm_request(chat_request(), session_id="s1")
    assert engine.on_llm_output("All done.", session_id="s1") is None


def test_local_tool_gets_real_values_network_tool_keeps_placeholders(engine):
    res = engine.on_llm_request(chat_request(), session_id="s1")
    user = res["request"]["messages"][1]["content"]
    email_ph = [w.strip("(),.") for w in user.split() if "[[EMAIL_" in w][0]
    local = engine.on_tool_request("write_file", {"path": "reply.txt", "content": f"To: {email_ph}"}, session_id="s1")
    assert local["args"]["content"] == f"To: {PII['email']}"
    web = engine.on_tool_request("web_search", {"query": f"who is {email_ph}"}, session_id="s1")
    assert web is None  # unchanged -> placeholder goes to the search provider
    kinds = [e["kind"] for e in engine.audit.tail(10)]
    assert "tool_restore" in kinds and "tool_kept_placeholders" in kinds


def test_restore_tools_star_allows_everything(make_engine):
    eng = make_engine(restore_tools=("*",))
    res = eng.on_llm_request(chat_request(), session_id="s")
    ph = [w.strip("(),.") for w in res["request"]["messages"][1]["content"].split() if "[[EMAIL_" in w][0]
    assert eng.on_tool_request("web_search", {"q": ph}, session_id="s")["args"]["q"] == PII["email"]


def test_clean_request_is_passed_through(engine):
    assert engine.on_llm_request({"messages": [{"role": "user", "content": "refactor utils.py"}]}, session_id="s") is None


def test_block_policy_stops_the_turn_before_inference(make_engine):
    eng = make_engine(block_types=("CREDIT_CARD",))
    r = eng.on_pre_llm_call(session_id="s", user_message=USER)
    assert r["action"] == "block" and "CREDIT_CARD" in r["message"]
    assert PII["card"] not in r["message"]
    assert eng.on_pre_llm_call(session_id="s", user_message="hello") is None


def test_model_guide_only_when_needed(engine):
    assert engine.on_pre_llm_call(session_id="s", user_message="list files") is None
    assert "placeholder" in engine.on_pre_llm_call(session_id="s", user_message=USER)["context"]


def test_fail_closed_withholds_content(engine, monkeypatch):
    def boom(_):
        raise RuntimeError("detector crashed")

    monkeypatch.setattr(engine.detector, "find", boom)
    res = engine.on_llm_request(chat_request(), session_id="s")
    wire = json.dumps(res["request"])
    for v in PII.values():
        assert v not in wire
    assert WITHHELD in wire
    assert engine.audit.tail(1)[0]["kind"] == "error"


def test_fail_open_is_opt_in(make_engine, monkeypatch):
    eng = make_engine(fail_mode="open")
    monkeypatch.setattr(eng.detector, "find", lambda _: 1 / 0)
    assert eng.on_llm_request(chat_request(), session_id="s") is None


def test_audit_never_contains_values_or_placeholders_and_chain_detects_edits(engine):
    engine.on_llm_request(chat_request(), session_id="s1", provider="p", base_url="https://api.example.com/v1")
    engine.on_llm_output("[[nothing]]", session_id="s1")
    raw = engine.audit.path.read_text()
    for v in PII.values():
        assert v not in raw
    assert "[[" not in raw
    assert "s1" not in raw  # session id is hashed
    ev = engine.audit.tail(1)[0]
    assert ev["counts"]["EMAIL"] == 2 and ev["host"] == "api.example.com"
    assert engine.audit.verify()[0] is True
    lines = raw.splitlines()
    lines[0] = lines[0].replace('"EMAIL":2', '"EMAIL":0')
    engine.audit.path.write_text("\n".join(lines) + "\n")
    ok, _, msg = engine.audit.verify()
    assert not ok and "line 1" in msg


def test_auxiliary_calls_with_pii_are_flagged(engine):
    engine.on_pre_auxiliary_call(aux_task="title_generation", request={"messages": [{"role": "user", "content": USER}]}, session_id="s")
    engine.on_pre_auxiliary_call(aux_task="compression", request={"messages": [{"role": "user", "content": "hi"}]}, session_id="s")
    assert dict(engine.aux_unprotected) == {"title_generation": 1}
    assert engine.audit.tail(1)[0]["kind"] == "aux_unprotected"


def test_compaction_keeps_old_placeholders_resolvable(engine):
    res = engine.on_llm_request(chat_request(), session_id="old")
    ph = [w.strip("(),.") for w in res["request"]["messages"][1]["content"].split() if "[[EMAIL_" in w][0]
    engine.on_post_compaction(session_id="new", old_session_id="old")
    assert engine.on_llm_output(f"mail {ph}", session_id="new") == f"mail {PII['email']}"


def test_session_delete_drops_vault(engine):
    engine.on_llm_request(chat_request(), session_id="gone")
    engine.on_session_delete(session_id="gone")
    assert engine.vaults.peek("gone") is None


def test_cache_reuse_keeps_vault_consistent(engine):
    a = engine.on_llm_request(chat_request(), session_id="c")["request"]
    engine.vaults.drop("c")
    b = engine.on_llm_request(chat_request(), session_id="c")["request"]
    assert a == b
    ph = [w.strip("(),.") for w in b["messages"][1]["content"].split() if "[[EMAIL_" in w][0]
    assert engine.on_llm_output(ph, session_id="c") == PII["email"]


def test_terms_update_invalidates_cache(engine):
    req = {"messages": [{"role": "user", "content": "status of Project Nightjar please"}]}
    assert engine.on_llm_request(req, session_id="t") is None
    engine.set_terms(("Project Nightjar",))
    out = engine.on_llm_request(req, session_id="t")
    assert "Nightjar" not in json.dumps(out["request"]) and "[[TERM_" in json.dumps(out["request"])


def test_name_found_once_is_hidden_everywhere_in_the_session(engine):
    req = {"messages": [
        {"role": "user", "content": "My client Zebulon Moreau needs his file."},
        {"role": "tool", "tool_call_id": "c", "content": "42: Zebulon Moreau, open; Moreau called twice"},
    ]}
    wire = json.dumps(engine.on_llm_request(req, session_id="n")["request"])
    assert "Zebulon" not in wire and "Moreau" not in wire
    later = {"messages": [{"role": "tool", "tool_call_id": "d", "content": "note: Zebulon Moreau paid"}]}
    assert "Zebulon" not in json.dumps(engine.on_llm_request(later, session_id="n")["request"])
    # a learned name is matched case-sensitively: the word "moreau" in lower case is left alone
    other = {"messages": [{"role": "user", "content": "the moreau file format"}]}
    assert engine.on_llm_request(other, session_id="n") is None
