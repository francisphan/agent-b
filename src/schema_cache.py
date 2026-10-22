"""Thread-safe TTL cache for live API schema calls with background refresh.

Caches the results of expensive describe/metadata API calls and refreshes
them on a configurable interval so callers always get fast reads without
stale-forever data.

Usage::

    from src.schema_cache import schema_cache

    # Reads are instant after first call; background thread keeps them fresh.
    desc = schema_cache.sf_describe("Account")
    objects = schema_cache.sf_list_objects()
    ns_types = schema_cache.ns_list_record_types()
    ns_schema = schema_cache.ns_get_record_schema("customer")

Keys that nobody reads anymore stop being background-refreshed: once a key
has gone unaccessed for SCHEMA_CACHE_IDLE seconds it is skipped by the sweep
(previously a single call kept its metadata API hit going every ~30 min for
the life of the process). The stale value stays cached; the next access
re-fetches lazily and resumes background refresh for that key.

Configuration (env vars):
    SCHEMA_CACHE_TTL        – seconds before an entry is considered stale (default 3600)
    SCHEMA_CACHE_REFRESH    – seconds between background refresh sweeps (default 1800)
    SCHEMA_CACHE_IDLE       – seconds without access before a key stops being
                              background-refreshed (default 2× refresh interval)
"""

import logging
import os
import threading
import time
from typing import Any, Callable

logger = logging.getLogger(__name__)

_DEFAULT_TTL = 3600  # 1 hour
_DEFAULT_REFRESH = 1800  # 30 minutes


class _CacheEntry:
    __slots__ = ("value", "fetched_at", "last_accessed")

    def __init__(self, value: Any, fetched_at: float) -> None:
        self.value = value
        self.fetched_at = fetched_at
        self.last_accessed = fetched_at


class SchemaCache:
    """Thread-safe TTL cache with background refresh for schema API calls.

    On first access the cache calls through to the live API, stores the result,
    and returns it.  A background daemon thread periodically re-fetches every
    key that has been accessed at least once, so subsequent reads are always
    served from memory.

    If a background refresh fails (e.g. API down), the stale value is kept and
    the refresh is retried on the next sweep.
    """

    def __init__(
        self,
        ttl: int | None = None,
        refresh_interval: int | None = None,
        idle_after: float | None = None,
    ) -> None:
        self._ttl = ttl or int(os.getenv("SCHEMA_CACHE_TTL", str(_DEFAULT_TTL)))
        self._refresh_interval = refresh_interval or int(
            os.getenv("SCHEMA_CACHE_REFRESH", str(_DEFAULT_REFRESH))
        )
        if idle_after is None:
            idle_env = os.getenv("SCHEMA_CACHE_IDLE")
            idle_after = float(idle_env) if idle_env else 2.0 * self._refresh_interval
        self._idle_after = idle_after
        self._lock = threading.Lock()
        self._store: dict[str, _CacheEntry] = {}

        # Registry of (key → fetcher) so the background thread knows how to
        # refresh each cached key.
        self._fetchers: dict[str, Callable[[], Any]] = {}

        self._refresh_thread: threading.Thread | None = None
        self._stop_event = threading.Event()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _get_or_fetch(self, key: str, fetcher: Callable[[], Any]) -> Any:
        """Return cached value if fresh, otherwise call *fetcher* and cache."""
        now = time.monotonic()
        with self._lock:
            entry = self._store.get(key)
            if entry is not None and (now - entry.fetched_at) < self._ttl:
                entry.last_accessed = now
                return entry.value

        # Cache miss or stale — call through (outside the lock to avoid blocking)
        value = fetcher()
        with self._lock:
            self._store[key] = _CacheEntry(value, time.monotonic())
            self._fetchers[key] = fetcher
        self._ensure_refresh_thread()
        return value

    def _ensure_refresh_thread(self) -> None:
        """Start the background refresh daemon if it isn't running yet."""
        if self._refresh_thread is not None and self._refresh_thread.is_alive():
            return
        self._stop_event.clear()
        t = threading.Thread(target=self._refresh_loop, daemon=True, name="schema-cache-refresh")
        t.start()
        self._refresh_thread = t

    def _refresh_loop(self) -> None:
        """Periodically re-fetch every registered key."""
        while not self._stop_event.is_set():
            self._stop_event.wait(self._refresh_interval)
            if self._stop_event.is_set():
                break
            self._refresh_all()

    def _refresh_all(self) -> None:
        """Re-fetch registered keys still in active use, keeping stale values on failure.

        Keys unaccessed for longer than idle_after are skipped: their stale
        value stays cached and the next access re-fetches lazily (which also
        makes them eligible for background refresh again).
        """
        now = time.monotonic()
        with self._lock:
            snapshot = {}
            for key, fetcher in self._fetchers.items():
                entry = self._store.get(key)
                if entry is not None and (now - entry.last_accessed) >= self._idle_after:
                    logger.debug("Schema cache key idle; skipping background refresh: %s", key)
                    continue
                snapshot[key] = fetcher

        for key, fetcher in snapshot.items():
            try:
                value = fetcher()
                with self._lock:
                    prev = self._store.get(key)
                    entry = _CacheEntry(value, time.monotonic())
                    if prev is not None:
                        entry.last_accessed = prev.last_accessed
                    self._store[key] = entry
                logger.debug("Schema cache refreshed: %s", key)
            except Exception:
                logger.warning(
                    "Schema cache refresh failed for %s; keeping stale value", key, exc_info=True
                )

    # ------------------------------------------------------------------
    # Public API — Salesforce
    # ------------------------------------------------------------------

    def sf_describe(self, object_name: str) -> dict:
        """Cached Salesforce describe_object call."""
        from src.sf_client import describe_object

        key = f"sf:describe:{object_name}"
        return self._get_or_fetch(key, lambda: describe_object(object_name))

    def sf_list_objects(self) -> list[str]:
        """Cached Salesforce list_objects call."""
        from src.sf_client import list_objects

        key = "sf:list_objects"
        return self._get_or_fetch(key, list_objects)

    # ------------------------------------------------------------------
    # Public API — NetSuite
    # ------------------------------------------------------------------

    def ns_list_record_types(self) -> dict:
        """Cached NetSuite list_record_types call."""
        from src.ns_client import list_record_types

        key = "ns:list_record_types"
        return self._get_or_fetch(key, list_record_types)

    def ns_get_record_schema(self, record_type: str) -> dict:
        """Cached NetSuite get_record_schema call."""
        from src.ns_client import get_record_schema

        key = f"ns:record_schema:{record_type}"
        return self._get_or_fetch(key, lambda: get_record_schema(record_type))

    # ------------------------------------------------------------------
    # Management
    # ------------------------------------------------------------------

    def invalidate(self, key: str | None = None) -> None:
        """Drop one entry (by key) or the entire cache.

        After invalidation the next access will re-fetch from the live API.
        """
        with self._lock:
            if key is None:
                self._store.clear()
                self._fetchers.clear()
            else:
                self._store.pop(key, None)
                self._fetchers.pop(key, None)

    def stats(self) -> dict:
        """Return cache statistics for diagnostics."""
        now = time.monotonic()
        with self._lock:
            entries = {}
            for key, entry in self._store.items():
                age = now - entry.fetched_at
                idle = now - entry.last_accessed
                entries[key] = {
                    "age_seconds": round(age, 1),
                    "stale": age >= self._ttl,
                    "idle_seconds": round(idle, 1),
                    "background_refresh": idle < self._idle_after,
                }
            return {
                "ttl": self._ttl,
                "refresh_interval": self._refresh_interval,
                "idle_after": self._idle_after,
                "entry_count": len(self._store),
                "entries": entries,
            }

    def stop(self) -> None:
        """Stop the background refresh thread (for clean shutdown in tests)."""
        self._stop_event.set()
        if self._refresh_thread is not None:
            self._refresh_thread.join(timeout=5)
            self._refresh_thread = None


# Module-level singleton
schema_cache = SchemaCache()
