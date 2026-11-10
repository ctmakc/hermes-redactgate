"""RedactGate for Hermes Agent — reversible PII pseudonymization at the model boundary.

register(ctx) wires:
  llm_request middleware   every outgoing model request is tokenized (all roles, tool calls,
                           tool results, system prompt, Anthropic/Responses shapes)
  pre_llm_call             optional block policy + a one-paragraph placeholder guide
  tool_request middleware  real values restored only for local tools (allow-list)
  transform_llm_output     real values restored in the reply, stream held until then
  pre_auxiliary_call       observer: flags auxiliary calls that carried PII (not rewritable)
  post_compaction / on_session_delete   keep / drop the in-memory session vault
  /redact                  status, last request as the model saw it, dry run, audit, terms
"""

from __future__ import annotations

import logging
import secrets as _secrets
from pathlib import Path
from typing import Any

from .commands import make_handler
from .engine import Engine, Settings

__version__ = "1.0.0"
logger = logging.getLogger("redactgate")

_ENGINE: Engine | None = None


class _MemoryState:
    """Stand-in when the host context has no ``state`` (validation probe, old Hermes)."""

    def __init__(self) -> None:
        import tempfile

        self._d: dict[str, Any] = {}
        self.data_dir = Path(tempfile.gettempdir()) / "redactgate-ephemeral"

    def get(self, key: str, default: Any = None) -> Any:
        return self._d.get(key, default)

    def set(self, key: str, value: Any) -> None:
        self._d[key] = value


def _state(ctx: Any) -> Any:
    try:
        return ctx.state
    except AttributeError:
        return _MemoryState()


def _data_dir(st: Any) -> Path:
    d = getattr(st, "data_dir", None)
    if callable(d):
        d = d()
    if d:
        return Path(d)
    from plugins.plugin_storage import plugin_data_dir  # documented fallback

    return plugin_data_dir("redactgate")


def _secret(st: Any) -> bytes:
    hexkey = st.get("hmac_secret", default=None)
    if not isinstance(hexkey, str) or len(hexkey) != 64:
        hexkey = _secrets.token_hex(32)
        st.set("hmac_secret", hexkey)
    return bytes.fromhex(hexkey)


def build_engine(ctx: Any, st: Any) -> Engine:
    state_terms = tuple(st.get("terms", default=[]) or [])
    settings = Settings.from_getter(lambda k, d=None: ctx.get_config(k, default=d), extra_terms=state_terms)
    return Engine(settings, _secret(st), _data_dir(st))


def register(ctx: Any) -> None:
    global _ENGINE
    st = _state(ctx)
    engine = build_engine(ctx, st)
    _ENGINE = engine

    def _set_terms(terms: list[str]) -> None:
        st.set("terms", terms)
        cfg = Settings.from_getter(lambda k, d=None: ctx.get_config(k, default=d))
        engine.set_terms(tuple(dict.fromkeys(cfg.terms + tuple(terms))))

    def _terms() -> list[str]:
        return list(st.get("terms", default=[]) or [])

    def _add(t: str) -> None:
        cur = _terms()
        if t not in cur:
            cur.append(t)
        _set_terms(cur)

    def _rm(t: str) -> bool:
        cur = _terms()
        if t not in cur:
            return False
        cur.remove(t)
        _set_terms(cur)
        return True

    ctx.register_middleware("llm_request", engine.on_llm_request)
    ctx.register_middleware("tool_request", engine.on_tool_request)
    ctx.register_hook("pre_llm_call", engine.on_pre_llm_call)
    try:
        ctx.register_hook("transform_llm_output", engine.on_llm_output, hold_stream=True)
    except TypeError:  # Hermes without hold_stream: reply still restored, placeholders may flash while streaming
        ctx.register_hook("transform_llm_output", engine.on_llm_output)
    ctx.register_hook("pre_auxiliary_call", engine.on_pre_auxiliary_call)
    ctx.register_hook("post_compaction", engine.on_post_compaction)
    ctx.register_hook("on_session_delete", engine.on_session_delete)
    ctx.register_command(
        "redact",
        make_handler(lambda: engine, {"add": _add, "remove": _rm, "list": _terms}),
        description="RedactGate: what the model saw, dry run, audit, custom terms",
        args_hint="[status|last|test <text>|audit [n]|verify|harden|terms add <t>]",
    )
