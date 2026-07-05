"""Server-wiring tests for the PARDOT_TOOLS_ENABLED registration gate.

These reload src.server under each flag value with the Pardot register functions
patched, so we can observe what gets registered without touching the real MCP
surface. Complements the client-level guard tests in test_pardot_client.py and
the composite-degradation tests in test_cross_tools.py.
"""

import importlib
import os
from unittest.mock import patch

import pytest

import src.pardot_tools as pardot_tools
import src.pardot_write_tools as pardot_write_tools


def _reload_server(monkeypatch, value):
    """Reload src.server with PARDOT_TOOLS_ENABLED = `value` (None = unset), with
    the Pardot register functions patched. Returns (read_mock, write_mock)."""
    if value is None:
        monkeypatch.delenv("PARDOT_TOOLS_ENABLED", raising=False)
    else:
        monkeypatch.setenv("PARDOT_TOOLS_ENABLED", value)
    with (
        patch.object(pardot_tools, "register_tools") as read_mock,
        patch.object(pardot_write_tools, "register_tools") as write_mock,
    ):
        import src.server as server_mod

        importlib.reload(server_mod)
    return read_mock, write_mock


@pytest.fixture(autouse=True)
def _restore_server():
    """Snapshot PARDOT_TOOLS_ENABLED and restore it EXACTLY after each test, then
    reload src.server so it reflects the restored env — otherwise a value exported
    in the real dev/CI environment would be clobbered for the rest of the session
    and src.server left reloaded in the wrong state."""
    original = os.environ.get("PARDOT_TOOLS_ENABLED")
    yield
    if original is None:
        os.environ.pop("PARDOT_TOOLS_ENABLED", None)
    else:
        os.environ["PARDOT_TOOLS_ENABLED"] = original
    import src.server as server_mod

    importlib.reload(server_mod)


class TestPardotRegistrationGate:
    def test_disabled_registers_no_pardot_tools(self, monkeypatch):
        read_mock, write_mock = _reload_server(monkeypatch, "false")
        read_mock.assert_not_called()
        write_mock.assert_not_called()

    def test_unset_registers_curated_read_subset(self, monkeypatch):
        from src.pardot_tools import CURATED_READ_TOOLS

        read_mock, write_mock = _reload_server(monkeypatch, None)
        read_mock.assert_called_once()
        _, kwargs = read_mock.call_args
        assert kwargs.get("include") == CURATED_READ_TOOLS
        write_mock.assert_not_called()

    def test_truthy_registers_full_surface(self, monkeypatch):
        read_mock, write_mock = _reload_server(monkeypatch, "true")
        read_mock.assert_called_once()
        _, kwargs = read_mock.call_args
        assert "include" not in kwargs  # full surface = no include filter
        write_mock.assert_called_once()
