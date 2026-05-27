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
