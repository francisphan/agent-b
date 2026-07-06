"""Best-effort Redis mirror of tool-usage records for the weekly report.

The JSONL log that :mod:`src.tool_logging` writes lives on Railway's *ephemeral*
disk, so it can't answer "what happened over the last seven days" across a
redeploy — every deploy starts the file empty. This module mirrors each usage
record into a capped Redis list so the ``/usage/report`` endpoint can serve a
real weekly window that survives restarts and is shared across replicas.

Two very different failure postures, by design:

* :func:`record` runs *inline with every tool call*. It must never raise and
  never block meaningfully, so it no-ops when ``REDIS_URL`` is unset and
  swallows every exception. Losing a usage sample is always preferable to
  slowing or breaking a real tool call.
* :func:`fetch` is called by the report endpoint, which *wants* to know when the
  store is unavailable (so it can return a 503 rather than silently report an
  empty week). It therefore lets Redis errors propagate.

Client construction mirrors :mod:`src.oauth_store` (``redis.from_url``) but with
tighter socket timeouts, again because :func:`record` is on the hot path.
"""

from __future__ import annotations

import json
import os
import time
from datetime import datetime

# Redis list holding one JSON-encoded usage record per element, oldest first.
USAGE_KEY = "agent_b:usage:v1"

# Cap the list so a busy stretch can't grow it without bound. At Agent B's
# volume (~tens of calls/week) this is many years of history; LTRIM after each
# push keeps only the most recent MAX_RECORDS elements.
MAX_RECORDS = 100_000

# Short timeouts: record() runs inline with a tool call, so an unreachable or
# slow Redis must fail fast rather than add latency to the user's request.
_SOCKET_TIMEOUT = 2

# Boxed so tests can reset it; lazily built on first use when REDIS_URL is set.
_client_box: list = [None]


def _get_client():
    """Return a lazily-built Redis client, or ``None`` when ``REDIS_URL`` is unset.

    Mirrors :mod:`src.oauth_store`'s ``redis.from_url`` construction but with the
    tighter connect/op timeouts appropriate to the tool-call hot path.
    """
    url = os.getenv("REDIS_URL")
    if not url:
        return None
    if _client_box[0] is None:
        import redis  # lazy: redis is only needed when REDIS_URL is configured

        _client_box[0] = redis.from_url(
            url,
            decode_responses=True,
            socket_connect_timeout=_SOCKET_TIMEOUT,
            socket_timeout=_SOCKET_TIMEOUT,
        )
    return _client_box[0]


def _epoch(value: object) -> float:
    """Best-effort epoch seconds from a record's ISO-8601 ``ts`` (or now).

    The JSONL record's ``ts`` is an ISO-8601 string, which :func:`fetch` can't
    range-filter numerically — so we convert it to epoch seconds here. A missing
    or unparseable value falls back to the current time (the record is written
    within milliseconds of the tool call, so the drift is negligible).
    """
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value).timestamp()
        except ValueError:
            pass
    return time.time()


def record(rec: dict) -> None:
    """Mirror one usage record into Redis. Best-effort; never raises or blocks.

    No-ops when ``REDIS_URL`` is unset. Otherwise ensures the stored record
    carries an epoch float ``ts`` (see :func:`_epoch`), RPUSHes it, then LTRIMs
    the list back to :data:`MAX_RECORDS`. Because this runs on the tool-call hot
    path, ANY exception — Redis down, serialisation failure, whatever — is
    swallowed: usage analytics must never be able to break a tool call.
    """
    try:
        client = _get_client()
        if client is None:
            return
        payload = dict(rec)
        if not isinstance(payload.get("ts"), (int, float)):
            payload["ts"] = _epoch(payload.get("ts"))
        client.rpush(USAGE_KEY, json.dumps(payload, default=str))
        client.ltrim(USAGE_KEY, -MAX_RECORDS, -1)
    except Exception:
        # Swallowed on purpose — see the module docstring.
        pass


def fetch(since_ts: float) -> list[dict]:
    """Return usage records with epoch ``ts`` >= ``since_ts``.

    Reads the whole capped list and decodes each element. Unlike :func:`record`,
    this lets Redis errors propagate so the report endpoint can surface a store
    outage as a 503 instead of silently reporting an empty week. Elements that
    fail to decode, or lack a numeric ``ts``, are skipped. Returns an empty list
    when ``REDIS_URL`` is unset (local dev with no store configured).
    """
    client = _get_client()
    if client is None:
        return []
    raw = client.lrange(USAGE_KEY, 0, -1)
    out: list[dict] = []
    for item in raw:
        try:
            parsed = json.loads(item)
        except (ValueError, TypeError):
            continue
        ts = parsed.get("ts")
        if isinstance(ts, (int, float)) and ts >= since_ts:
            out.append(parsed)
    return out
