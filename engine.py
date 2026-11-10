"""RedactGate for Hermes — the runtime behind every hook (new for the Hermes plugin).

Kept free of Hermes imports so it is unit-testable; ``__init__.register`` wires it to
``ctx``. Every public method is total: it catches its own errors, because Hermes skips a
raising middleware and would then send the request unmodified.
"""

from __future__ import annotations

import hashlib
import logging
import threading
import time
from collections import Counter, OrderedDict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from .audit import AuditLog, session_ref
from .detect import Detector, DetectorConfig
from .redactgate_core.entities import EntitySpan
from .redactgate_core.merge import merge_spans
from .vault import SessionVault, VaultRegistry, find_placeholders, mask_value, restore, tokenize
from .walk import ROOT_KEYS, _walk_json_value, iter_texts, rewrite_request

logger = logging.getLogger("redactgate")

# Local tools get real values back in their arguments; everything else (web search, browser,
# image generation, MCP and other plugins' tools, subagents) keeps the placeholders.
LOCAL_TOOLS = (
    "terminal", "process_manage", "read_file", "write_file", "patch", "search_files",
    "execute_code", "todo_list", "memory", "clarify",
)

WITHHELD = "[redactgate: content withheld because redaction failed; see /redact status]"

MODEL_GUIDE = (
    "[RedactGate privacy note] Personal data in this conversation has been replaced with "
    "placeholders shaped like [[PERSON_xxxxxx]] or [[EMAIL_xxxxxx]] before it reached you. Treat "
    "each placeholder as the real value it stands for and copy it exactly, double brackets "
    "included, whenever you need that value in your answer or in a tool call. RedactGate puts "
    "the real value back for the user and for local tools. Never ask the user to reveal it."
)


@dataclass
class Settings:
    packs: tuple[str, ...] = ("GENERIC", "CA", "US", "EU", "UA", "IRCC")
    names: bool = True
    rule_ner: bool = True
    terms: tuple[str, ...] = ()
    allow: tuple[str, ...] = ()
    ignore_types: tuple[str, ...] = ()
    restore_tools: tuple[str, ...] = LOCAL_TOOLS
    block_types: tuple[str, ...] = ()
    fail_mode: str = "closed"
    guide_model: bool = True

    @classmethod
    def from_getter(cls, get, extra_terms: tuple[str, ...] = ()) -> "Settings":
        def _list(key: str, default: tuple[str, ...]) -> tuple[str, ...]:
            v = get(key, None)
            if v is None:
                return default
            if isinstance(v, str):
                v = [x.strip() for x in v.split(",")]
            return tuple(str(x) for x in v if str(x).strip())

        def _bool(key: str, default: bool) -> bool:
            v = get(key, None)
            if v is None:
                return default
            if isinstance(v, str):
                return v.strip().lower() in ("1", "true", "yes", "on")
            return bool(v)

        fail = str(get("fail_mode", "closed") or "closed").lower()
        return cls(
            packs=_list("packs", cls.packs),
            names=_bool("names", True),
            rule_ner=_bool("rule_ner", True),
            terms=_list("terms", ()) + tuple(extra_terms),
            allow=_list("allow", ()),
            ignore_types=_list("ignore_types", ()),
            restore_tools=_list("restore_tools", LOCAL_TOOLS),
            block_types=tuple(t.upper() for t in _list("block_types", ())),
            fail_mode="open" if fail == "open" else "closed",
            guide_model=_bool("guide_model", True),
        )


@dataclass
class LastRequest:
    ts: float
    provider: str
    model: str
    host: str
    counts: dict[str, int]
    user_preview: str
    placeholders: list[tuple[str, str]] = field(default_factory=list)  # (placeholder, masked)


def _host(base_url: str) -> str:
    try:
        return urlparse(base_url or "").hostname or ""
    except ValueError:
        return ""


def _last_user_text(request: dict[str, Any]) -> str:
    for key in ("messages", "input"):
        items = request.get(key)
        if isinstance(items, str) and key == "input":
            return items
        if not isinstance(items, list):
            continue
        for item in reversed(items):
            if not isinstance(item, dict) or item.get("role") != "user":
                continue
            c = item.get("content")
            if isinstance(c, str):
                return c
            if isinstance(c, list):
                parts = [p.get("text", "") for p in c if isinstance(p, dict) and isinstance(p.get("text"), str)]
                if parts:
                    return "\n".join(parts)
    return ""


class Engine:
    def __init__(self, settings: Settings, secret: bytes, data_dir: Path):
        self.settings = settings
        self.detector = Detector(
            DetectorConfig(
                packs=settings.packs,
                names=settings.names,
                rule_ner=settings.rule_ner,
                terms=settings.terms,
                allow=settings.allow,
                ignore_types=settings.ignore_types,
            )
        )
        self.vaults = VaultRegistry(secret)
        self.audit = AuditLog(Path(data_dir) / "audit.jsonl")
        self.last: "OrderedDict[str, LastRequest]" = OrderedDict()
        self.aux_unprotected: Counter[str] = Counter()
        self.stats: Counter[str] = Counter()
        self.errors: list[str] = []
        self._cache: "OrderedDict[str, tuple]" = OrderedDict()
        self._lock = threading.Lock()

    # ── core ────────────────────────────────────────────────────────────────
    _LEARN_TYPES = ("PERSON", "ORG", "TERM", "ADDRESS")

    def _spans(self, text: str) -> tuple:
        """Detector spans for *text*, cached by content (detection is the expensive part)."""
        key = hashlib.blake2b(text.encode("utf-8", "surrogatepass"), digest_size=16).hexdigest()
        with self._lock:
            hit = self._cache.get(key)
            if hit is not None:
                self._cache.move_to_end(key)
                return hit
        spans = tuple(self.detector.find(text))
        with self._lock:
            self._cache[key] = spans
            if len(self._cache) > 8192:
                self._cache.popitem(last=False)
        return spans

    def _learn(self, vault: SessionVault, spans) -> None:
        for sp in spans:
            if sp.entity_type not in self._LEARN_TYPES:
                continue
            vault.learn(sp.entity_type, sp.text)
            if sp.entity_type == "PERSON":
                parts = sp.text.split()
                if len(parts) >= 2 and len(parts[-1]) >= 4 and parts[-1][:1].isupper():
                    vault.learn("PERSON", parts[-1])  # "Jane Doe" ... later "Ms Doe" / "Doe replied"

    def _tok(self, session: str, vault: SessionVault, text: str, counts: Counter[str]) -> str:
        if not text or len(text) < 3:
            return text
        spans = list(self._spans(text))
        cs, ci = vault.known_patterns()
        for rx in (cs, ci):
            if rx is None:
                continue
            for m in rx.finditer(text):
                etype = vault.known_type(m.group(0)) or "PERSON"
                spans.append(EntitySpan(m.start(), m.end(), etype, m.group(0), 0.97, "session"))
        if not spans:
            return text
        spans = merge_spans(spans)
        counts.update(sp.entity_type for sp in spans)
        return tokenize(text, spans, vault)

    def set_terms(self, terms: tuple[str, ...]) -> None:
        """Swap the detector for one with new custom terms; cached tokenizations are dropped."""
        from dataclasses import replace

        self.settings = replace(self.settings, terms=terms)
        s = self.settings
        detector = Detector(
            DetectorConfig(packs=s.packs, names=s.names, rule_ner=s.rule_ner, terms=s.terms, allow=s.allow, ignore_types=s.ignore_types)
        )
        with self._lock:
            self.detector = detector
            self._cache.clear()

    def redact_text(self, text: str, session: str = "_preview") -> tuple[str, list]:
        vault = self.vaults.get(session)
        spans = self.detector.find(text)
        return tokenize(text, spans, vault), spans

    # ── llm_request middleware ─────────────────────────────────────────────
    def on_llm_request(self, request: dict[str, Any], **kw: Any) -> dict[str, Any] | None:
        session = str(kw.get("session_id") or kw.get("task_id") or "")
        try:
            if not isinstance(request, dict):
                return None
            vault = self.vaults.get(session)
            counts: Counter[str] = Counter()
            for text in iter_texts(request):  # pass 1: learn names anywhere in the request
                if text and len(text) >= 3:
                    self._learn(vault, self._spans(text))
            new = rewrite_request(request, lambda s: self._tok(session, vault, s, counts))
            n = sum(counts.values())
            self.stats["requests"] += 1
            self.stats["entities"] += n
            preview = _last_user_text(new)
            phs = []
            for ph in dict.fromkeys(find_placeholders(preview)):
                etype, _, hx = ph[2:-2].rpartition("_")
                val = vault.lookup(etype, hx)
                phs.append((ph, mask_value(val) if val else "?"))
            with self._lock:
                self.last[session] = LastRequest(
                    ts=time.time(),
                    provider=str(kw.get("provider") or ""),
                    model=str(kw.get("model") or request.get("model") or ""),
                    host=_host(str(kw.get("base_url") or "")),
                    counts=dict(counts),
                    user_preview=preview,
                    placeholders=phs,
                )
                self.last.move_to_end(session)
                while len(self.last) > 64:
                    self.last.popitem(last=False)
            self.audit.append(
                "llm_request",
                session=session_ref(session),
                provider=str(kw.get("provider") or ""),
                host=_host(str(kw.get("base_url") or "")),
                model=str(kw.get("model") or ""),
                api_call=kw.get("api_call_count"),
                counts=dict(counts),
            )
            if n == 0:
                return None
            return {"request": new, "source": "redactgate", "reason": f"tokenized {n} PII value(s)"}
        except Exception as exc:  # fail closed: never let the raw request through on our bug
            return self._fail(request, session, exc)

    def _fail(self, request: Any, session: str, exc: Exception) -> dict[str, Any] | None:
        logger.warning("redactgate: redaction failed (%s)", type(exc).__name__, exc_info=True)
        self.errors.append(f"{time.strftime('%H:%M:%S')} {type(exc).__name__}")
        del self.errors[:-20]
        try:
            self.audit.append("error", session=session_ref(session), error=type(exc).__name__, fail_mode=self.settings.fail_mode)
        except Exception:
            pass
        if self.settings.fail_mode == "open" or not isinstance(request, dict):
            return None
        try:
            blanked = rewrite_request(request, lambda s: WITHHELD if s and s.strip() else s)
        except Exception:
            blanked = dict(request)
            for k in ROOT_KEYS:
                if k in blanked:
                    blanked[k] = [{"role": "user", "content": WITHHELD}] if k in ("messages", "input") else WITHHELD
        return {"request": blanked, "source": "redactgate", "reason": "redaction failed; content withheld"}

    # ── pre_llm_call: block policy + placeholder guide ─────────────────────
    def on_pre_llm_call(self, session_id: str = "", user_message: str = "", **kw: Any) -> Any:
        try:
            spans = self.detector.find(user_message or "") if user_message else []
            types = {s.entity_type for s in spans}
            hit = sorted(types & set(self.settings.block_types))
            if hit:
                self.audit.append("block", session=session_ref(session_id), types=hit)
                self.stats["blocked"] += 1
                return {
                    "action": "block",
                    "message": (
                        "RedactGate held this message back: it contains "
                        + ", ".join(hit)
                        + ", which this profile is set to never send to a model. Remove it and try "
                        "again, or change plugins.entries.redactgate.settings.block_types."
                    ),
                }
            if not self.settings.guide_model:
                return None
            vault = self.vaults.peek(session_id)
            if spans or (vault is not None and len(vault)):
                return {"context": MODEL_GUIDE}
            return None
        except Exception:
            logger.debug("redactgate pre_llm_call failed", exc_info=True)
            return None

    # ── tool_request middleware: real values for local tools only ──────────
    def tool_allowed(self, tool_name: str) -> bool:
        allow = self.settings.restore_tools
        return "*" in allow or tool_name in allow

    def on_tool_request(self, tool_name: str = "", args: Any = None, **kw: Any) -> dict[str, Any] | None:
        session = str(kw.get("session_id") or kw.get("task_id") or "")
        try:
            if not isinstance(args, dict):
                return None
            vault = self.vaults.peek(session)
            if vault is None or not len(vault):
                return None
            n_total = 0

            def _r(s: str) -> str:
                nonlocal n_total
                out, n = restore(s, vault)
                n_total += n
                return out

            if not self.tool_allowed(tool_name):
                # count only, keep placeholders
                _walk_json_value(args, _r)
                if n_total:
                    self.stats["tool_kept"] += 1
                    self.audit.append("tool_kept_placeholders", session=session_ref(session), tool=tool_name, placeholders=n_total)
                return None
            new_args = _walk_json_value(args, _r)
            if not n_total:
                return None
            self.stats["tool_restored"] += 1
            self.audit.append("tool_restore", session=session_ref(session), tool=tool_name, placeholders=n_total)
            return {"args": new_args, "source": "redactgate", "reason": f"restored {n_total} value(s) for local tool {tool_name}"}
        except Exception:
            logger.debug("redactgate tool_request failed", exc_info=True)
            return None

    # ── transform_llm_output: the user sees real values ────────────────────
    def on_llm_output(self, response_text: str = "", session_id: str = "", **kw: Any) -> str | None:
        try:
            vault = self.vaults.peek(session_id) or self.vaults.peek(str(kw.get("task_id") or ""))
            out, n = restore(response_text or "", vault)
            if not n:
                return None
            self.stats["reply_restored"] += n
            self.audit.append("reply_restore", session=session_ref(session_id), placeholders=n)
            return out
        except Exception:
            logger.debug("redactgate transform_llm_output failed", exc_info=True)
            return None

    # ── observers ──────────────────────────────────────────────────────────
    def on_pre_auxiliary_call(
        self, aux_task: str = "", request: Any = None, session_id: str = "",
        request_messages: Any = None, system_prompt: str = "", **kw: Any,
    ) -> None:
        """Observer only: Hermes runs no request middleware for auxiliary calls, so all this
        hook can do is notice personal data in them and say so (audit + /redact warning)."""
        try:
            payload: dict[str, Any] = {}
            if isinstance(request_messages, list) and request_messages:
                payload["messages"] = request_messages
            if isinstance(system_prompt, str) and system_prompt:
                payload["system"] = system_prompt
            if not payload and isinstance(request, dict):
                body = request.get("body") if isinstance(request.get("body"), dict) else request
                payload = {k: body[k] for k in ROOT_KEYS if k in body}
            if not payload:
                return
            texts = iter_texts(payload)
            vault = self.vaults.peek(session_id)
            counts = Counter(self.detector.counts(texts))
            if vault is not None:  # names learned earlier in the session count too
                cs, ci = vault.known_patterns()
                for rx in (cs, ci):
                    if rx is not None:
                        for t in texts:
                            for m in rx.finditer(t or ""):
                                counts[vault.known_type(m.group(0)) or "PERSON"] += 1
            if counts:
                task = aux_task or "auxiliary"
                self.aux_unprotected[task] += 1
                self.audit.append(
                    "aux_unprotected",
                    session=session_ref(session_id),
                    aux_task=task,
                    host=_host(str(kw.get("base_url") or "")),
                    counts=dict(counts),
                )
        except Exception:
            logger.debug("redactgate pre_auxiliary_call failed", exc_info=True)

    def on_post_compaction(self, session_id: str = "", old_session_id: str = "", **kw: Any) -> None:
        if old_session_id and session_id and old_session_id != session_id:
            self.vaults.alias(old_session_id, session_id)

    def on_session_delete(self, session_id: str = "", deleted_ids: Any = None, **kw: Any) -> None:
        for sid in [session_id, *(deleted_ids or [])]:
            if sid:
                self.vaults.drop(str(sid))
                with self._lock:
                    self.last.pop(str(sid), None)
