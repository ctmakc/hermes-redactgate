"""Walk every provider request shape Hermes sends and rewrite its text fields.

Shapes covered (new for the Hermes plugin):

* OpenAI Chat Completions — ``messages[].content`` (str | parts), every role including
  ``system`` / ``developer`` / ``tool``; ``tool_calls[].function.arguments`` (JSON string).
* Anthropic Messages — top-level ``system`` (str | blocks), ``content`` blocks ``text``,
  ``tool_use.input`` (dict), ``tool_result.content`` (str | blocks).
* OpenAI Responses / Codex — ``instructions``, ``input`` (str | items): ``message`` items,
  ``function_call.arguments``, ``function_call_output.output``.
* Legacy completions — ``prompt``.

Never touched: ids, roles, tool names and schemas, images/audio/files, and model reasoning
blocks (``thinking`` / ``redacted_thinking`` / ``reasoning``), because providers verify
their signatures and a rewrite would fail the request.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

TextFn = Callable[[str], str]

ROOT_KEYS = ("messages", "system", "input", "instructions", "prompt")
SKIP_BLOCK_TYPES = {
    "thinking", "redacted_thinking", "reasoning", "image", "image_url", "input_image",
    "input_audio", "audio", "file", "input_file", "document", "computer_screenshot",
}
TEXT_KEYS = {"content", "text", "output", "refusal", "instructions", "system", "prompt"}
JSON_STRING_KEYS = {"arguments"}
DICT_ARG_KEYS = {"input"}  # anthropic tool_use.input


def _walk_json_value(v: Any, fn: TextFn) -> Any:
    if isinstance(v, str):
        return fn(v)
    if isinstance(v, list):
        return [_walk_json_value(x, fn) for x in v]
    if isinstance(v, dict):
        return {k: _walk_json_value(x, fn) for k, x in v.items()}
    return v


def rewrite_arguments(arg: Any, fn: TextFn) -> Any:
    """Tool-call arguments: JSON string -> walk values -> JSON string; dict -> walk."""
    if isinstance(arg, dict):
        return _walk_json_value(arg, fn)
    if not isinstance(arg, str) or not arg:
        return arg
    try:
        parsed = json.loads(arg)
    except (ValueError, TypeError):
        return fn(arg)
    if not isinstance(parsed, (dict, list)):
        return fn(arg)
    return json.dumps(_walk_json_value(parsed, fn), ensure_ascii=False)


def _walk_node(node: Any, fn: TextFn) -> Any:
    if isinstance(node, str):
        return fn(node)
    if isinstance(node, list):
        return [_walk_node(x, fn) for x in node]
    if not isinstance(node, dict):
        return node
    btype = node.get("type")
    if isinstance(btype, str) and btype in SKIP_BLOCK_TYPES:
        return node
    out = dict(node)
    for k, v in node.items():
        if k in JSON_STRING_KEYS:
            out[k] = rewrite_arguments(v, fn)
        elif k == "input" and btype == "tool_use":
            out[k] = rewrite_arguments(v, fn)
        elif k in TEXT_KEYS or (k == "input" and isinstance(v, (str, list))):
            out[k] = _walk_node(v, fn)
        elif k in ("tool_calls", "function", "function_call"):
            out[k] = _walk_node(v, fn)
        elif k == "summary" and isinstance(v, (str, list)) and btype not in SKIP_BLOCK_TYPES:
            out[k] = _walk_node(v, fn)
        # every other key (id, role, name, type, cache_control, signature …) is kept as-is
    return out


def rewrite_request(request: dict[str, Any], fn: TextFn) -> dict[str, Any]:
    """Return a copy of *request* with every model-visible text passed through *fn*."""
    out = dict(request)
    for key in ROOT_KEYS:
        if key in request and request[key] is not None:
            out[key] = _walk_node(request[key], fn)
    return out


def iter_texts(request: dict[str, Any]) -> list[str]:
    """All model-visible texts in *request* (for counting / leak checks)."""
    seen: list[str] = []

    def grab(s: str) -> str:
        seen.append(s)
        return s

    rewrite_request(request, grab)
    return seen
