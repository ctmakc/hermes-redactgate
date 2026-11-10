"""``/redact`` slash command (new for the Hermes plugin)."""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

from .engine import Engine
from .vault import mask_value

HELP = """/redact — RedactGate privacy layer
  /redact                 status: what is protected, counters, warnings
  /redact last            what the model actually received for your last message
  /redact test <text>     dry run: show how <text> would be sent
  /redact audit [n]       last n audit events (counts only, never values)
  /redact verify          check the audit hash chain
  /redact terms           list custom always-hide terms
  /redact terms add <t>   always hide <t> (a client, a project code name)
  /redact terms rm <t>    stop hiding <t>"""


def _ago(ts: float) -> str:
    d = max(0, int(time.time() - ts))
    return f"{d}s ago" if d < 120 else f"{d // 60}m ago"


def _fmt_counts(c: dict[str, int]) -> str:
    return ", ".join(f"{k}×{v}" for k, v in sorted(c.items(), key=lambda kv: (-kv[1], kv[0]))) or "none"


def make_handler(get_engine: Callable[[], Engine], terms_api: dict[str, Callable[..., Any]]):
    def handler(raw_args: str = "", *, session_id: str = "", **_: Any) -> str:
        eng = get_engine()
        args = (raw_args or "").strip()
        cmd, _, rest = args.partition(" ")
        cmd = cmd.lower()
        rest = rest.strip()

        if cmd in ("", "status"):
            s = eng.settings
            lines = [
                "RedactGate is ON — model requests are tokenized before they leave this machine.",
                f"  packs: {', '.join(eng.detector.pack_codes)} · names: {'on' if s.names else 'off'}"
                f" · custom terms: {len(s.terms)}",
                f"  real values go back to: the reply you read, and tools {', '.join(s.restore_tools)}",
                "  everything else (web, browser, MCP, subagents) gets placeholders",
                f"  on redaction error: {'withhold the request' if s.fail_mode == 'closed' else 'send unmodified (fail_mode: open)'}",
                f"  this process: {eng.stats['requests']} requests, {eng.stats['entities']} values hidden, "
                f"{eng.stats['reply_restored']} restored in replies, {eng.stats['tool_restored']} local tool calls restored, "
                f"{eng.stats['tool_kept']} tool calls kept placeholders, {eng.stats['blocked']} blocked",
            ]
            if s.block_types:
                lines.append(f"  never sent (turn blocked): {', '.join(s.block_types)}")
            if eng.aux_unprotected:
                lines.append(
                    "  ⚠ auxiliary model calls carried personal data RedactGate cannot rewrite "
                    "(Hermes runs no llm_request middleware for them): "
                    + _fmt_counts(dict(eng.aux_unprotected))
                    + ". Point auxiliary tasks at a local model, or add the types to block_types."
                )
            if eng.errors:
                lines.append(f"  ⚠ redaction errors: {', '.join(eng.errors[-3:])}")
            return "\n".join(lines)

        if cmd == "last":
            last = eng.last.get(session_id) or (next(reversed(eng.last.values())) if eng.last else None)
            if last is None:
                return "No model request yet in this session."
            lines = [
                f"Last request → {last.host or last.provider or 'provider'} ({last.model}), {_ago(last.ts)}",
                f"Hidden in the full request: {_fmt_counts(last.counts)}",
                "Your last message, exactly as the model received it:",
                "  " + (last.user_preview[:1200] + ("…" if len(last.user_preview) > 1200 else "")).replace("\n", "\n  "),
            ]
            if last.placeholders:
                lines.append("Placeholders (values masked here, kept in memory only):")
                lines += [f"  {ph}  ←  {masked}" for ph, masked in last.placeholders[:30]]
            return "\n".join(lines)

        if cmd == "test":
            if not rest:
                return "Usage: /redact test <text>"
            out, spans = eng.redact_text(rest)
            lines = ["Would be sent as:", "  " + out]
            if spans:
                lines.append("Found: " + ", ".join(f"{s.entity_type} ({mask_value(s.text)})" for s in spans))
            else:
                lines.append("Nothing to hide.")
            return "\n".join(lines)

        if cmd == "audit":
            try:
                n = max(1, min(50, int(rest or 10)))
            except ValueError:
                n = 10
            evs = eng.audit.tail(n)
            if not evs:
                return f"No audit events yet ({eng.audit.path})."
            lines = [f"Audit journal {eng.audit.path} (last {len(evs)}):"]
            for e in evs:
                ts = time.strftime("%H:%M:%S", time.localtime(e.get("ts", 0)))
                extra = []
                if e.get("counts"):
                    extra.append(_fmt_counts(e["counts"]))
                for k in ("tool", "placeholders", "aux_task", "host", "types", "error"):
                    if e.get(k):
                        extra.append(f"{k}={e[k]}")
                lines.append(f"  {ts} {e.get('kind', '?'):<22} {' · '.join(str(x) for x in extra)}  #{str(e.get('hash', ''))[:8]}")
            return "\n".join(lines)

        if cmd == "verify":
            ok, n, msg = eng.audit.verify()
            return ("✓ " if ok else "✗ ") + msg

        if cmd == "terms":
            sub, _, term = rest.partition(" ")
            sub = sub.lower()
            term = term.strip().strip('"').strip("'")
            if sub == "add" and term:
                terms_api["add"](term)
                return f"Added. “{mask_value(term)}” is hidden from the model from the next request on."
            if sub in ("rm", "remove", "del") and term:
                ok = terms_api["remove"](term)
                return "Removed." if ok else "Not in the list."
            terms = terms_api["list"]()
            if not terms:
                return "No custom terms. Add one: /redact terms add <name or code word>"
            return "Custom terms (masked): " + ", ".join(mask_value(t) for t in terms)

        return HELP

    return handler
