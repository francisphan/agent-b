"""Salesforce client with OAuth refresh token auth, singleton connection, and retry logic."""

import json
import logging
import os
import time
from pathlib import Path

from dotenv import load_dotenv
from requests.exceptions import ConnectionError as RequestsConnectionError, Timeout
from simple_salesforce import Salesforce
from simple_salesforce.exceptions import (
    SalesforceError,
    SalesforceExpiredSession,
)

from src.sanitize import escape_sosl, validate_object_name, validate_path_segment, validate_sf_id

load_dotenv()

logger = logging.getLogger(__name__)

TOKEN_CACHE = Path(__file__).parent.parent / ".token_cache.json"

MAX_RETRIES = 3
RETRY_BACKOFF = 2  # seconds, doubled each retry

# Cap how much of an error body we write to the logs — never log an unbounded
# response (it may be large or carry PII).
_MAX_LOG_BODY = 2000

_sf_holder: list[Salesforce | None] = [None]


def _clip(value: object) -> str:
    """Render a value for logging, truncated to a safe length."""
    text = value if isinstance(value, str) else str(value)
    if len(text) > _MAX_LOG_BODY:
        return f"{text[:_MAX_LOG_BODY]}… (+{len(text) - _MAX_LOG_BODY} chars)"
    return text


def _log_sf_error(exc: SalesforceError, will_retry: bool) -> None:
    """Log a Salesforce API error with the request that caused it.

    ``SalesforceError`` carries the HTTP status, the request URL (which for a
    SOQL/SOSL query embeds the query itself), and the parsed error body — so a
    4xx/5xx is diagnosable from the logs alone. WARNING when the call will be
    retried, ERROR when it propagates to the caller.
    """
    logger.log(
        logging.WARNING if will_retry else logging.ERROR,
        "Salesforce request failed: status=%s url=%s%s | response=%s",
        getattr(exc, "status", None),
        getattr(exc, "url", None),
        " (will retry)" if will_retry else "",
        _clip(getattr(exc, "content", None)),
    )


def _save_token(instance_url: str, access_token: str, refresh_token: str | None = None):
    cache = {"instance_url": instance_url, "access_token": access_token}
    if refresh_token:
        cache["refresh_token"] = refresh_token
    elif TOKEN_CACHE.exists():
        try:
            old = json.loads(TOKEN_CACHE.read_text())
            if old.get("refresh_token"):
                cache["refresh_token"] = old["refresh_token"]
        except Exception:
            pass
    fd = os.open(str(TOKEN_CACHE), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(cache, f)


def _load_cached_token() -> Salesforce | None:
    """Try to connect using a cached token. Returns None if expired/missing."""
    if not TOKEN_CACHE.exists():
        return None
    try:
        data = json.loads(TOKEN_CACHE.read_text())
        sf = Salesforce(
            instance_url=data["instance_url"], session_id=data["access_token"]
        )
        sf.describe()  # test if token is still valid
        return sf
    except Exception:
        return None


def _refresh_oauth_token() -> Salesforce | None:
    """Silently refresh the access token using the cached refresh token."""
    if not TOKEN_CACHE.exists():
        return None
    try:
        cache = json.loads(TOKEN_CACHE.read_text())
        refresh_token = cache.get("refresh_token")
        if not refresh_token:
            return None
    except Exception:
        return None

    client_id = os.environ["SF_CLIENT_ID"]
    client_secret = os.environ["SF_CLIENT_SECRET"]
    domain = os.environ.get("SF_DOMAIN") or "login"
    base_url = f"https://{domain}.salesforce.com"

    import requests

    resp = requests.post(
        f"{base_url}/services/oauth2/token",
        data={
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "client_id": client_id,
            "client_secret": client_secret,
        },
    )
    if not resp.ok:
        return None

    data = resp.json()
    _save_token(data["instance_url"], data["access_token"], data.get("refresh_token"))
    return Salesforce(
        instance_url=data["instance_url"], session_id=data["access_token"]
    )


def _refresh_from_env() -> Salesforce | None:
    """Refresh using SF_REFRESH_TOKEN env var (for deployed environments without token cache)."""
    refresh_token = os.environ.get("SF_REFRESH_TOKEN")
    if not refresh_token:
        return None

    client_id = os.environ["SF_CLIENT_ID"]
    client_secret = os.environ["SF_CLIENT_SECRET"]
    domain = os.environ.get("SF_DOMAIN") or "login"
    base_url = f"https://{domain}.salesforce.com"

    import requests

    resp = requests.post(
        f"{base_url}/services/oauth2/token",
        data={
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "client_id": client_id,
            "client_secret": client_secret,
        },
    )
    if not resp.ok:
        return None

    data = resp.json()
    _save_token(data["instance_url"], data["access_token"], refresh_token)
    return Salesforce(
        instance_url=data["instance_url"], session_id=data["access_token"]
    )


def _reconnect() -> Salesforce:
    """Re-authenticate using OAuth refresh token."""
    refreshed = _refresh_oauth_token()
    if refreshed:
        return refreshed
    refreshed = _refresh_from_env()
    if refreshed:
        return refreshed
    raise RuntimeError(
        "Salesforce OAuth refresh failed. Re-run the initial OAuth flow to get a new refresh token."
    )


def get_client() -> Salesforce:
    """Return a connected Salesforce instance (singleton — reconnects if needed)."""
    if _sf_holder[0] is None:
        cached = _load_cached_token()
        if cached:
            _sf_holder[0] = cached
        else:
            _sf_holder[0] = _reconnect()
    return _sf_holder[0]


def instance_url() -> str:
    """Base URL of the connected Salesforce instance (e.g. https://acme.my.salesforce.com)."""
    sf = get_client()
    host = getattr(sf, "sf_instance", None)
    if host:
        return f"https://{host}"
    base = getattr(sf, "base_url", "") or ""
    return base.split("/services/")[0]


def record_url(object_name: str, record_id: str | None) -> str:
    """Lightning URL for a record so a human can open it. '' if unbuildable; never raises."""
    if not record_id:
        return ""
    try:
        base = instance_url()
    except Exception:
        return ""
    return f"{base}/lightning/r/{object_name}/{record_id}/view" if base else ""


def _with_retry(func, *, idempotent: bool = True):
    """Execute func(sf) with retry on transient errors and re-auth on expired session.

    Args:
        func: Callable taking a Salesforce instance.
        idempotent: If False (e.g. creates), only retry on auth expiry and
                    connection-level errors to avoid duplicate records.
    """
    if _sf_holder[0] is None:
        get_client()
    last_exc = None
    for attempt in range(MAX_RETRIES):
        try:
            return func(_sf_holder[0])
        except SalesforceExpiredSession as e:
            last_exc = e
            _sf_holder[0] = _reconnect()
            continue
        except RequestsConnectionError as e:
            # Connection never reached the server — safe to retry even for writes
            last_exc = e
            if attempt < MAX_RETRIES - 1:
                time.sleep(RETRY_BACKOFF * (2**attempt))
            continue
        except (Timeout, OSError) as e:
            # Timeout/OS errors: request may have reached the server
            if not idempotent:
                raise
            last_exc = e
            if attempt < MAX_RETRIES - 1:
                time.sleep(RETRY_BACKOFF * (2**attempt))
            continue
        except SalesforceError as e:
            # Salesforce API errors (malformed query, permission denied, etc.)
            # Only retry for idempotent ops — these may be transient server errors
            will_retry = idempotent and attempt < MAX_RETRIES - 1
            _log_sf_error(e, will_retry)
            if not idempotent:
                raise
            last_exc = e
            if attempt < MAX_RETRIES - 1:
                time.sleep(RETRY_BACKOFF * (2**attempt))
            continue
    if last_exc is None:
        raise RuntimeError("All retry attempts exhausted; OAuth refresh may be broken.")
    raise last_exc


def query(soql: str) -> list[dict]:
    """Run a SOQL query with automatic retry (fetches all pages)."""

    def _do(sf):
        result = sf.query_all(soql)
        records = result.get("records", [])
        for r in records:
            r.pop("attributes", None)
        return records

    return _with_retry(_do)


def query_page(soql: str = "", next_records_url: str = "") -> dict:
    """Run a SOQL query returning a single page, or fetch the next page via URL.

    Returns a dict with 'records', 'totalSize', 'done', and optionally 'nextRecordsUrl'.
    """

    def _do(sf):
        if next_records_url:
            result = sf.query_more(next_records_url, identifier_is_url=True)
        else:
            result = sf.query(soql)
        records = result.get("records", [])
        for r in records:
            r.pop("attributes", None)
        page = {
            "records": records,
            "totalSize": result.get("totalSize", len(records)),
            "done": result.get("done", True),
        }
        if not result.get("done") and result.get("nextRecordsUrl"):
            page["nextRecordsUrl"] = result["nextRecordsUrl"]
        return page

    return _with_retry(_do)


def describe_object(object_name: str) -> dict:
    """Describe a Salesforce object with retry."""
    validate_object_name(object_name)

    def _do(sf):
        return getattr(sf, object_name).describe()

    return _with_retry(_do)


def list_objects() -> list[str]:
    """List all queryable SObject names, sorted."""

    def _do(sf):
        desc = sf.describe()
        return sorted(obj["name"] for obj in desc["sobjects"] if obj["queryable"])

    return _with_retry(_do)


def get_record(object_name: str, record_id: str) -> dict:
    """Get a single record by Id with retry."""
    validate_object_name(object_name)
    validate_sf_id(record_id)

    def _do(sf):
        record = getattr(sf, object_name).get(record_id)
        record.pop("attributes", None)
        return record

    return _with_retry(_do)


def create_record(object_name: str, data: dict) -> dict:
    """Create a new Salesforce record (non-idempotent — only retries on connection errors)."""
    validate_object_name(object_name)

    def _do(sf):
        return getattr(sf, object_name).create(data)

    return _with_retry(_do, idempotent=False)


def update_record(object_name: str, record_id: str, data: dict) -> dict:
    """Update an existing Salesforce record by Id with retry."""
    validate_object_name(object_name)
    validate_sf_id(record_id)

    def _do(sf):
        status = getattr(sf, object_name).update(record_id, data)
        return {"success": True, "id": record_id, "status_code": status}

    return _with_retry(_do)


def delete_record(object_name: str, record_id: str) -> dict:
    """Delete a Salesforce record by Id (non-idempotent — retrying may re-delete)."""
    validate_object_name(object_name)
    validate_sf_id(record_id)

    def _do(sf):
        status = getattr(sf, object_name).delete(record_id)
        return {"success": True, "id": record_id, "status_code": status}

    return _with_retry(_do, idempotent=False)


def upsert_record(
    object_name: str, external_id_field: str, external_id: str, data: dict
) -> dict:
    """Upsert a Salesforce record using an external ID field with retry."""
    validate_object_name(object_name)
    validate_object_name(external_id_field)
    validate_path_segment(external_id)

    def _do(sf):
        return getattr(sf, object_name).upsert(
            f"{external_id_field}/{external_id}", data
        )

    return _with_retry(_do)


# ---------------------------------------------------------------------------
# New capabilities
# ---------------------------------------------------------------------------


def search(sosl_query: str) -> list[dict]:
    """Run a SOSL search query with retry."""

    def _do(sf):
        result = sf.search(sosl_query)
        return result.get("searchRecords", [])

    return _with_retry(_do)


def quick_search(search_term: str) -> list[dict]:
    """Run a quick SOSL search across all objects with retry.

    Falls back to a manual SOSL FIND query if quick_search is not available.
    """

    def _do(sf):
        if hasattr(sf, "quick_search"):
            result = sf.quick_search(search_term)
        else:
            # Fallback: construct a basic SOSL query with proper escaping
            escaped = escape_sosl(search_term)
            result = sf.search(f"FIND {{{escaped}}} IN ALL FIELDS")
        return result.get("searchRecords", [])

    return _with_retry(_do)


def get_limits() -> dict:
    """Return current Salesforce API usage limits."""

    def _do(sf):
        return sf.limits()

    return _with_retry(_do)


_BULK_ALLOWED_OPS = frozenset(("insert", "update", "upsert", "delete"))


def bulk_operation(
    object_name: str,
    operation: str,
    records: list[dict],
    external_id_field: str | None = None,
    batch_size: int = 10000,
) -> list[dict]:
    """Run a bulk API operation (insert, update, upsert, delete)."""
    validate_object_name(object_name)
    if operation not in _BULK_ALLOWED_OPS:
        raise ValueError(f"Invalid bulk operation: {operation!r}")

    def _do(sf):
        bulk_obj = getattr(sf.bulk, object_name)
        op_func = getattr(bulk_obj, operation)
        kwargs: dict = {"batch_size": batch_size}
        if operation == "upsert" and external_id_field:
            kwargs["external_id_field"] = external_id_field
        results = op_func(records, **kwargs)
        # simple-salesforce bulk API may return nested lists (one per batch) — flatten
        if results and isinstance(results[0], list):
            return [item for batch in results for item in batch]
        return results

    # insert and delete are non-idempotent (duplicates / re-deletes)
    is_idempotent = operation in ("update", "upsert")
    return _with_retry(_do, idempotent=is_idempotent)


def get_recent_items(limit: int | None = None) -> dict | list:
    """Return recently viewed Salesforce records."""

    def _do(sf):
        path = "recent/"
        if limit:
            path += f"?limit={int(limit)}"
        return sf.restful(path)

    return _with_retry(_do)
