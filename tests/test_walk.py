import json

from redactgate_plugin.walk import iter_texts, rewrite_request

UP = str.upper


def test_chat_completions_every_role_and_tool_call_arguments():
    req = {
        "model": "gpt-x",
        "messages": [
            {"role": "system", "content": "you help jane"},
            {"role": "user", "content": [{"type": "text", "text": "hi"}, {"type": "image_url", "image_url": {"url": "data:x"}}]},
            {"role": "assistant", "content": None, "tool_calls": [
                {"id": "call_1", "type": "function", "function": {"name": "send", "arguments": json.dumps({"to": "jane", "n": 3})}}
            ]},
            {"role": "tool", "tool_call_id": "call_1", "content": "sent to jane"},
        ],
        "tools": [{"type": "function", "function": {"name": "send", "description": "send to jane"}}],
    }
    out = rewrite_request(req, UP)
    m = out["messages"]
    assert m[0]["content"] == "YOU HELP JANE"
    assert m[1]["content"][0]["text"] == "HI"
    assert m[1]["content"][1]["image_url"]["url"] == "data:x"  # images untouched
    args = json.loads(m[2]["tool_calls"][0]["function"]["arguments"])
    assert args == {"to": "JANE", "n": 3}
    assert m[2]["tool_calls"][0]["function"]["name"] == "send"
    assert m[2]["tool_calls"][0]["id"] == "call_1"
    assert m[3]["content"] == "SENT TO JANE" and m[3]["tool_call_id"] == "call_1"
    assert out["tools"] == req["tools"]  # tool schemas untouched
    assert out["model"] == "gpt-x"
    assert req["messages"][0]["content"] == "you help jane"  # input not mutated


def test_anthropic_system_tool_use_tool_result_and_thinking():
    req = {
        "system": [{"type": "text", "text": "about jane", "cache_control": {"type": "ephemeral"}}],
        "messages": [
            {"role": "assistant", "content": [
                {"type": "thinking", "thinking": "jane secret", "signature": "sig"},
                {"type": "tool_use", "id": "tu1", "name": "lookup", "input": {"q": "jane"}},
            ]},
            {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "tu1", "content": [{"type": "text", "text": "jane ok"}]}]},
        ],
    }
    out = rewrite_request(req, UP)
    assert out["system"][0]["text"] == "ABOUT JANE"
    assert out["system"][0]["cache_control"] == {"type": "ephemeral"}
    blocks = out["messages"][0]["content"]
    assert blocks[0] == req["messages"][0]["content"][0]  # signed thinking untouched
    assert blocks[1]["input"] == {"q": "JANE"} and blocks[1]["name"] == "lookup"
    assert out["messages"][1]["content"][0]["content"][0]["text"] == "JANE OK"


def test_responses_api_items():
    req = {
        "instructions": "be nice to jane",
        "input": [
            {"role": "user", "content": [{"type": "input_text", "text": "mail jane"}]},
            {"type": "function_call", "call_id": "c1", "name": "mail", "arguments": "{\"to\": \"jane\"}"},
            {"type": "function_call_output", "call_id": "c1", "output": "ok jane"},
            {"type": "reasoning", "summary": [{"type": "summary_text", "text": "jane"}], "encrypted_content": "zz"},
        ],
    }
    out = rewrite_request(req, UP)
    assert out["instructions"] == "BE NICE TO JANE"
    assert out["input"][0]["content"][0]["text"] == "MAIL JANE"
    assert json.loads(out["input"][1]["arguments"]) == {"to": "JANE"}
    assert out["input"][1]["call_id"] == "c1"
    assert out["input"][2]["output"] == "OK JANE"
    assert out["input"][3] == req["input"][3]  # reasoning items untouched


def test_plain_string_input_and_prompt_and_iter_texts():
    assert rewrite_request({"input": "jane"}, UP)["input"] == "JANE"
    assert rewrite_request({"prompt": "jane"}, UP)["prompt"] == "JANE"
    assert sorted(iter_texts({"messages": [{"role": "user", "content": "a"}], "system": "b"})) == ["a", "b"]
