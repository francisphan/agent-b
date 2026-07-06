"""Tests for the /usage/report custom route in src/server.py.

The route does its own bearer check (custom Starlette routes aren't behind the
SDK/legacy auth middleware) and reads the Redis usage mirror. We drive the
handler directly with a lightweight fake request, monkeypatching the module's
tokens and usage_store.fetch — the same style as tests/test_auth.py.
"""

import asyncio
import json
import time
import types

from src import server, usage_store


def _req(headers=None, query=None):
    return types.SimpleNamespace(headers=headers or {}, query_params=query or {})


def _set_tokens(monkeypatch, read=None, write=None):
    monkeypatch.setattr(server, "READ_TOKEN", read)
    monkeypatch.setattr(server, "WRITE_TOKEN", write)


class TestUsageReportRoute:
    def test_401_without_bearer_when_token_set(self, monkeypatch):
        _set_tokens(monkeypatch, read="r-secret")
        resp = asyncio.run(server.usage_report_route(_req()))
        assert resp.status_code == 401

    def test_401_with_wrong_bearer(self, monkeypatch):
        _set_tokens(monkeypatch, read="r-secret")
        resp = asyncio.run(
            server.usage_report_route(_req(headers={"authorization": "Bearer nope"}))
        )
        assert resp.status_code == 401

    def test_200_with_read_token(self, monkeypatch):
        _set_tokens(monkeypatch, read="r-secret")
        monkeypatch.setattr(
            usage_store,
            "fetch",
            lambda since: [
                {
                    "tool": "sf_soql_query",
                    "status": "ok",
                    "duration_ms": 12,
                    "auth": "read",
                    "ts": time.time() - 3600,
                }
            ],
        )
        resp = asyncio.run(
            server.usage_report_route(_req(headers={"authorization": "Bearer r-secret"}))
        )
        assert resp.status_code == 200
        body = json.loads(resp.body)
        assert body["totals"]["calls"] == 1
        assert body["days"] == 7
        assert "per_tool" in body and "per_day" in body and "prev_totals" in body
        assert body["per_tool"][0]["tool"] == "sf_soql_query"

    def test_write_token_also_allowed(self, monkeypatch):
        _set_tokens(monkeypatch, read="r-secret", write="w-secret")
        monkeypatch.setattr(usage_store, "fetch", lambda since: [])
        resp = asyncio.run(
            server.usage_report_route(_req(headers={"authorization": "Bearer w-secret"}))
        )
        assert resp.status_code == 200

    def test_503_on_fetch_error(self, monkeypatch):
        _set_tokens(monkeypatch, read="r-secret")

        def boom(_since):
            raise RuntimeError("redis down")

        monkeypatch.setattr(usage_store, "fetch", boom)
        resp = asyncio.run(
            server.usage_report_route(_req(headers={"authorization": "Bearer r-secret"}))
        )
        assert resp.status_code == 503
        assert "error" in json.loads(resp.body)

    def test_open_when_no_tokens_configured(self, monkeypatch):
        _set_tokens(monkeypatch, read=None, write=None)
        monkeypatch.setattr(usage_store, "fetch", lambda since: [])
        resp = asyncio.run(server.usage_report_route(_req()))
        assert resp.status_code == 200

    def test_days_clamped_to_30(self, monkeypatch):
        _set_tokens(monkeypatch, read=None, write=None)
        monkeypatch.setattr(usage_store, "fetch", lambda since: [])
        resp = asyncio.run(server.usage_report_route(_req(query={"days": "999"})))
        assert json.loads(resp.body)["days"] == 30

    def test_bad_days_falls_back_to_default(self, monkeypatch):
        _set_tokens(monkeypatch, read=None, write=None)
        monkeypatch.setattr(usage_store, "fetch", lambda since: [])
        resp = asyncio.run(server.usage_report_route(_req(query={"days": "abc"})))
        assert json.loads(resp.body)["days"] == 7

    def test_splits_current_and_previous_window(self, monkeypatch):
        _set_tokens(monkeypatch, read=None, write=None)
        now = time.time()
        # One record in the current 7d window, one in the previous window.
        monkeypatch.setattr(
            usage_store,
            "fetch",
            lambda since: [
                {
                    "tool": "cur",
                    "status": "ok",
                    "duration_ms": 5,
                    "auth": "read",
                    "ts": now - 3600,
                },
                {
                    "tool": "prev",
                    "status": "ok",
                    "duration_ms": 5,
                    "auth": "read",
                    "ts": now - 8 * 86400,
                },
            ],
        )
        body = json.loads(asyncio.run(server.usage_report_route(_req())).body)
        assert body["totals"]["calls"] == 1
        assert body["prev_totals"]["calls"] == 1
        assert body["per_tool"][0]["tool"] == "cur"
