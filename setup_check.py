"""Check the Hermes settings RedactGate cannot enforce itself (new for the Hermes plugin).

Hermes runs auxiliary model calls (session titles, context compression, vision, approval)
without request middleware, so no plugin can rewrite them. This module reads the merged
Hermes config through the public ``hermes_cli.config`` module and reports the ones that send
conversation text to a model; ``harden()`` switches off the one that fires on every new
session (model-made titles), only when the user runs ``/redact harden``.
"""

from __future__ import annotations

from typing import Any

TITLE_KEY = "auxiliary.title_generation.model_upgrade_enabled"


def _cfg() -> dict[str, Any] | None:
    try:
        from hermes_cli.config import load_config_readonly

        cfg = load_config_readonly()
        return cfg if isinstance(cfg, dict) else None
    except Exception:
        return None


def _get(cfg: dict[str, Any], dotted: str, default: Any = None) -> Any:
    cur: Any = cfg
    for part in dotted.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return default
        cur = cur[part]
    return cur


def _is_local(cfg: dict[str, Any], task: str) -> bool:
    provider = str(_get(cfg, f"auxiliary.{task}.provider", "auto") or "auto").lower()
    base = str(_get(cfg, f"auxiliary.{task}.base_url", "") or "")
    return provider in ("ollama", "lmstudio", "llamacpp", "vllm") or "127.0.0.1" in base or "localhost" in base


def findings() -> list[str]:
    """Human lines; empty when nothing sends conversation text around the plugin."""
    cfg = _cfg()
    if cfg is None:
        return []
    out = []
    titles_on = _get(cfg, "auxiliary.title_generation.enabled", True) is not False
    upgrade_on = _get(cfg, TITLE_KEY, True) is not False
    if titles_on and upgrade_on and not _is_local(cfg, "title_generation"):
        out.append(
            "session titles: Hermes sends your first message to a model to name the session, "
            "outside any plugin. Fix: /redact harden (keeps the instant local title)."
        )
    if _get(cfg, "compression.enabled", True) is not False and not _is_local(cfg, "compression"):
        out.append(
            "long sessions: context compression sends the raw transcript to auxiliary.compression.provider "
            "(auto = your main provider). Point it at a local model to keep it private."
        )
    return out


def harden() -> str:
    try:
        from hermes_cli.config import set_config_value

        set_config_value(TITLE_KEY, "false")
    except (Exception, SystemExit) as exc:
        return f"Could not change {TITLE_KEY} ({type(exc).__name__}). Run: hermes config set {TITLE_KEY} false"
    return (
        f"Done: {TITLE_KEY} = false. New sessions keep Hermes's instant local title and no longer send "
        "your first message to a model for naming. Context compression is left as is (see /redact)."
    )
