"""Tests for src/tool_logging.py — redaction and tool-dispatch logging."""

import asyncio
import json
import logging

import pytest

from src import tool_logging
from src.tool_logging import instrument, redact


class TestRedact:
    def test_masks_email_value(self):
        assert redact({"email": "jane.doe@example.com"}) == {"email": "j***@example.com"}

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

    @mcp.tool()
    def inband_error(email: str = "") -> dict:
        # Tools return handled failures in-band rather than raising.
        return {"error": "boom in band"}

    @mcp.tool()
    def list_error() -> list:
        # List-returning tools wrap the failure in a one-element list.
        return [{"error": "list boom"}]

    @mcp.tool()
    def degraded() -> dict:
        # Composite with a partial (per-leg) failure.
        return {"ok_part": 1, "_errors": ["Salesforce: nope", "OPERA: down"]}

    @mcp.tool()
    def data_rows() -> list:
        # A multi-row data result whose first row has a column named "error"
        # (e.g. a log/audit table) — must NOT be classified as a failure.
        return [{"error": "some log text", "id": 1}, {"id": 2}]

    @mcp.tool()
    def raises_with_email(email: str = "") -> dict:
        raise ValueError(f"no record for {email}")

    return mcp


class TestInstrument:
    def test_success_logs_and_writes_record_with_redacted_args(self, usage_path, caplog):
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
                asyncio.run(mcp._tool_manager.call_tool("lookup", {"email": "boom"}))

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
                asyncio.run(mcp._tool_manager.call_tool("lookup", {"email": "x@y.com"}))
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

        token = request_ctx.set(RequestContext("r1", None, None, None, request=_Req()))
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
        rc_token = request_ctx.set(RequestContext("r1", None, None, None, request=_Req()))
        try:
            with caplog.at_level("INFO", logger="agent_b.usage"):
                asyncio.run(mcp._tool_manager.call_tool("lookup", {"email": "x@y.com"}))
        finally:
            request_ctx.reset(rc_token)
            tool_logging.correlation_id.reset(cv_token)

        record = json.loads(usage_path.read_text().strip())
        assert record["corr"] == "turn-current"


class TestClassifyResult:
    """Unit tests for the status classifier across raw and converted payloads."""

    def test_raw_ok_dict(self):
        assert tool_logging._classify_result({"found": True}) == ("ok", None)

    def test_raw_error_dict(self):
        status, snip = tool_logging._classify_result({"error": "boom"})
        assert status == "error"
        assert snip == "boom"

    def test_falsy_error_value_is_ok(self):
        # A tool that returns error=None/"" is not a failure.
        assert tool_logging._classify_result({"error": None}) == ("ok", None)
        assert tool_logging._classify_result({"error": ""}) == ("ok", None)

    def test_single_element_error_list(self):
        # The ns_tools/opera_tools convention: [{"error": ...}].
        status, snip = tool_logging._classify_result([{"error": "list boom"}])
        assert status == "error"
        assert snip == "list boom"

    def test_multi_row_list_is_ok(self):
        # A normal multi-row list result (no leading error dict) stays ok.
        assert tool_logging._classify_result([{"id": 1}, {"id": 2}]) == ("ok", None)

    def test_multi_row_with_error_column_is_ok(self):
        # Regression: a multi-row data result whose first row has a column
        # literally named "error" (log/audit tables) must NOT be an error — only
        # a *single*-element list is treated as the error sentinel.
        rows = [{"error": "log text", "id": 1}, {"id": 2}]
        assert tool_logging._classify_result(rows) == ("ok", None)

    def test_empty_list_is_ok(self):
        assert tool_logging._classify_result([]) == ("ok", None)

    def test_degraded_on_nonempty_errors(self):
        status, snip = tool_logging._classify_result(
            {"ok_part": 1, "_errors": ["Salesforce: nope", "OPERA: down"]}
        )
        assert status == "degraded"
        assert snip == "Salesforce: nope; OPERA: down"

    def test_empty_errors_list_is_ok(self):
        assert tool_logging._classify_result({"_errors": []}) == ("ok", None)

    def test_error_wins_over_errors(self):
        status, _ = tool_logging._classify_result({"error": "hard fail", "_errors": ["leg"]})
        assert status == "error"

    def test_converted_content_block_error(self):
        # Live server path: convert_result=True → list[TextContent] whose .text
        # is the JSON-dumped return value.
        from mcp.types import TextContent

        blocks = [TextContent(type="text", text=json.dumps({"error": "boom"}))]
        status, snip = tool_logging._classify_result(blocks)
        assert status == "error"
        assert "boom" in snip

    def test_converted_content_block_ok(self):
        from mcp.types import TextContent

        blocks = [TextContent(type="text", text=json.dumps({"found": True}))]
        assert tool_logging._classify_result(blocks) == ("ok", None)

    def test_converted_content_block_degraded(self):
        # A composite's dict arrives as a single content block on the live path.
        from mcp.types import TextContent

        blocks = [TextContent(type="text", text=json.dumps({"_errors": ["NetSuite: 400"]}))]
        status, snip = tool_logging._classify_result(blocks)
        assert status == "degraded"
        assert "NetSuite: 400" in snip

    def test_converted_multi_row_data_is_ok(self):
        # Multiple content blocks == a multi-row data result, even if the first
        # row carries an "error" column — the live-path twin of the raw regression.
        from mcp.types import TextContent

        blocks = [
            TextContent(type="text", text=json.dumps({"error": "log text", "id": 1})),
            TextContent(type="text", text=json.dumps({"id": 2})),
        ]
        assert tool_logging._classify_result(blocks) == ("ok", None)

    def test_converted_structured_tuple(self):
        # Tool with an output schema → (unstructured, structured) tuple.
        assert tool_logging._classify_result(([], {"error": "boom"}))[0] == "error"

    def test_converted_wrap_output_tuple(self):
        # wrap_output nests the real value under "result".
        assert tool_logging._classify_result(([], {"result": {"error": "boom"}}))[0] == "error"

    def test_error_snippet_masks_email(self):
        status, snip = tool_logging._classify_result(
            {"error": "no customer for jane@vines.com found"}
        )
        assert status == "error"
        assert "jane@vines.com" not in snip
        assert "j***@vines.com" in snip

    def test_error_snippet_clipped(self):
        status, snip = tool_logging._classify_result({"error": "x" * 400})
        assert status == "error"
        assert "+100 chars" in snip


class TestInstrumentStatus:
    """The wrapper must record status=error/degraded for in-band failures."""

    def test_inband_error_dict_logs_status_error(self, usage_path, caplog):
        mcp = _build_mcp()
        instrument(mcp)
        with caplog.at_level("INFO", logger="agent_b.usage"):
            result = asyncio.run(mcp._tool_manager.call_tool("inband_error", {}))

        assert result == {"error": "boom in band"}  # tool return unchanged
        assert "status=error" in caplog.text
        assert "boom in band" in caplog.text
        record = json.loads(usage_path.read_text().strip())
        assert record["status"] == "error"
        assert "boom in band" in record["error"]
        # An in-band error is not a raised exception, so no error_type.
        assert "error_type" not in record
        # result_bytes is still recorded (schema stays additive).
        assert isinstance(record["result_bytes"], int)

    def test_list_error_first_element_logs_status_error(self, usage_path, caplog):
        mcp = _build_mcp()
        instrument(mcp)
        with caplog.at_level("INFO", logger="agent_b.usage"):
            asyncio.run(mcp._tool_manager.call_tool("list_error", {}))
        assert "status=error" in caplog.text
        record = json.loads(usage_path.read_text().strip())
        assert record["status"] == "error"
        assert "list boom" in record["error"]

    def test_degraded_composite_logs_status_degraded(self, usage_path, caplog):
        mcp = _build_mcp()
        instrument(mcp)
        with caplog.at_level("INFO", logger="agent_b.usage"):
            asyncio.run(mcp._tool_manager.call_tool("degraded", {}))
        assert "status=degraded" in caplog.text
        record = json.loads(usage_path.read_text().strip())
        assert record["status"] == "degraded"
        assert "Salesforce: nope" in record["error"]
        assert "OPERA: down" in record["error"]

    def test_success_still_ok_and_no_error_field(self, usage_path, caplog):
        mcp = _build_mcp()
        instrument(mcp)
        with caplog.at_level("INFO", logger="agent_b.usage"):
            asyncio.run(mcp._tool_manager.call_tool("lookup", {"email": "x@y.com"}))
        assert "status=ok" in caplog.text
        record = json.loads(usage_path.read_text().strip())
        assert record["status"] == "ok"
        assert "error" not in record

    def test_multi_row_data_with_error_column_stays_ok(self, usage_path, caplog):
        # End-to-end via the live convert_result=True path: a multi-row result
        # whose first row has an "error" column must not flip to status=error.
        mcp = _build_mcp()
        instrument(mcp)
        with caplog.at_level("INFO", logger="agent_b.usage"):
            asyncio.run(mcp.call_tool("data_rows", {}))
        assert "status=ok" in caplog.text
        record = json.loads(usage_path.read_text().strip())
        assert record["status"] == "ok"
        assert "error" not in record

    def test_raised_exception_snippet_masks_email(self, usage_path, caplog):
        # The raised-exception path must mask PII in its snippet too, in both the
        # stdout line and the JSONL "error" field.
        from mcp.server.fastmcp.exceptions import ToolError

        mcp = _build_mcp()
        instrument(mcp)
        with caplog.at_level("INFO", logger="agent_b.usage"):
            with pytest.raises(ToolError):
                asyncio.run(
                    mcp._tool_manager.call_tool("raises_with_email", {"email": "jane@vines.com"})
                )
        assert "jane@vines.com" not in caplog.text
        assert "j***@vines.com" in caplog.text
        record = json.loads(usage_path.read_text().strip())
        assert record["status"] == "error"
        assert "jane@vines.com" not in record["error"]
        assert "j***@vines.com" in record["error"]


class TestUsageStoreMirror:
    """_write_usage_record must also mirror into usage_store, best-effort."""

    def test_tool_call_mirrors_record_into_usage_store(self, usage_path, monkeypatch):
        from src import usage_store

        captured = []
        monkeypatch.setattr(usage_store, "record", captured.append)

        mcp = _build_mcp()
        instrument(mcp)
        asyncio.run(mcp._tool_manager.call_tool("lookup", {"email": "x@y.com"}))

        assert len(captured) == 1
        assert captured[0]["tool"] == "lookup"
        assert captured[0]["status"] == "ok"

    def test_raising_usage_store_record_does_not_break_call(self, usage_path, monkeypatch):
        from src import usage_store

        def boom(_rec):
            raise RuntimeError("redis blew up")

        monkeypatch.setattr(usage_store, "record", boom)

        mcp = _build_mcp()
        instrument(mcp)
        result = asyncio.run(mcp._tool_manager.call_tool("lookup", {"email": "x@y.com"}))

        # Tool return is unchanged and the JSONL record was still written.
        assert result == {"found": True, "email": "x@y.com"}
        record = json.loads(usage_path.read_text().strip())
        assert record["status"] == "ok"


@pytest.fixture
def restore_log_levels():
    """Snapshot and restore levels the configure_logging tests mutate."""
    names = [
        "",
        "agent_b.usage",
        "some.random.logger",
        *tool_logging._LOGGER_LEVEL_DEFAULTS,
    ]
    saved = {n: logging.getLogger(n).level for n in names}
    try:
        yield
    finally:
        for name, level in saved.items():
            logging.getLogger(name).setLevel(level)


class TestConfigureLogging:
    def test_per_logger_defaults_applied(self, monkeypatch, restore_log_levels):
        monkeypatch.delenv("LOG_LEVEL", raising=False)
        monkeypatch.delenv("LOG_LEVEL_OVERRIDES", raising=False)
        for name in tool_logging._LOGGER_LEVEL_DEFAULTS:
            logging.getLogger(name).setLevel(logging.NOTSET)

        tool_logging.configure_logging()

        for name, want in tool_logging._LOGGER_LEVEL_DEFAULTS.items():
            assert logging.getLogger(name).level == getattr(logging, want)

    def test_noisy_loggers_default_to_warning(self, monkeypatch, restore_log_levels):
        monkeypatch.delenv("LOG_LEVEL", raising=False)
        monkeypatch.delenv("LOG_LEVEL_OVERRIDES", raising=False)
        tool_logging.configure_logging()
        for name in ("uvicorn.access", "httpx", "httpcore", "mcp.server.lowlevel"):
            assert logging.getLogger(name).level == logging.WARNING

    def test_uvicorn_error_stays_info_for_startup_banner(self, monkeypatch, restore_log_levels):
        # LOG_LEVEL=WARNING must not hide the "Uvicorn running on ..." banner.
        monkeypatch.setenv("LOG_LEVEL", "WARNING")
        monkeypatch.delenv("LOG_LEVEL_OVERRIDES", raising=False)
        tool_logging.configure_logging()
        assert logging.getLogger("uvicorn.error").level == logging.INFO

    def test_override_via_env(self, monkeypatch, restore_log_levels):
        monkeypatch.setenv("LOG_LEVEL_OVERRIDES", "uvicorn.access=INFO, httpx=DEBUG")
        tool_logging.configure_logging()
        assert logging.getLogger("uvicorn.access").level == logging.INFO
        assert logging.getLogger("httpx").level == logging.DEBUG
        # A logger not named in the override keeps the WARNING default.
        assert logging.getLogger("httpcore").level == logging.WARNING

    def test_override_can_add_arbitrary_logger(self, monkeypatch, restore_log_levels):
        monkeypatch.setenv("LOG_LEVEL_OVERRIDES", "some.random.logger=ERROR")
        tool_logging.configure_logging()
        assert logging.getLogger("some.random.logger").level == logging.ERROR

    def test_malformed_overrides_are_ignored(self, monkeypatch, restore_log_levels):
        monkeypatch.setenv("LOG_LEVEL_OVERRIDES", "garbage,,=INFO,httpx=DEBUG")
        tool_logging.configure_logging()  # must not raise
        assert logging.getLogger("httpx").level == logging.DEBUG

    def test_invalid_log_level_does_not_crash(self, monkeypatch, restore_log_levels):
        # BASIC_FORMAT is a real logging attribute but not a level — the old
        # getattr guard let it through and setLevel raised at import.
        monkeypatch.setenv("LOG_LEVEL", "BASIC_FORMAT")
        monkeypatch.delenv("LOG_LEVEL_OVERRIDES", raising=False)
        tool_logging.configure_logging()  # must not raise
        assert logging.getLogger().level == logging.INFO  # fell back to default

    def test_invalid_override_level_falls_back_to_default(self, monkeypatch, restore_log_levels):
        # A garbage level on a default logger falls back to that logger's default.
        monkeypatch.setenv("LOG_LEVEL_OVERRIDES", "uvicorn.access=BASIC_FORMAT")
        tool_logging.configure_logging()  # must not raise
        assert logging.getLogger("uvicorn.access").level == logging.WARNING

    def test_invalid_override_level_on_arbitrary_logger_is_skipped(
        self, monkeypatch, restore_log_levels
    ):
        logging.getLogger("some.random.logger").setLevel(logging.NOTSET)
        monkeypatch.setenv("LOG_LEVEL_OVERRIDES", "some.random.logger=NOPE")
        tool_logging.configure_logging()  # must not raise
        # Unresolvable level for a non-default logger → left untouched.
        assert logging.getLogger("some.random.logger").level == logging.NOTSET

    def test_usage_logger_stays_info_regardless_of_root(self, monkeypatch, restore_log_levels):
        monkeypatch.setenv("LOG_LEVEL", "ERROR")
        monkeypatch.delenv("LOG_LEVEL_OVERRIDES", raising=False)
        tool_logging.configure_logging()
        assert logging.getLogger().level == logging.ERROR
        assert logging.getLogger("agent_b.usage").level == logging.INFO

    def test_usage_logger_pin_beats_override(self, monkeypatch, restore_log_levels):
        # Even an explicit override can't silence the usage log.
        monkeypatch.setenv("LOG_LEVEL_OVERRIDES", "agent_b.usage=ERROR")
        tool_logging.configure_logging()
        assert logging.getLogger("agent_b.usage").level == logging.INFO


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

        assert (
            client.get("/echo", headers={"X-Correlation-ID": "turn-1"}).json()["corr"] == "turn-1"
        )
        # Next request carries no header — must be None, not "turn-1".
        assert client.get("/echo").json()["corr"] is None
