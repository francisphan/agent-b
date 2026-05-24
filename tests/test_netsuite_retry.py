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
