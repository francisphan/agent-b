"""Schema cache: idle keys stop being background-refreshed (issue #39)."""

import time

from src.schema_cache import SchemaCache


def _make_cache(**kwargs) -> SchemaCache:
    cache = SchemaCache(**kwargs)
    # Tests drive _refresh_all() directly — no background thread needed.
    cache._ensure_refresh_thread = lambda: None
    return cache


class _CountingFetcher:
    def __init__(self, value="v"):
        self.calls = 0
        self.value = value

    def __call__(self):
        self.calls += 1
        return self.value


class TestGetOrFetch:
    def test_first_access_fetches_then_serves_from_cache(self):
        cache = _make_cache(ttl=3600, refresh_interval=1800)
        fetcher = _CountingFetcher()
        assert cache._get_or_fetch("k", fetcher) == "v"
        assert cache._get_or_fetch("k", fetcher) == "v"
        assert fetcher.calls == 1

    def test_stale_entry_refetched_lazily(self):
        cache = _make_cache(ttl=3600, refresh_interval=1800)
        fetcher = _CountingFetcher()
        cache._get_or_fetch("k", fetcher)
        cache._store["k"].fetched_at = time.monotonic() - 7200  # force stale
        cache._get_or_fetch("k", fetcher)
        assert fetcher.calls == 2


class TestIdleRefresh:
    def test_active_key_is_refreshed(self):
        cache = _make_cache(ttl=3600, refresh_interval=1800, idle_after=3600)
        fetcher = _CountingFetcher()
        cache._get_or_fetch("k", fetcher)
        cache._refresh_all()
        assert fetcher.calls == 2

    def test_idle_key_is_skipped(self):
        cache = _make_cache(ttl=3600, refresh_interval=1800, idle_after=3600)
        fetcher = _CountingFetcher()
        cache._get_or_fetch("k", fetcher)
        cache._store["k"].last_accessed = time.monotonic() - 7200  # idle > idle_after
        cache._refresh_all()
        assert fetcher.calls == 1

    def test_background_refresh_does_not_count_as_access(self):
        # Refresh sweeps must not keep a key alive forever by resetting its
        # last-access time — that was the original bug's shape.
        cache = _make_cache(ttl=3600, refresh_interval=1800, idle_after=3600)
        fetcher = _CountingFetcher()
        cache._get_or_fetch("k", fetcher)
        accessed = cache._store["k"].last_accessed
        cache._refresh_all()
        assert cache._store["k"].last_accessed == accessed

    def test_access_after_idle_resumes_background_refresh(self):
        cache = _make_cache(ttl=3600, refresh_interval=1800, idle_after=3600)
        fetcher = _CountingFetcher()
        cache._get_or_fetch("k", fetcher)
        cache._store["k"].last_accessed = time.monotonic() - 7200
        cache._refresh_all()  # skipped
        assert fetcher.calls == 1
        cache._get_or_fetch("k", fetcher)  # cache hit — bumps last_accessed
        cache._refresh_all()  # eligible again
        assert fetcher.calls == 2

    def test_idle_default_is_twice_refresh_interval(self):
        cache = _make_cache(ttl=3600, refresh_interval=1800)
        assert cache._idle_after == 3600

    def test_stats_reports_idle_state(self):
        cache = _make_cache(ttl=3600, refresh_interval=1800, idle_after=3600)
        cache._get_or_fetch("k", _CountingFetcher())
        stats = cache.stats()
        assert stats["idle_after"] == 3600
        entry = stats["entries"]["k"]
        assert entry["background_refresh"] is True
        assert entry["idle_seconds"] < 1

        cache._store["k"].last_accessed = time.monotonic() - 7200
        assert cache.stats()["entries"]["k"]["background_refresh"] is False
