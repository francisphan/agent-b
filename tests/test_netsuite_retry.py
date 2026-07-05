"""Tests for transient transport-error retry in NetSuiteClient._request_sync."""

import types
from unittest.mock import MagicMock, patch

import httpx
import pytest

from src.netsuite.client import NetSuiteClient


def _client(max_retries=3):
    """Build a NetSuiteClient with a mocked httpx client, bypassing env config."""
    c = NetSuiteClient.__new__(NetSuiteClient)
    c._config = types.SimpleNamespace(max_retries=max_retries, retry_backoff_factor=0.0)
    http = MagicMock()
    http.is_closed = False
    c._sync_client = http
    return c, http


def _ok():
    r = MagicMock()
    r.status_code = 200
    r.content = b'{"ok": 1}'
    r.json.return_value = {"ok": 1}
    return r


def _err(status, *, title="Invalid request", detail="bad query"):
    body = {
        "type": "https://example.com/error",
        "title": title,
        "status": status,
        "o:errorDetails": [{"detail": detail, "o:errorCode": "INVALID_REQUEST"}],
    }
    r = MagicMock()
    r.status_code = status
    r.content = b"{}"
    r.json.return_value = body
    import json as _json

    r.text = _json.dumps(body)
    return r


@patch("src.netsuite.client.time.sleep")
class TestTransportRetry:
    def test_connect_error_then_success_is_retried(self, _sleep):
        c, http = _client()
        http.request.side_effect = [httpx.ConnectError("reset"), _ok()]
        assert c._request_sync("GET", "/x") == {"ok": 1}
        assert http.request.call_count == 2

    def test_read_timeout_not_retried_for_non_idempotent(self, _sleep):
        # A POST read-timeout may have been applied server-side — don't replay it.
        c, http = _client()
        http.request.side_effect = httpx.ReadTimeout("slow")
        with pytest.raises(httpx.ReadTimeout):
            c._request_sync("POST", "/record")
        assert http.request.call_count == 1

    def test_read_timeout_retried_for_idempotent_get(self, _sleep):
        c, http = _client()
        http.request.side_effect = [httpx.ReadTimeout("slow"), _ok()]
        assert c._request_sync("GET", "/x") == {"ok": 1}
        assert http.request.call_count == 2

    def test_connect_error_retried_even_for_post(self, _sleep):
        # Connect-phase failure never reached the server — safe for any verb.
        c, http = _client()
        http.request.side_effect = [httpx.ConnectError("dns"), _ok()]
        assert c._request_sync("POST", "/record") == {"ok": 1}
        assert http.request.call_count == 2

    def test_exhausts_retries_then_raises(self, _sleep):
        c, http = _client(max_retries=3)
        http.request.side_effect = httpx.ConnectError("down")
        with pytest.raises(httpx.ConnectError):
            c._request_sync("GET", "/x")
        assert http.request.call_count == 4  # initial + 3 retries


@patch("src.netsuite.client.time.sleep")
class TestNonSuccessLogging:
    def test_400_logs_query_and_error_body_at_error(self, _sleep, caplog):
        from src.netsuite.exceptions import ValidationError

        c, http = _client()
        http.request.return_value = _err(400, detail="Invalid search query")
        sql = "SELECT id FROM nonexistent_table"
        with caplog.at_level("WARNING", logger="src.netsuite.client"):
            with pytest.raises(ValidationError):
                c._request_sync(
                    "POST", "/services/rest/query/v1/suiteql", json={"q": sql}
                )
        # Terminal (non-retryable) 400 logs at ERROR with the query + NetSuite body.
        assert http.request.call_count == 1
        rec = next(r for r in caplog.records if r.levelname == "ERROR")
        msg = rec.getMessage()
        assert "HTTP 400" in msg
        assert sql in msg  # the offending SuiteQL query
        assert "Invalid search query" in msg  # NetSuite's error detail

    def test_500_retry_logs_at_warning(self, _sleep, caplog):
        c, http = _client(max_retries=1)
        http.request.side_effect = [_err(500, title="Server error"), _ok()]
        with caplog.at_level("WARNING", logger="src.netsuite.client"):
            assert c._request_sync("GET", "/x") == {"ok": 1}
        # The retried attempt is logged at WARNING, not ERROR.
        assert any(r.levelname == "WARNING" for r in caplog.records)
        assert not any(r.levelname == "ERROR" for r in caplog.records)
        assert "will retry" in caplog.text


def _nonjson_err(status, text):
    """An error response whose body is not JSON (e.g. an HTML gateway page)."""
    r = MagicMock()
    r.status_code = status
    r.content = text.encode()
    r.json.side_effect = ValueError("no json")
    r.text = text
    return r


@patch("src.netsuite.client.time.sleep")
class TestErrorMessageDetail:
    """The raised exception's message must carry NetSuite's actionable detail.

    Every MCP tool returns ``{"error": str(e)}``, so whatever ends up in the
    message is all an LLM agent sees. A bare "Bad Request" made it retry blind.
    """

    def test_400_message_includes_error_detail(self, _sleep):
        from src.netsuite.exceptions import ValidationError

        c, http = _client()
        detail = (
            "Search error occurred: Failed to parse SQL query. "
            "Encountered syntax error ... near: LIMIT"
        )
        http.request.return_value = _err(400, title="Bad Request", detail=detail)
        with pytest.raises(ValidationError) as exc_info:
            c._request_sync(
                "POST", "/services/rest/query/v1/suiteql", json={"q": "..."}
            )
        msg = str(exc_info.value)
        assert "Bad Request" in msg  # title prefix preserved
        assert detail in msg  # NetSuite's actionable detail
        # error_details attribute stays intact for programmatic use.
        assert exc_info.value.error_details
        assert exc_info.value.error_details[0]["detail"] == detail

    def test_multiple_details_are_joined(self, _sleep):
        from src.netsuite.exceptions import ValidationError

        body = {
            "title": "Bad Request",
            "status": 400,
            "o:errorDetails": [
                {"detail": "first problem", "o:errorCode": "E1"},
                {"detail": "second problem", "o:errorCode": "E2"},
            ],
        }
        r = MagicMock()
        r.status_code = 400
        r.content = b"{}"
        r.json.return_value = body
        import json as _json

        r.text = _json.dumps(body)

        c, http = _client()
        http.request.return_value = r
        with pytest.raises(ValidationError) as exc_info:
            c._request_sync("POST", "/x", json={"q": "..."})
        assert "first problem; second problem" in str(exc_info.value)

    def test_long_detail_is_clipped(self, _sleep):
        from src.netsuite.exceptions import ValidationError

        c, http = _client()
        http.request.return_value = _err(400, detail="x" * 900)
        with pytest.raises(ValidationError) as exc_info:
            c._request_sync("POST", "/x", json={"q": "..."})
        msg = str(exc_info.value)
        assert "+400 chars" in msg  # 900 - 500 (_MAX_ERROR_DETAIL) truncated
        assert len(msg) < 900

    def test_non_json_body_appended_to_message(self, _sleep):
        from src.netsuite.exceptions import NetSuiteError

        c, http = _client()
        http.request.return_value = _nonjson_err(400, "<html>Gateway problem</html>")
        with pytest.raises(NetSuiteError) as exc_info:
            c._request_sync("POST", "/x", json={"q": "..."})
        msg = str(exc_info.value)
        assert "HTTP 400" in msg
        assert "Gateway problem" in msg

    def test_non_json_empty_body_falls_back_to_status(self, _sleep):
        from src.netsuite.exceptions import NetSuiteError

        c, http = _client()
        http.request.return_value = _nonjson_err(503, "")
        with pytest.raises(NetSuiteError) as exc_info:
            # 503 with max_retries exhausted still surfaces the built exception.
            c._request_sync("POST", "/x", json={"q": "..."})
        assert str(exc_info.value) == "HTTP 503"
