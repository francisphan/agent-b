"""Verbose, PII-redacted logging of MCP tool usage.

Wraps the FastMCP tool dispatcher so every tool call emits two things:

* a human-readable line to stdout (via ``logging``) for live tailing in Railway, and
* a structured JSON record appended to an analytics file (``TOOL_USAGE_LOG``)
  for offline aggregation ("top tools this week", "p95 latency", "error rate").

Tool arguments are redacted before logging — email addresses are masked and
values under name/phone/address-style keys are partially masked — so guest PII
does not land in the logs while the *structure* of a query stays readable.
Tool *results* are never logged verbatim; only their serialized size is
recorded (enough for usage analysis, no PII).

The analytics file is local to the container and therefore ephemeral on a
platform like Railway; stdout is the durable record. Point ``TOOL_USAGE_LOG``
at a mounted volume if you need the JSONL to survive redeploys.
"""

from __future__ import annotations

import json
import logging
import os
import re
import sys
import time
from contextvars import ContextVar
from datetime import datetime, timezone
from threading import Lock
from typing import Any

from starlette.middleware.base import BaseHTTPMiddleware

logger = logging.getLogger("agent_b.usage")

# Correlation ID for the current request, taken from the X-Correlation-ID
# header the Sabueso bot sets per conversation turn. Lets the per-tool logs
# here be tied back to the bot turn that triggered them.
#
# This ContextVar (set by CorrelationIdMiddleware) is only a *fallback*. The
# primary source is _correlation_from_request(), which reads the header off the
# SDK's request_ctx — reliable in a way the middleware is not (see that function
# and the middleware docstring for why).
correlation_id: ContextVar[str | None] = ContextVar("correlation_id", default=None)

# Cap to keep a hostile/garbage header out of the logs unbounded.
_MAX_CORRELATION_ID = 64

# Same cap for the X-End-User header (see _end_user_from_request).
_MAX_END_USER = 64


def _correlation_from_request() -> str | None:
    """Read X-Correlation-ID off the active MCP request.

    This is the reliable source for the correlation ID, and the reason the
    earlier middleware-only approach logged stale or ``None`` IDs:

    With streamable-http, the MCP server's message loop runs in a long-lived
    task created at session init, *not* in the per-request ASGI task. A
    ContextVar set by HTTP middleware (CorrelationIdMiddleware) lives in the
    request task and is therefore invisible to the tool dispatcher — which sees
    only whatever value was frozen into the loop task's context (a leftover ID
    from an earlier turn, or the default ``None``).

    The low-level server sets ``request_ctx`` in the *same* task that dispatches
    the tool handler, and (for HTTP transports) hangs the originating Starlette
    request off ``RequestContext.request``. Reading the header from there gives
    us the issuing turn's ID on every tool path. Returns ``None`` for transports
    without an HTTP request (e.g. stdio).
    """
    try:
        from mcp.server.lowlevel.server import request_ctx
    except Exception:  # pragma: no cover - SDK layout change
        return None
    try:
        ctx = request_ctx.get()
    except LookupError:
        return None
    request = getattr(ctx, "request", None)
    headers = getattr(request, "headers", None)
    if not headers:
        return None
    cid = headers.get("x-correlation-id")
    return cid[:_MAX_CORRELATION_ID] if cid else None


def _active_request() -> Any:
    """Return the Starlette request hung off the SDK's request_ctx, or None.

    Shared by the caller/end-user readers below. Same reliable source as
    _correlation_from_request: it lives on the task that dispatches the tool
    handler, unlike an ASGI-middleware contextvar. Returns None off the HTTP
    path (e.g. stdio) or before request_ctx is set.
    """
    try:
        from mcp.server.lowlevel.server import request_ctx
    except Exception:  # pragma: no cover - SDK layout change
        return None
    try:
        ctx = request_ctx.get()
    except LookupError:
        return None
    return getattr(ctx, "request", None)


def _caller_from_request() -> str | None:
    """Resolve the caller label off the live request's authenticated user.

    The SDK stores whatever ``load_access_token`` returned on
    ``request.scope["user"].access_token`` (an ``AccessToken``), and we hung the
    OAuth email on its ``subject``. Reading it here — rather than from the CALLER
    contextvar — is reliable across the streamable-http task boundary, the same
    reason corr is read from the request. Returns None when the request carries
    no authenticated user (no-OAuth mode, where CALLER is the fallback).
    """
    request = _active_request()
    scope = getattr(request, "scope", None)
    user = scope.get("user") if hasattr(scope, "get") else None
    token = getattr(user, "access_token", None)
    if token is None:
        return None
    from src.auth import caller_label

    return caller_label(getattr(token, "client_id", None), getattr(token, "subject", None))


def _end_user_from_request() -> str | None:
    """Read the optional X-End-User header off the live request.

    Sabueso sets this to the originating Slack user id so per-end-user usage can
    be attributed under the shared bot credential. Read the same way as corr.
    """
    request = _active_request()
    headers = getattr(request, "headers", None)
    if not headers:
        return None
    eu = headers.get("x-end-user")
    return eu[:_MAX_END_USER] if eu else None


def _caller_context() -> tuple[str, str | None]:
    """Resolve ``(client, end_user)`` for a tool call.

    ``client`` prefers the live-request read and falls back to the CALLER
    contextvar (set at auth time), then "anonymous". ``end_user`` is only
    trusted for static-token clients (Sabueso): an OAuth user shouldn't be able
    to spoof attribution rows, and their identity is already their email.
    """
    from src.auth import CALLER, STATIC_CALLERS

    client = _caller_from_request() or CALLER.get()
    end_user = _end_user_from_request()
    if not (end_user and client in STATIC_CALLERS):
        end_user = None
    return client, end_user


# Per-string-value cap so a giant query or note can't bloat the logs.
_MAX_VALUE = 500

# Arg keys whose values are treated as identifiers and partially masked.
# Kept deliberately specific so structural args (object_name, record_type,
# field names) are NOT masked — we want those for usage analysis.
_SENSITIVE_KEYS = {
    "email",
    "e_mail",
    "mail",
    "personemail",
    "phone",
    "mobile",
    "telephone",
    "fax",
    "first_name",
    "firstname",
    "last_name",
    "lastname",
    "full_name",
    "fullname",
    "given_name",
    "surname",
    "address",
    "street",
    "postal_code",
    "zip",
    "ssn",
}

_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")

_usage_file_lock = Lock()


def _mask_email(match: re.Match) -> str:
    local, _, domain = match.group(0).partition("@")
    return f"{local[:1]}***@{domain}"


def _mask_value(value: str) -> str:
    """Partially mask an identifier value, keeping a hint of its shape."""
    if "@" in value:
        return _EMAIL_RE.sub(_mask_email, value)
    if len(value) <= 2:
        return "***"
    return f"{value[0]}***{value[-1]}"


def _clip(text: str, limit: int = _MAX_VALUE) -> str:
    if len(text) > limit:
        return f"{text[:limit]}… (+{len(text) - limit} chars)"
    return text


def redact(value: Any, *, key: str | None = None) -> Any:
    """Recursively redact PII from a tool-argument structure.

    - Values under a sensitive key (email/name/phone/…) are partially masked.
    - Email addresses are masked wherever they appear, including inside query
      strings, so query structure stays readable but addresses do not leak.
    - Long strings are truncated.
    """
    if isinstance(value, dict):
        return {k: redact(v, key=k) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [redact(v, key=key) for v in value]
    if isinstance(value, str):
        if key and key.lower() in _SENSITIVE_KEYS:
            return _mask_value(value)
        return _clip(_EMAIL_RE.sub(_mask_email, value))
    return value


def _auth_level() -> str:
    """Best-effort caller authorization level (read/write/none)."""
    try:
        from src.auth import AUTH_LEVEL, _has_oauth_write_scope

        if _has_oauth_write_scope():
            return "write"
        return AUTH_LEVEL.get()
    except Exception:
        return "unknown"


def _result_bytes(result: Any) -> int | None:
    try:
        return len(json.dumps(result, default=str))
    except Exception:
        try:
            return len(str(result))
        except Exception:
            return None


# --- Result status classification ----------------------------------------
#
# Tools don't raise on handled failures; they return ``{"error": "..."}`` (or
# ``[{"error": "..."}]`` for list-returning tools), and composites
# (guest_360_profile, person_brief, ...) return a dict carrying a non-empty
# ``_errors`` list when only some of their legs failed. Without inspecting the
# payload the dispatcher logged every one of those as status=ok. The helpers
# below recover the tool's logical return value — whether the dispatcher handed
# us the raw dict/list (``convert_result=False``, e.g. the unit tests) or
# FastMCP's converted content (``convert_result=True``, the live server) — and
# derive a status plus a short, PII-masked error snippet.

_MAX_ERROR_SNIPPET = 300


def _clip_error(text: str) -> str:
    """Mask emails, then clip an error message to a short, log-safe snippet."""
    return _clip(_EMAIL_RE.sub(_mask_email, text), _MAX_ERROR_SNIPPET)


def _logical_payload(result: Any) -> Any:
    """Recover a tool's return value from whatever the dispatcher handed back.

    ``mcp._tool_manager.call_tool`` returns different shapes depending on the
    ``convert_result`` flag its caller passed:

    * ``False`` (unit tests and other direct callers) → the raw Python return
      value: a ``dict`` or ``list``.
    * ``True`` (FastMCP's live ``call_tool``) → converted output: either a list
      of content blocks — one ``TextContent`` per returned value, each carrying
      the JSON-dumped value in ``.text`` — or, if a tool declares an output
      schema, an ``(unstructured, structured)`` tuple.

    For the content-block case we decode every block back into its value so the
    list's length is preserved: ``_classify_result`` relies on single-element
    vs multi-element to tell an error sentinel (``[{"error": ...}]``) apart from
    a multi-row data result. Non-content-block lists (raw return values) pass
    through unchanged. Returns a dict/list for classification, or ``None`` when
    nothing can be recovered (the call is then treated as ok).

    We deliberately don't cap json.loads size here: the largest payloads are
    degraded guest_360 profiles and call volume is ~84/week, so the parse cost
    is negligible against the visibility win.
    """
    if isinstance(result, tuple) and len(result) == 2:
        structured = result[1]
        if isinstance(structured, dict):
            # wrap_output nests the real return value under "result".
            if set(structured) == {"result"}:
                return structured["result"]
            return structured
        return None
    if isinstance(result, dict):
        return result
    if isinstance(result, list):
        decoded = []
        for item in result:
            text = getattr(item, "text", None)
            if isinstance(text, str) and getattr(item, "type", None) == "text":
                try:
                    decoded.append(json.loads(text))
                except (ValueError, TypeError):
                    decoded.append(item)
            else:
                decoded.append(item)
        return decoded
    return None


def _classify_result(result: Any) -> tuple[str, str | None]:
    """Map a tool return value to ``(status, error_snippet)``.

    * dict with a truthy ``error`` → ``error``
    * single-element list whose only item is such a dict → ``error`` (the
      ns_tools/opera_tools ``[{"error": ...}]`` convention). A *multi*-element
      list is a multi-row data result, never a status probe — so a data row
      that happens to have a column named ``error`` can't trip a false positive.
    * dict with a non-empty ``_errors`` list → ``degraded`` (composite partial)
    * anything else → ``ok`` (snippet ``None``)
    """
    payload = _logical_payload(result)
    # A dict-returning tool arrives wrapped in a one-element content-block list
    # once convert_result has run; list tools use the same one-element shape for
    # their error sentinel. Unwrap only that case — a longer list is data.
    if isinstance(payload, list):
        if len(payload) == 1:
            payload = payload[0]
        else:
            return "ok", None
    if isinstance(payload, dict):
        err = payload.get("error")
        if err:
            return "error", _clip_error(str(err))
        legs = payload.get("_errors")
        if isinstance(legs, list) and legs:
            return "degraded", _clip_error("; ".join(str(leg) for leg in legs))
    return "ok", None


def _write_usage_record(record: dict) -> None:
    """Append one JSON line to the analytics file. Best-effort; never raises."""
    path = os.getenv("TOOL_USAGE_LOG")
    if not path:
        path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            ".cache",
            "tool_usage.jsonl",
        )
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        line = json.dumps(record, default=str)
        with _usage_file_lock:
            with open(path, "a", encoding="utf-8") as fh:
                fh.write(line + "\n")
    except Exception:  # pragma: no cover - logging must never break a tool call
        logger.debug("Could not write tool-usage record", exc_info=True)

    # Mirror the record into Redis so the weekly report can serve windows the
    # ephemeral JSONL can't (see src/usage_store.py). Independent of the JSONL
    # write above and, like it, strictly best-effort: usage_store.record already
    # swallows everything, but wrap the call too so an import hiccup can't break
    # a tool call either.
    try:
        from src import usage_store

        usage_store.record(record)
    except Exception:  # pragma: no cover - mirror must never break a tool call
        logger.debug("Could not mirror tool-usage record to store", exc_info=True)


class CorrelationIdMiddleware(BaseHTTPMiddleware):
    """Read X-Correlation-ID off each request into a contextvar (fallback only).

    The dispatcher prefers ``_correlation_from_request()``; this middleware is a
    backstop. Note it sets the contextvar *unconditionally* (to ``None`` when the
    header is absent) and resets it after the request, so a header-less request
    can never leave a stale ID behind for the next one — the bug that made the
    logs show a previous turn's correlation ID.

    Because Starlette's BaseHTTPMiddleware runs the downstream app in a separate
    task, the value set here may not reach the tool dispatcher at all; that's
    exactly why the request_ctx-based reader is the primary source.
    """

    async def dispatch(self, request, call_next):
        cid = request.headers.get("x-correlation-id")
        token = correlation_id.set(cid[:_MAX_CORRELATION_ID] if cid else None)
        try:
            return await call_next(request)
        finally:
            correlation_id.reset(token)


# Explicit default levels for specific loggers, applied after the root level.
# Most are noisy framework loggers that emit a line per request / HTTP
# round-trip and would bury the agent_b.usage log at their INFO default, so we
# quiet them to WARNING. uvicorn.error is the exception: it carries the
# "Uvicorn running on ..." startup banner that ops/scrapers watch for, so we
# hold it at INFO even when LOG_LEVEL raises the root threshold. Every entry is
# overridable via LOG_LEVEL_OVERRIDES — a comma-separated list of
# ``logger=LEVEL`` pairs, e.g. LOG_LEVEL_OVERRIDES="uvicorn.access=INFO,httpx=DEBUG"
_LOGGER_LEVEL_DEFAULTS = {
    "uvicorn.access": "WARNING",
    "uvicorn.error": "INFO",
    "httpx": "WARNING",
    "httpcore": "WARNING",
    "mcp.server.lowlevel": "WARNING",
}


def _resolve_level(value: str) -> int | None:
    """Resolve a level name to its int, or ``None`` if it isn't a real level.

    ``getattr(logging, value)`` is unsafe: a value like ``BASIC_FORMAT`` is a
    genuine ``logging`` attribute but not a level, so it slips past a getattr
    default and makes ``setLevel`` raise — refusing server start. ``logging``'s
    own name→level table is the authoritative check: ``getLevelName`` returns an
    int for a known name and a ``"Level <x>"`` string otherwise.
    """
    resolved = logging.getLevelName(str(value).strip().upper())
    return resolved if isinstance(resolved, int) else None


def _parse_log_overrides(raw: str) -> dict[str, str]:
    """Parse ``LOG_LEVEL_OVERRIDES`` ("name=LEVEL,name=LEVEL") into a mapping.

    Malformed entries (no ``=``, empty name/level) are skipped rather than
    raising — a bad env var must never stop the server from starting. Level
    validity is checked later, in :func:`_resolve_level`.
    """
    overrides: dict[str, str] = {}
    for item in raw.split(","):
        name, sep, level = item.partition("=")
        name, level = name.strip(), level.strip().upper()
        if sep and name and level:
            overrides[name] = level
    return overrides


def configure_logging() -> None:
    """Ensure INFO-level logs reach stdout with a consistent format.

    Idempotent: if a handler is already installed (e.g. by uvicorn/mcp) we only
    raise the level, so we don't double-emit. ``LOG_LEVEL`` overrides the
    default root level of INFO (set it to DEBUG for maximum verbosity).

    Also applies the per-logger defaults in ``_LOGGER_LEVEL_DEFAULTS`` (quieting
    noisy framework loggers, keeping uvicorn's startup banner), each overridable
    via ``LOG_LEVEL_OVERRIDES``. ``agent_b.usage`` is pinned to INFO last so the
    usage log stays audible even when ``LOG_LEVEL`` raises the root threshold.
    Unrecognised level names (in ``LOG_LEVEL`` or an override) are ignored rather
    than crashing the process — see :func:`_resolve_level`.

    Note: ``src/__main__.py`` starts uvicorn with ``log_config=None`` so
    uvicorn's own ``dictConfig`` can't reset ``uvicorn.access`` back to INFO
    after this runs.
    """
    root = logging.getLogger()
    if not root.handlers:
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(
            logging.Formatter(
                "%(asctime)s %(levelname)s [%(name)s] %(message)s",
                "%Y-%m-%d %H:%M:%S",
            )
        )
        root.addHandler(handler)
    root_level = _resolve_level(os.getenv("LOG_LEVEL", "INFO"))
    root.setLevel(root_level if root_level is not None else logging.INFO)

    overrides = _parse_log_overrides(os.getenv("LOG_LEVEL_OVERRIDES", ""))
    for name, default_level in _LOGGER_LEVEL_DEFAULTS.items():
        chosen = _resolve_level(overrides.get(name, default_level))
        if chosen is None:  # bad override → fall back to our own valid default
            chosen = _resolve_level(default_level)
        logging.getLogger(name).setLevel(chosen)
    # Honour overrides for loggers outside the default set too; skip bad levels.
    for name, value in overrides.items():
        if name in _LOGGER_LEVEL_DEFAULTS:
            continue
        chosen = _resolve_level(value)
        if chosen is not None:
            logging.getLogger(name).setLevel(chosen)
    # The usage log is the point of this module; keep it audible regardless.
    logging.getLogger("agent_b.usage").setLevel(logging.INFO)


def instrument(mcp) -> None:
    """Wrap the FastMCP tool dispatcher to log every tool call.

    Logs once the call resolves (success or error) with the tool name, caller
    auth level, redacted arguments, duration, and result size. Re-raises any
    exception unchanged so tool behaviour is untouched.
    """
    tool_manager = mcp._tool_manager
    original_call_tool = tool_manager.call_tool

    async def logged_call_tool(name, arguments, *args, **kwargs):
        started = time.monotonic()
        auth = _auth_level()
        # Prefer the per-request read (reliable across the streamable-http task
        # boundary); fall back to the middleware contextvar for non-HTTP paths.
        corr = _correlation_from_request()
        if corr is None:
            corr = correlation_id.get()
        # Who is calling (Sabueso / s2s / an OAuth email) and, for static clients
        # only, the end user they're acting for (e.g. a Slack user id).
        client, end_user = _caller_context()
        redacted_args = redact(arguments) if isinstance(arguments, dict) else arguments
        try:
            result = await original_call_tool(name, arguments, *args, **kwargs)
        except Exception as exc:
            duration_ms = round((time.monotonic() - started) * 1000)
            # Mask emails: an exception message like "no record for jane@x.com"
            # would otherwise leak the exact PII _clip_error exists to prevent.
            exc_snippet = _clip_error(str(exc))
            logger.info(
                "tool=%s corr=%s auth=%s client=%s end_user=%s status=error dur=%dms error=%s: %s | args=%s",
                name,
                corr,
                auth,
                client,
                end_user,
                duration_ms,
                type(exc).__name__,
                exc_snippet,
                redacted_args,
            )
            _write_usage_record(
                {
                    "ts": datetime.now(timezone.utc).isoformat(),
                    "event": "tool_call",
                    "tool": name,
                    "corr": corr,
                    "auth": auth,
                    "client": client,
                    "end_user": end_user,
                    "status": "error",
                    "duration_ms": duration_ms,
                    "error_type": type(exc).__name__,
                    "error": exc_snippet,
                    "args": redacted_args,
                }
            )
            raise
        duration_ms = round((time.monotonic() - started) * 1000)
        size = _result_bytes(result)
        # Tools signal handled failures in-band (returned {"error": ...} or a
        # non-empty _errors list) rather than raising, so inspect the payload to
        # avoid logging those as status=ok.
        status, error_snippet = _classify_result(result)
        size_str = size if size is not None else "?"
        if status == "ok":
            logger.info(
                "tool=%s corr=%s auth=%s client=%s end_user=%s status=ok dur=%dms result=%sB | args=%s",
                name,
                corr,
                auth,
                client,
                end_user,
                duration_ms,
                size_str,
                redacted_args,
            )
        else:
            logger.info(
                "tool=%s corr=%s auth=%s client=%s end_user=%s status=%s dur=%dms result=%sB error=%s | args=%s",
                name,
                corr,
                auth,
                client,
                end_user,
                status,
                duration_ms,
                size_str,
                error_snippet,
                redacted_args,
            )
        record = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "event": "tool_call",
            "tool": name,
            "corr": corr,
            "auth": auth,
            "client": client,
            "end_user": end_user,
            "status": status,
            "duration_ms": duration_ms,
            "result_bytes": size,
            "args": redacted_args,
        }
        if error_snippet is not None:
            record["error"] = error_snippet
        _write_usage_record(record)
        return result

    tool_manager.call_tool = logged_call_tool
