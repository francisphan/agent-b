"""Persistence backend for the OAuth provider's state.

The OAuth proxy keeps four kinds of state: registered DCR clients (long-lived),
refresh tokens (30d), authorization codes (60s), and pending-auth handles
(10min). When this lived in process memory it was wiped on every container
restart, so Claude Desktop had to reconnect after each deploy (its refresh
token and client registration both vanished). It also couldn't span more than
one replica. This module persists the state instead.

Backed by Redis when ``REDIS_URL`` is set; otherwise an in-process dict store
that preserves the old single-instance behaviour for local dev and for
deployments that don't provision Redis.

The store is a thin async key-value layer. Logical expiry (the ``expires_at`` /
``created_at`` checks) stays in the provider so it works on both backends; the
``ttl_seconds`` passed here is an additional backend-level safety net that lets
Redis auto-evict stale keys (and is simply ignored by the in-memory store).
"""

from __future__ import annotations

import os
from typing import Optional, Protocol, runtime_checkable


@runtime_checkable
class OAuthStore(Protocol):
    """Async key-value interface the OAuth provider depends on."""

    async def get(self, key: str) -> Optional[str]: ...

    async def set(self, key: str, value: str, ttl_seconds: Optional[int] = None) -> None: ...

    async def delete(self, key: str) -> None: ...

    async def getdel(self, key: str) -> Optional[str]:
        """Atomically fetch and delete a key (used for single-use tokens)."""
        ...


class InMemoryOAuthStore:
    """Process-local store. No cross-restart persistence — the pre-Redis behaviour.

    Ignores ``ttl_seconds``; the provider's ``expires_at`` checks handle logical
    expiry, so stale keys are skipped on read even though they linger in the dict.
    """

    def __init__(self) -> None:
        self._data: dict[str, str] = {}

    async def get(self, key: str) -> Optional[str]:
        return self._data.get(key)

    async def set(self, key: str, value: str, ttl_seconds: Optional[int] = None) -> None:
        self._data[key] = value

    async def delete(self, key: str) -> None:
        self._data.pop(key, None)

    async def getdel(self, key: str) -> Optional[str]:
        return self._data.pop(key, None)


class RedisOAuthStore:
    """Redis-backed store. Survives container restarts and is shared across replicas."""

    def __init__(self, url: str) -> None:
        # Imported lazily so redis is only required when REDIS_URL is configured.
        import redis.asyncio as redis

        self._redis = redis.from_url(
            url,
            decode_responses=True,
            socket_connect_timeout=5,
            socket_timeout=5,
            health_check_interval=30,
            retry_on_timeout=True,
        )

    async def get(self, key: str) -> Optional[str]:
        return await self._redis.get(key)

    async def set(self, key: str, value: str, ttl_seconds: Optional[int] = None) -> None:
        await self._redis.set(key, value, ex=ttl_seconds)

    async def delete(self, key: str) -> None:
        await self._redis.delete(key)

    async def getdel(self, key: str) -> Optional[str]:
        # GETDEL requires Redis 6.2+ (Railway's Redis is well past that).
        return await self._redis.getdel(key)


def build_oauth_store() -> OAuthStore:
    """Return a Redis store when ``REDIS_URL`` is set, else an in-memory store."""
    url = os.getenv("REDIS_URL")
    if url:
        return RedisOAuthStore(url)
    return InMemoryOAuthStore()
