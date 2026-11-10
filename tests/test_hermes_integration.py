"""Load the plugin through Hermes's real PluginManager and drive the real call sites.

Skipped when Hermes is not importable (plain `pytest` in this repo); CI runs it against a
pinned hermes-agent checkout.
"""

import json
import shutil
from pathlib import Path

import pytest

hermes_plugins = pytest.importorskip("hermes_cli.plugins")
middleware = pytest.importorskip("hermes_cli.middleware")

ROOT = Path(__file__).resolve().parent.parent
FILES = ["__init__.py", "plugin.yaml", "engine.py", "detect.py", "vault.py", "walk.py", "audit.py", "commands.py", "names_en.py", "setup_check.py"]


@pytest.fixture
def manager(tmp_path, monkeypatch):
    home = tmp_path / "hermes_home"
    dst = home / "plugins" / "redactgate"
    dst.mkdir(parents=True)
    for f in FILES:
        shutil.copy(ROOT / f, dst / f)
    shutil.copytree(ROOT / "redactgate_core", dst / "redactgate_core")
    (home / "config.yaml").write_text("plugins:\n  enabled:\n    - redactgate\n", encoding="utf-8")
    monkeypatch.setenv("HERMES_HOME", str(home))
    mgr = hermes_plugins.PluginManager()
    mgr.discover_and_load()
    monkeypatch.setattr(hermes_plugins, "get_plugin_manager", lambda: mgr)
    monkeypatch.setattr(hermes_plugins, "_plugin_manager", mgr, raising=False)
    return mgr


def test_plugin_loads_with_declared_surfaces(manager):
    loaded = manager._plugins["redactgate"]
    assert set(loaded.middleware_registered) == {"llm_request", "tool_request"}
    assert manager.holds_stream() is True  # transform_llm_output registered with hold_stream=True


def test_round_trip_through_hermes_call_sites(manager):
    req = {"model": "m", "messages": [
        {"role": "system", "content": "You are helpful."},
        {"role": "user", "content": "Email Jane Doe at jane.doe@acme-corp.com about invoice 7."},
    ]}
    res = middleware.apply_llm_request_middleware(req, session_id="sess-int", provider="openrouter",
                                                  base_url="https://openrouter.ai/api/v1", model="m")
    wire = json.dumps(res.payload)
    assert "jane.doe@acme-corp.com" not in wire and "Jane Doe" not in wire
    assert res.changed is True
    assert any(t.get("source") == "redactgate" for t in res.trace)
    user = res.payload["messages"][1]["content"]
    email_ph = [w for w in user.split() if w.startswith("[[EMAIL_")][0]
    person_ph = user.split("Email ")[1].split(" at ")[0]

    tool = middleware.apply_tool_request_middleware(
        "write_file", {"path": "draft.txt", "content": f"To: {email_ph}\nDear {person_ph},"}, session_id="sess-int")
    assert tool.payload["content"] == "To: jane.doe@acme-corp.com\nDear Jane Doe,"

    web = middleware.apply_tool_request_middleware("web_search", {"query": person_ph}, session_id="sess-int")
    assert web.payload["query"] == person_ph

    outs = hermes_plugins.invoke_hook("transform_llm_output", response_text=f"Sent to {person_ph} ({email_ph}).",
                                      session_id="sess-int", model="m", platform="cli")
    assert "Sent to Jane Doe (jane.doe@acme-corp.com)." in outs


def test_slash_command_is_registered(manager):
    cmds = getattr(manager, "_plugin_commands", None) or getattr(manager, "_commands", {})
    assert "redact" in cmds


def test_setup_check_and_harden_use_the_real_hermes_config(manager):
    from redactgate_plugin import setup_check

    assert any("session titles" in f for f in setup_check.findings())
    msg = setup_check.harden()
    assert msg.startswith("Done"), msg
    from hermes_cli.config import load_config_readonly

    assert load_config_readonly()["auxiliary"]["title_generation"]["model_upgrade_enabled"] is False
    assert not any("session titles" in f for f in setup_check.findings())
