"""Load the repository root as an importable package named ``redactgate_plugin``.

Hermes imports a plugin directory as a namespaced package; the tests do the same so the
relative imports inside the plugin resolve exactly as they do at runtime.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


def _load():
    if "redactgate_plugin" in sys.modules:
        return sys.modules["redactgate_plugin"]
    spec = importlib.util.spec_from_file_location(
        "redactgate_plugin", ROOT / "__init__.py", submodule_search_locations=[str(ROOT)]
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules["redactgate_plugin"] = mod
    spec.loader.exec_module(mod)
    return mod


_load()


@pytest.fixture
def engine(tmp_path):
    from redactgate_plugin.engine import Engine, Settings

    return Engine(Settings(), b"k" * 32, tmp_path)


@pytest.fixture
def make_engine(tmp_path):
    from redactgate_plugin.engine import Engine, Settings

    def _mk(**kw):
        return Engine(Settings(**kw), b"k" * 32, tmp_path)

    return _mk
