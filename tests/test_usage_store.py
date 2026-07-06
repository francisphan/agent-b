"""Tests for src/usage_store.py — the best-effort Redis usage mirror.

No real Redis and no fakeredis dependency: a hand-rolled FakeRedis records the
commands it receives so we can assert on RPUSH/LTRIM/LRANGE behaviour.
"""

import json

from src import usage_store


class FakeRedis:
    """Minimal stand-in recording every command; can be told to raise."""

    def __init__(self, items=None, raise_on=None):
        self.items = list(items or [])
        self.calls = []
        self.raise_on = set(raise_on or ())

    def rpush(self, key, value):
        self.calls.append(("rpush", key, value))
        if "rpush" in self.raise_on:
            raise RuntimeError("redis down")
        self.items.append(value)

    def ltrim(self, key, start, end):
        self.calls.append(("ltrim", key, start, end))
        if "ltrim" in self.raise_on:
            raise RuntimeError("redis down")

    def lrange(self, key, start, end):
        self.calls.append(("lrange", key, start, end))
        if "lrange" in self.raise_on:
            raise RuntimeError("redis down")
        return list(self.items)


class TestRecord:
    def test_noops_without_redis_url(self, monkeypatch):
        # Even with a client sitting in the box, no REDIS_URL means no work —
        # _get_client short-circuits on the env var before touching the box.
        spy = FakeRedis()
        monkeypatch.setattr(usage_store, "_client_box", [spy])
        monkeypatch.delenv("REDIS_URL", raising=False)

        usage_store.record({"tool": "sf_soql_query", "ts": "2026-07-01T00:00:00+00:00"})

        assert spy.calls == []

    def test_swallows_a_raising_client(self, monkeypatch):
        spy = FakeRedis(raise_on={"rpush"})
        monkeypatch.setattr(usage_store, "_get_client", lambda: spy)
        # Must not raise despite the client blowing up on rpush.
        usage_store.record({"tool": "sf_soql_query", "ts": 1000.0})

    def test_rpush_then_ltrim_with_cap(self, monkeypatch):
        spy = FakeRedis()
        monkeypatch.setattr(usage_store, "_get_client", lambda: spy)

        usage_store.record({"tool": "sf_soql_query", "ts": 1234.5})

        kinds = [c[0] for c in spy.calls]
        assert kinds == ["rpush", "ltrim"]
        assert spy.calls[0][1] == usage_store.USAGE_KEY
        # LTRIM keeps only the most recent MAX_RECORDS elements.
        assert spy.calls[1] == ("ltrim", usage_store.USAGE_KEY, -usage_store.MAX_RECORDS, -1)

    def test_adds_epoch_ts_from_iso_string(self, monkeypatch):
        from datetime import datetime, timezone

        spy = FakeRedis()
        monkeypatch.setattr(usage_store, "_get_client", lambda: spy)

        usage_store.record({"tool": "t", "ts": "2026-07-01T00:00:00+00:00"})

        stored = json.loads(spy.items[0])
        assert isinstance(stored["ts"], float)
        expected = datetime(2026, 7, 1, tzinfo=timezone.utc).timestamp()
        assert stored["ts"] == expected

    def test_preserves_numeric_ts(self, monkeypatch):
        spy = FakeRedis()
        monkeypatch.setattr(usage_store, "_get_client", lambda: spy)

        usage_store.record({"tool": "t", "ts": 42.0})

        assert json.loads(spy.items[0])["ts"] == 42.0

    def test_does_not_mutate_caller_record(self, monkeypatch):
        spy = FakeRedis()
        monkeypatch.setattr(usage_store, "_get_client", lambda: spy)
        rec = {"tool": "t", "ts": "2026-07-01T00:00:00+00:00"}

        usage_store.record(rec)

        # The ISO ts the JSONL writer built is left untouched.
        assert rec["ts"] == "2026-07-01T00:00:00+00:00"


class AsyncFakeRedis:
    """Async stand-in for redis.asyncio, recording awaited commands."""

    def __init__(self, raise_on=None):
        self.items = []
        self.calls = []
        self.raise_on = set(raise_on or ())

    async def rpush(self, key, value):
        self.calls.append(("rpush", key, value))
        if "rpush" in self.raise_on:
            raise RuntimeError("redis down")
        self.items.append(value)

    async def ltrim(self, key, start, end):
        self.calls.append(("ltrim", key, start, end))
        if "ltrim" in self.raise_on:
            raise RuntimeError("redis down")


class TestRecordAsync:
    async def test_noops_without_redis_url(self, monkeypatch):
        spy = AsyncFakeRedis()
        monkeypatch.setattr(usage_store, "_aclient_box", [spy])
        monkeypatch.delenv("REDIS_URL", raising=False)

        await usage_store.record_async({"tool": "t", "ts": 1.0})

        assert spy.calls == []

    async def test_rpush_then_ltrim_with_cap(self, monkeypatch):
        spy = AsyncFakeRedis()
        monkeypatch.setattr(usage_store, "_get_async_client", lambda: spy)

        await usage_store.record_async({"tool": "t", "ts": 5.0})

        assert [c[0] for c in spy.calls] == ["rpush", "ltrim"]
        assert spy.calls[1] == ("ltrim", usage_store.USAGE_KEY, -usage_store.MAX_RECORDS, -1)

    async def test_swallows_raising_client(self, monkeypatch):
        spy = AsyncFakeRedis(raise_on={"rpush"})
        monkeypatch.setattr(usage_store, "_get_async_client", lambda: spy)
        await usage_store.record_async({"tool": "t", "ts": 1.0})  # must not raise

    async def test_stamps_epoch_ts(self, monkeypatch):
        spy = AsyncFakeRedis()
        monkeypatch.setattr(usage_store, "_get_async_client", lambda: spy)

        await usage_store.record_async({"tool": "t", "ts": "2026-07-01T00:00:00+00:00"})

        stored = json.loads(spy.items[0])
        assert isinstance(stored["ts"], float)


class TestFetch:
    def test_filters_by_ts(self, monkeypatch):
        items = [
            json.dumps({"tool": "old", "ts": 100.0}),
            json.dumps({"tool": "mid", "ts": 200.0}),
            json.dumps({"tool": "new", "ts": 300.0}),
        ]
        spy = FakeRedis(items=items)
        monkeypatch.setattr(usage_store, "_get_client", lambda: spy)

        out = usage_store.fetch(200.0)

        assert [r["tool"] for r in out] == ["mid", "new"]
        assert spy.calls[0] == ("lrange", usage_store.USAGE_KEY, 0, -1)

    def test_skips_undecodable_and_ts_less_records(self, monkeypatch):
        items = [
            "not json",
            json.dumps({"tool": "no_ts"}),
            json.dumps({"tool": "keep", "ts": 500.0}),
        ]
        spy = FakeRedis(items=items)
        monkeypatch.setattr(usage_store, "_get_client", lambda: spy)

        out = usage_store.fetch(0.0)
        assert [r["tool"] for r in out] == ["keep"]

    def test_returns_empty_without_redis_url(self, monkeypatch):
        monkeypatch.setattr(usage_store, "_client_box", [None])
        monkeypatch.delenv("REDIS_URL", raising=False)
        assert usage_store.fetch(0.0) == []

    def test_propagates_redis_errors(self, monkeypatch):
        spy = FakeRedis(raise_on={"lrange"})
        monkeypatch.setattr(usage_store, "_get_client", lambda: spy)
        # Unlike record(), fetch() must let the caller see the outage.
        import pytest

        with pytest.raises(RuntimeError):
            usage_store.fetch(0.0)
