"""OPERA PMS Oracle client — singleton connection, retry, and read-only SQL helper.

Targets the on-prem OPERA 5 Oracle database (OPERA schema) over a network
tunnel (Tailscale, Cloudflare Tunnel, etc.) reachable from the Railway
deployment. Uses python-oracledb in Thin mode so no Instant Client is
required in the container image.

All queries are read-only — :func:`query` rejects anything that isn't a
SELECT or WITH-statement and re-checks at the DB level via a read-only
transaction. The Oracle account used by this client should additionally
have only SELECT grants on the OPERA schema.
"""

import os
import re
import time

import oracledb
from dotenv import load_dotenv

load_dotenv()


MAX_RETRIES = 3
RETRY_BACKOFF = 2  # seconds, doubled each retry
DEFAULT_ROW_LIMIT = 500
HARD_ROW_LIMIT = 5000


_conn_holder: list[oracledb.Connection | None] = [None]
_clob_registered = False


def _ensure_clob_as_str() -> None:
    """Make CLOB columns (e.g. NAME$NOTES.NOTES) come back as Python strings."""
    global _clob_registered
    if _clob_registered:
        return
    if oracledb.CLOB not in oracledb.defaults.fetch_lobs:
        oracledb.defaults.fetch_lobs = False  # return CLOB/BLOB as str/bytes
    _clob_registered = True


def _connect() -> oracledb.Connection:
    """Open a new Oracle connection from env vars."""
    host = os.environ["OPERA_DB_HOST"]
    port = int(os.environ.get("OPERA_DB_PORT", "1521"))
    service = os.environ["OPERA_DB_SERVICE_NAME"]
    user = os.environ["OPERA_DB_USER"]
    password = os.environ["OPERA_DB_PASSWORD"]

    dsn = oracledb.makedsn(host, port, service_name=service)
    conn = oracledb.connect(user=user, password=password, dsn=dsn)
    # Belt-and-suspenders read-only enforcement at the session level.
    # If the account already lacks DML grants this is a no-op; if it doesn't,
    # this still blocks accidental writes.
    try:
        conn.autocommit = False
        with conn.cursor() as cur:
            cur.execute("SET TRANSACTION READ ONLY")
    except oracledb.DatabaseError:
        pass
    return conn


def get_connection() -> oracledb.Connection:
    """Return a healthy Oracle connection (singleton, lazy-init, ping-on-reuse)."""
    _ensure_clob_as_str()
    conn = _conn_holder[0]
    if conn is not None:
        try:
            conn.ping()
            return conn
        except oracledb.DatabaseError:
            try:
                conn.close()
            except Exception:
                pass
            _conn_holder[0] = None

    _conn_holder[0] = _connect()
    return _conn_holder[0]


# ---------------------------------------------------------------------------
# Read-only SQL guard
# ---------------------------------------------------------------------------

_FORBIDDEN_KEYWORDS = (
    "INSERT", "UPDATE", "DELETE", "MERGE", "DROP", "ALTER", "CREATE",
    "TRUNCATE", "GRANT", "REVOKE", "EXECUTE", "CALL", "RENAME",
    "BEGIN", "DECLARE", "COMMIT", "ROLLBACK", "SAVEPOINT",
)

# Strip Oracle line comments (-- to end of line) and block comments (/* ... */)
# before keyword inspection so that `SELECT 1 -- DROP TABLE x` isn't flagged.
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
# Query execution
# ---------------------------------------------------------------------------

def query(
    sql: str,
    binds: dict | None = None,
    *,
    limit: int = DEFAULT_ROW_LIMIT,
) -> list[dict]:
    """Run a read-only SELECT against the OPERA Oracle DB and return rows as dicts.

    Args:
        sql: A SELECT or WITH statement. Multiple statements are rejected.
        binds: Optional named bind parameters (use :name in the SQL).
        limit: Maximum rows to return. Capped at HARD_ROW_LIMIT (5000).

    Returns:
        List of dicts keyed by lowercase column name.

    Raises:
        ValueError: if the SQL fails the read-only guard.
        oracledb.DatabaseError: on connection or syntax errors.
    """
    assert_read_only(sql)
    capped_limit = min(max(1, limit), HARD_ROW_LIMIT)
    binds = dict(binds or {})

    last_err: Exception | None = None
    for attempt in range(MAX_RETRIES):
        try:
            conn = get_connection()
            with conn.cursor() as cur:
                cur.arraysize = min(capped_limit, 500)
                cur.execute(sql, binds)
                cols = [c[0].lower() for c in cur.description]
                rows = cur.fetchmany(capped_limit)
            return [dict(zip(cols, row)) for row in rows]
        except oracledb.DatabaseError as e:
            last_err = e
            # Drop the cached connection — next attempt will reconnect.
            try:
                if _conn_holder[0] is not None:
                    _conn_holder[0].close()
            except Exception:
                pass
            _conn_holder[0] = None
            if attempt < MAX_RETRIES - 1:
                time.sleep(RETRY_BACKOFF * (2 ** attempt))

    raise last_err if last_err else RuntimeError("OPERA query failed without exception")
