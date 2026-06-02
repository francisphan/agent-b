"""Tests for src/tool_logging.py — redaction and tool-dispatch logging."""

import asyncio
import json

import pytest

from src import tool_logging
from src.tool_logging import instrument, redact


class TestRedact:
    def test_masks_email_value(self):
        assert redact({"email": "jane.doe@example.com"}) == {
            "email": "j***@example.com"
        }

    def test_masks_email_inside_query_string_but_keeps_structure(self):
        out = redact({"query_str": "SELECT Id FROM Contact WHERE Email='bob@vines.com'"})
        assert "SELECT Id FROM Contact" in out["query_str"]
        assert "bob@vines.com" not in out["query_str"]
        assert "b***@vines.com" in out["query_str"]

    def test_masks_name_under_sensitive_key(self):
        assert redact({"last_name": "Smith"}) == {"last_name": "S***h"}

    def test_keeps_structural_args(self):
        # object_name / record_type must NOT be masked — we want them for analysis.
        args = {"object_name": "TVRS_Guest__c", "record_type": "customer", "limit": 50}
        assert redact(args) == args

    def test_truncates_long_value(self):
        long = "x" * 1000
        out = redact({"note": long})["note"]
        assert out.startswith("x" * 500)
        assert "+500 chars" in out

    def test_recurses_into_nested(self):
        out = redact({"filters": [{"email": "a@b.com"}]})
        assert out == {"filters": [{"email": "a***@b.com"}]}


@pytest.fixture
def usage_path(tmp_path, monkeypatch):
    p = tmp_path / "usage.jsonl"
    monkeypatch.setenv("TOOL_USAGE_LOG", str(p))
    return p


def _build_mcp():
    from mcp.server.fastmcp import FastMCP

    mcp = FastMCP("test")

    @mcp.tool()
    def lookup(email: str = "") -> dict:
        if email == "boom":
            raise ValueError("kaboom")
        return {"found": True, "email": email}

    return mcp


class TestInstrument:
    def test_success_logs_and_writes_record_with_redacted_args(
        self, usage_path, caplog
    ):
        mcp = _build_mcp()
        instrument(mcp)

        with caplog.at_level("INFO", logger="agent_b.usage"):
            result = asyncio.run(
                mcp._tool_manager.call_tool("lookup", {"email": "jane@vines.com"})
            )

        assert result == {"found": True, "email": "jane@vines.com"}  # tool unchanged

        # Human-readable line
        assert "tool=lookup" in caplog.text
        assert "status=ok" in caplog.text
        assert "jane@vines.com" not in caplog.text  # raw PII not logged
        assert "j***@vines.com" in caplog.text  # redacted form is

        # Structured JSONL record
        record = json.loads(usage_path.read_text().strip())
        assert record["tool"] == "lookup"
        assert record["status"] == "ok"
        assert isinstance(record["duration_ms"], int)
        assert record["args"] == {"email": "j***@vines.com"}

    def test_error_is_logged_and_reraised(self, usage_path, caplog):
        from mcp.server.fastmcp.exceptions import ToolError

        mcp = _build_mcp()
        instrument(mcp)

        with caplog.at_level("INFO", logger="agent_b.usage"):
            # FastMCP wraps tool exceptions in ToolError; the original message
            # ("kaboom") is preserved. The wrapper must re-raise it unchanged.
            with pytest.raises(ToolError, match="kaboom"):
                asyncio.run(
                    mcp._tool_manager.call_tool("lookup", {"email": "boom"})
                )

        assert "status=error" in caplog.text
        record = json.loads(usage_path.read_text().strip())
        assert record["status"] == "error"
        assert record["error_type"] == "ToolError"

    def test_write_record_never_raises_on_bad_path(self, tmp_path, monkeypatch):
        # A broken TOOL_USAGE_LOG path (parent is a file, not a dir) must not
        # break tool calls — the write is best-effort.
        blocker = tmp_path / "iam_a_file"
        blocker.write_text("x")
        monkeypatch.setenv("TOOL_USAGE_LOG", str(blocker / "bad.jsonl"))
        tool_logging._write_usage_record({"tool": "x"})  # should swallow the error

    def test_correlation_id_is_logged_and_recorded(self, usage_path, caplog):
        mcp = _build_mcp()
        instrument(mcp)

        token = tool_logging.correlation_id.set("turn-abc123")
        try:
            with caplog.at_level("INFO", logger="agent_b.usage"):
                asyncio.run(
                    mcp._tool_manager.call_tool("lookup", {"email": "x@y.com"})
                )
        finally:
            tool_logging.correlation_id.reset(token)

        assert "corr=turn-abc123" in caplog.text
        record = json.loads(usage_path.read_text().strip())
        assert record["corr"] == "turn-abc123"

    def test_correlation_id_absent_logs_none(self, usage_path, caplog):
        mcp = _build_mcp()
        instrument(mcp)
        with caplog.at_level("INFO", logger="agent_b.usage"):
            asyncio.run(mcp._tool_manager.call_tool("lookup", {"email": "x@y.com"}))
        record = json.loads(usage_path.read_text().strip())
        assert record["corr"] is None

    def test_correlation_id_read_from_request_ctx(self, usage_path, caplog):
        # The dispatcher must read the header off the SDK's request_ctx (set in
        # the same task as the tool handler), not just the middleware contextvar.
        from mcp.server.lowlevel.server import request_ctx
        from mcp.shared.context import RequestContext

        class _Req:
            headers = {"x-correlation-id": "turn-from-request"}

        mcp = _build_mcp()
        instrument(mcp)

        token = request_ctx.set(
            RequestContext("r1", None, None, None, request=_Req())
        )
        try:
            with caplog.at_level("INFO", logger="agent_b.usage"):
                asyncio.run(mcp._tool_manager.call_tool("lookup", {"email": "x@y.com"}))
        finally:
            request_ctx.reset(token)

        record = json.loads(usage_path.read_text().strip())
        assert record["corr"] == "turn-from-request"

    def test_request_ctx_overrides_stale_contextvar(self, usage_path, caplog):
        # Even if the middleware contextvar holds a stale ID, the per-request
        # value wins — this is the exact bug that logged a prior turn's ID.
        from mcp.server.lowlevel.server import request_ctx
        from mcp.shared.context import RequestContext

        class _Req:
            headers = {"x-correlation-id": "turn-current"}

        mcp = _build_mcp()
        instrument(mcp)

        cv_token = tool_logging.correlation_id.set("turn-STALE")
        rc_token = request_ctx.set(
            RequestContext("r1", None, None, None, request=_Req())
        )
        try:
            with caplog.at_level("INFO", logger="agent_b.usage"):
                asyncio.run(mcp._tool_manager.call_tool("lookup", {"email": "x@y.com"}))
        finally:
            request_ctx.reset(rc_token)
            tool_logging.correlation_id.reset(cv_token)

        record = json.loads(usage_path.read_text().strip())
        assert record["corr"] == "turn-current"


class TestCorrelationIdMiddleware:
    def test_header_sets_contextvar_for_downstream(self):
        from starlette.applications import Starlette
        from starlette.responses import JSONResponse
        from starlette.routing import Route
        from starlette.testclient import TestClient

        from src.tool_logging import CorrelationIdMiddleware, correlation_id

        async def echo(request):
            return JSONResponse({"corr": correlation_id.get()})

        app = Starlette(routes=[Route("/echo", echo)])
        app.add_middleware(CorrelationIdMiddleware)
        client = TestClient(app)

        assert client.get("/echo").json()["corr"] is None
        assert (
            client.get("/echo", headers={"X-Correlation-ID": "turn-xyz"}).json()["corr"]
            == "turn-xyz"
        )

    def test_overlong_header_is_clipped(self):
        from starlette.applications import Starlette
        from starlette.responses import JSONResponse
        from starlette.routing import Route
        from starlette.testclient import TestClient

        from src.tool_logging import CorrelationIdMiddleware, correlation_id

        async def echo(request):
            return JSONResponse({"corr": correlation_id.get()})

        app = Starlette(routes=[Route("/echo", echo)])
        app.add_middleware(CorrelationIdMiddleware)
        client = TestClient(app)

        got = client.get("/echo", headers={"X-Correlation-ID": "z" * 200}).json()["corr"]
        assert len(got) == 64

    def test_missing_header_does_not_leak_previous_value(self):
        # A header-less request must reset the contextvar to None rather than
        # inherit the prior request's ID (the staleness bug).
        from starlette.applications import Starlette
        from starlette.responses import JSONResponse
        from starlette.routing import Route
        from starlette.testclient import TestClient

        from src.tool_logging import CorrelationIdMiddleware, correlation_id

        async def echo(request):
            return JSONResponse({"corr": correlation_id.get()})

        app = Starlette(routes=[Route("/echo", echo)])
        app.add_middleware(CorrelationIdMiddleware)
        client = TestClient(app)

        assert client.get("/echo", headers={"X-Correlation-ID": "turn-1"}).json()["corr"] == "turn-1"
        # Next request carries no header — must be None, not "turn-1".
        assert client.get("/echo").json()["corr"] is None
