"""Shared fixtures for ReadyTrader Stocks plugin tests.

Stubs Agent Zero runtime modules (python.helpers.tool, python.helpers.plugins)
so tests run standalone without a live A0 instance.
"""
import sys
import types
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

_REPO_ROOT = Path(__file__).parent.parent.resolve()


def _install_a0_stubs():
    """Install minimal stubs for python.helpers.tool and python.helpers.plugins."""
    python_pkg = sys.modules.get("python") or types.ModuleType("python")
    python_pkg.__path__ = []
    sys.modules["python"] = python_pkg

    helpers_pkg = types.ModuleType("python.helpers")
    helpers_pkg.__path__ = []
    sys.modules["python.helpers"] = helpers_pkg
    python_pkg.helpers = helpers_pkg

    # python.helpers.tool
    tool_mod = types.ModuleType("python.helpers.tool")

    @dataclass
    class Response:
        message: str
        break_loop: bool
        additional: dict[str, Any] | None = None

    class Tool:
        def __init__(self, agent=None, name="", method=None, args=None,
                     message="", loop_data=None, **kw):
            self.agent = agent
            self.name = name
            self.method = method
            self.args = args or {}
            self.loop_data = loop_data
            self.message = message

        async def execute(self, **kw):
            raise NotImplementedError

    tool_mod.Tool = Tool
    tool_mod.Response = Response
    sys.modules["python.helpers.tool"] = tool_mod
    helpers_pkg.tool = tool_mod

    # python.helpers.plugins
    plugins_mod = types.ModuleType("python.helpers.plugins")
    # Tests may monkeypatch this to simulate settings overrides
    plugins_mod._override_config = {}

    def get_plugin_config(plugin_name, agent=None):
        return dict(plugins_mod._override_config)

    plugins_mod.get_plugin_config = get_plugin_config
    sys.modules["python.helpers.plugins"] = plugins_mod
    helpers_pkg.plugins = plugins_mod

    # Ensure the plugin's own package root is importable so `tools.stock_tools`
    # resolves from tests
    if str(_REPO_ROOT) not in sys.path:
        sys.path.insert(0, str(_REPO_ROOT))


_install_a0_stubs()


@pytest.fixture
def set_plugin_config():
    """Override the get_plugin_config return value for a single test."""
    import python.helpers.plugins as plugins_mod

    def _apply(**overrides):
        plugins_mod._override_config = dict(overrides)

    yield _apply
    plugins_mod._override_config = {}


@pytest.fixture
def mock_agent():
    """Minimal agent stand-in — tools only pass it through to get_plugin_config."""
    class _A:
        pass
    return _A()
