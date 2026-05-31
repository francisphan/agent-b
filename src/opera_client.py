"""OPERA PMS client — HTTP shim to the opera-pms-api service.

OPERA's Oracle DB is only reachable from inside the TVOMCORP network, which the
Railway deployment cannot reach directly. Instead of connecting to Oracle from
here, this client POSTs read-only SQL to **opera-pms-api** — a thin Go service
that runs on the TVOMCORP network (next to the OPERA DB) and is exposed to this
deployment over a tunnel (Tailscale / Cloudflare Tunnel). Only HTTP crosses the
tunnel; the raw Oracle port is never exposed, and the Go service connects to
Oracle with a SELECT-only account.

The public surface is unchanged from the previous python-oracledb client:
:func:`query` takes the same ``(sql, binds, limit)`` and returns ``list[dict]``
keyed by lowercase column name, so every OPERA tool and cross-system composite
keeps working without modification.

Config (env):
    OPERA_API_BASE_URL   e.g. https://tvrspms.<tailnet>.ts.net
    OPERA_API_TOKEN      bearer token the Go service requires
    OPERA_API_PROXY      optional; route ONLY the OPERA call through a proxy, e.g.
                         socks5h://localhost:1055 to reach the service over a
                         Tailscale userspace SOCKS5 tunnel (socks5h => DNS is
                         resolved at the tailscaled end, so the MagicDNS name
                         resolves over the tailnet). Other upstreams are direct.
"""

import logging
import os
import re
import time

import requests
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

# Reuse one HTTP connection across calls. Over the Railway Tailscale SOCKS proxy,
# opening a fresh connection per call is the expensive AND flaky step — each cold
# SOCKS connect is a chance to hit "General SOCKS server failure". A kept-alive
# Session pays that cost once, then rides a warm, stable path. requests.Session
# is safe for concurrent use here (urllib3 pools are thread-safe).
_session = requests.Session()


MAX_RETRIES = 3
RETRY_BACKOFF = 2  # seconds, doubled each retry
DEFAULT_ROW_LIMIT = 500
HARD_ROW_LIMIT = 5000

# Timeout (seconds) for the HTTP call to opera-pms-api. A single scalar on
# purpose: over a SOCKS proxy (the Railway Tailscale tunnel) PySocks applies one
# socket timeout to connect+read, so a (connect, read) tuple's small connect
# value would silently cap reads too (a cold tunnel handshake then trips a 5s
# "read timeout"). Must exceed the service's QUERY_TIMEOUT_SECONDS (30s) so a
# server-side 504 comes back instead of the client timing out first.
HTTP_TIMEOUT = 45

# Cap how much SQL we write to the logs — queries are normally small, but never
# log an unbounded statement.
_MAX_LOG_SQL = 2000


def _clip(value: object) -> str:
    """Render a value for logging, truncated to a safe length."""
    text = value if isinstance(value, str) else str(value)
    if len(text) > _MAX_LOG_SQL:
        return f"{text[:_MAX_LOG_SQL]}… (+{len(text) - _MAX_LOG_SQL} chars)"
    return text


def _api_config() -> tuple[str, str]:
    """Return (base_url, token), raising a clear error if unconfigured."""
    base = os.environ.get("OPERA_API_BASE_URL", "").strip()
    token = os.environ.get("OPERA_API_TOKEN", "").strip()
    if not base or not token:
        missing = ", ".join(
            name
            for name, val in (("OPERA_API_BASE_URL", base), ("OPERA_API_TOKEN", token))
            if not val
        )
        raise RuntimeError(f"OPERA API not configured (missing: {missing})")
    return base.rstrip("/"), token


# ---------------------------------------------------------------------------
# Read-only SQL guard (kept client-side as defense-in-depth; the Go service
# enforces the same guard server-side and runs as a SELECT-only DB user).
# ---------------------------------------------------------------------------

_FORBIDDEN_KEYWORDS = (
    "INSERT", "UPDATE", "DELETE", "MERGE", "DROP", "ALTER", "CREATE",
    "TRUNCATE", "GRANT", "REVOKE", "EXECUTE", "CALL", "RENAME",
    "BEGIN", "DECLARE", "COMMIT", "ROLLBACK", "SAVEPOINT",
)

_LINE_COMMENT_RE = re.compile(r"--[^\n\r]*")
_BLOCK_COMMENT_RE = re.compile(r"/\*.*?\*/", re.DOTALL)
_STRING_LITERAL_RE = re.compile(r"'(?:''|[^'])*'", re.DOTALL)


def _strip_noise(sql: str) -> str:
    sql = _BLOCK_COMMENT_RE.sub(" ", sql)
    sql = _LINE_COMMENT_RE.sub(" ", sql)
    sql = _STRING_LITERAL_RE.sub("''", sql)
    return sql


def assert_read_only(sql: str) -> None:
    """Raise ValueError if the SQL looks like anything other than a SELECT/WITH."""
    if not sql or not sql.strip():
        raise ValueError("Empty SQL.")

    cleaned = _strip_noise(sql).strip().rstrip(";").strip()
    if not cleaned:
        raise ValueError("SQL contained only comments.")

    if ";" in cleaned:
        raise ValueError(
            "Multiple statements are not allowed — submit a single SELECT."
        )

    upper = cleaned.upper()
    first_token = upper.split(None, 1)[0]
    if first_token not in ("SELECT", "WITH"):
        raise ValueError(
            f"Only SELECT/WITH queries are allowed (got: {first_token})."
        )

    tokens = set(re.findall(r"\b[A-Z_]+\b", upper))
    forbidden = tokens & set(_FORBIDDEN_KEYWORDS)
    if forbidden:
        raise ValueError(
            f"Disallowed keyword(s) in query: {', '.join(sorted(forbidden))}"
        )


# ---------------------------------------------------------------------------
# Query execution (over HTTP to opera-pms-api)
# ---------------------------------------------------------------------------

def query(
    sql: str,
    binds: dict | None = None,
    *,
    limit: int = DEFAULT_ROW_LIMIT,
) -> list[dict]:
    """Run a read-only SELECT via opera-pms-api and return rows as dicts.

    Args:
        sql: A SELECT or WITH statement. Multiple statements are rejected.
        binds: Optional named bind parameters (use :name in the SQL).
        limit: Maximum rows to return. Capped at HARD_ROW_LIMIT (5000).

    Returns:
        List of dicts keyed by lowercase column name.

    Raises:
        ValueError: if the SQL fails the read-only guard (locally or remotely).
        RuntimeError: on auth/config errors or after exhausting retries.
    """
    assert_read_only(sql)
    capped_limit = min(max(1, limit), HARD_ROW_LIMIT)
    payload = {"sql": sql, "binds": dict(binds or {}), "limit": capped_limit}

    base, token = _api_config()
    url = f"{base}/query"
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    # Route only this call through OPERA_API_PROXY (e.g. the Tailscale SOCKS5
    # tunnel) if set; leave all other agent-b upstreams direct.
    proxy = os.environ.get("OPERA_API_PROXY", "").strip()
    proxies = {"http": proxy, "https": proxy} if proxy else None

    last_err: Exception | None = None
    for attempt in range(MAX_RETRIES):
        will_retry = attempt < MAX_RETRIES - 1
        try:
            resp = _session.post(
                url, json=payload, headers=headers, timeout=HTTP_TIMEOUT, proxies=proxies
            )
        except requests.RequestException as e:
            # Network/timeout — transient, retry. Log bind *keys* only (PII).
            last_err = e
            logger.log(
                logging.WARNING if will_retry else logging.ERROR,
                "OPERA API request failed (attempt %d/%d)%s: %s | sql=%s | binds=%s",
                attempt + 1, MAX_RETRIES,
                " — will retry" if will_retry else "",
                e, _clip(sql), sorted(payload["binds"]),
            )
            if will_retry:
                time.sleep(RETRY_BACKOFF * (2 ** attempt))
            continue

        # Read-only guard / malformed request — not retryable.
        if resp.status_code == 400:
            raise ValueError(f"OPERA API rejected query: {_error_message(resp)}")
        # Auth/config problem — not retryable.
        if resp.status_code in (401, 403):
            raise RuntimeError(
                f"OPERA API auth failed ({resp.status_code}) — check OPERA_API_TOKEN"
            )
        # Server-side / rate-limit — transient, retry.
        if resp.status_code >= 500 or resp.status_code == 429:
            last_err = RuntimeError(f"OPERA API {resp.status_code}: {_clip(_error_message(resp))}")
            logger.log(
                logging.WARNING if will_retry else logging.ERROR,
                "OPERA API %d (attempt %d/%d)%s | sql=%s",
                resp.status_code, attempt + 1, MAX_RETRIES,
                " — will retry" if will_retry else "", _clip(sql),
            )
            if will_retry:
                time.sleep(RETRY_BACKOFF * (2 ** attempt))
            continue
        if not resp.ok:
            raise RuntimeError(f"OPERA API {resp.status_code}: {_clip(_error_message(resp))}")

        data = resp.json()
        return data.get("rows", [])

    raise last_err if last_err else RuntimeError("OPERA API query failed without exception")


def _error_message(resp: requests.Response) -> str:
    try:
        body = resp.json()
        if isinstance(body, dict) and "error" in body:
            return str(body["error"])
    except ValueError:
        pass
    return resp.text
