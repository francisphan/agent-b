"""Tests for src/auth.py."""

import asyncio
import types

import pytest
from starlette.responses import JSONResponse

from src.auth import AUTH_LEVEL, require_write_access
from src.server import BearerAuthMiddleware


class TestRequireWriteAccess:
    def test_raises_when_auth_level_is_none(self):
        token = AUTH_LEVEL.set("none")
        try:
            with pytest.raises(PermissionError, match="Write access denied"):
                require_write_access()
        finally:
            AUTH_LEVEL.reset(token)

    def test_raises_when_auth_level_is_read(self):
        token = AUTH_LEVEL.set("read")
        try:
            with pytest.raises(PermissionError, match="MCP_WRITE_TOKEN"):
                require_write_access()
        finally:
            AUTH_LEVEL.reset(token)

    def test_succeeds_when_auth_level_is_write(self):
        token = AUTH_LEVEL.set("write")
        try:
            require_write_access()  # Should not raise
        finally:
            AUTH_LEVEL.reset(token)

    def test_default_auth_level_is_none(self):
        # Default ContextVar value should be "none"
        assert AUTH_LEVEL.get() == "none"


class TestBearerAuthMiddleware:
    """The no-token path must fail CLOSED unless insecure mode is opted into."""

    def _dispatch(self, monkeypatch, *, read=None, write=None, insecure=False, header=None):
        monkeypatch.setattr("src.server.READ_TOKEN", read)
        monkeypatch.setattr("src.server.WRITE_TOKEN", write)
        monkeypatch.setattr("src.server.ALLOW_INSECURE_NO_AUTH", insecure)
        mw = BearerAuthMiddleware(app=lambda scope, receive, send: None)
        req = types.SimpleNamespace(
            url=types.SimpleNamespace(path="/mcp"),
            headers={"authorization": header} if header else {},
        )
        captured = {}
        sentinel = object()

        async def call_next(_request):
            captured["level"] = AUTH_LEVEL.get()
            return sentinel

        result = asyncio.run(mw.dispatch(req, call_next))
        return result, sentinel, captured

    def test_no_tokens_without_insecure_flag_is_denied(self, monkeypatch):
        result, sentinel, _ = self._dispatch(monkeypatch, insecure=False)
        assert result is not sentinel
        assert isinstance(result, JSONResponse)
        assert result.status_code == 503

    def test_no_tokens_with_insecure_flag_grants_write(self, monkeypatch):
        result, sentinel, captured = self._dispatch(monkeypatch, insecure=True)
        assert result is sentinel
        assert captured["level"] == "write"

    def test_write_token_match_grants_write(self, monkeypatch):
        result, sentinel, captured = self._dispatch(
            monkeypatch, write="w-secret", header="Bearer w-secret"
        )
        assert result is sentinel
        assert captured["level"] == "write"

    def test_read_token_match_grants_read(self, monkeypatch):
        result, sentinel, captured = self._dispatch(
            monkeypatch, read="r-secret", header="Bearer r-secret"
        )
        assert result is sentinel
        assert captured["level"] == "read"

    def test_bad_token_is_unauthorized(self, monkeypatch):
        result, sentinel, _ = self._dispatch(monkeypatch, write="w-secret", header="Bearer wrong")
        assert result is not sentinel
        assert isinstance(result, JSONResponse)
        assert result.status_code == 401


class TestTokenConstants:
    def test_read_token_from_env(self, monkeypatch):
        monkeypatch.setenv("MCP_API_TOKEN", "test-read-token")
        # Re-import to pick up new env var
        import importlib
        import src.auth

        importlib.reload(src.auth)
        assert src.auth.READ_TOKEN == "test-read-token"

    def test_write_token_from_env(self, monkeypatch):
        monkeypatch.setenv("MCP_WRITE_TOKEN", "test-write-token")
        import importlib
        import src.auth

        importlib.reload(src.auth)
        assert src.auth.WRITE_TOKEN == "test-write-token"

    def test_tokens_none_when_unset(self, monkeypatch):
        monkeypatch.delenv("MCP_API_TOKEN", raising=False)
        monkeypatch.delenv("MCP_WRITE_TOKEN", raising=False)
        import importlib
        import src.auth

        importlib.reload(src.auth)
        assert src.auth.READ_TOKEN is None
        assert src.auth.WRITE_TOKEN is None
