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

# Per-string-value cap so a giant query or note can't bloat the logs.
_MAX_VALUE = 500

# Arg keys whose values are treated as identifiers and partially masked.
# Kept deliberately specific so structural args (object_name, record_type,
# field names) are NOT masked — we want those for usage analysis.
_SENSITIVE_KEYS = {
    "email", "e_mail", "mail", "personemail",
    "phone", "mobile", "telephone", "fax",
    "first_name", "firstname", "last_name", "lastname",
    "full_name", "fullname", "given_name", "surname",
    "address", "street", "postal_code", "zip", "ssn",
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


def _clip(text: str) -> str:
    if len(text) > _MAX_VALUE:
        return f"{text[:_MAX_VALUE]}… (+{len(text) - _MAX_VALUE} chars)"
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


def configure_logging() -> None:
    """Ensure INFO-level logs reach stdout with a consistent format.

    Idempotent: if a handler is already installed (e.g. by uvicorn/mcp) we only
    raise the level, so we don't double-emit. ``LOG_LEVEL`` overrides the
    default of INFO (set it to DEBUG for maximum verbosity).
    """
    level = os.getenv("LOG_LEVEL", "INFO").upper()
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
    root.setLevel(getattr(logging, level, logging.INFO))


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
        redacted_args = redact(arguments) if isinstance(arguments, dict) else arguments
        try:
            result = await original_call_tool(name, arguments, *args, **kwargs)
        except Exception as exc:
            duration_ms = round((time.monotonic() - started) * 1000)
            logger.info(
                "tool=%s corr=%s auth=%s status=error dur=%dms error=%s: %s | args=%s",
                name, corr, auth, duration_ms, type(exc).__name__, _clip(str(exc)),
                redacted_args,
            )
            _write_usage_record(
                {
                    "ts": datetime.now(timezone.utc).isoformat(),
                    "event": "tool_call",
                    "tool": name,
                    "corr": corr,
                    "auth": auth,
                    "status": "error",
                    "duration_ms": duration_ms,
                    "error_type": type(exc).__name__,
                    "error": _clip(str(exc)),
                    "args": redacted_args,
                }
            )
            raise
        duration_ms = round((time.monotonic() - started) * 1000)
        size = _result_bytes(result)
        logger.info(
            "tool=%s corr=%s auth=%s status=ok dur=%dms result=%sB | args=%s",
            name, corr, auth, duration_ms, size if size is not None else "?",
            redacted_args,
        )
        _write_usage_record(
            {
                "ts": datetime.now(timezone.utc).isoformat(),
                "event": "tool_call",
                "tool": name,
                "corr": corr,
                "auth": auth,
                "status": "ok",
                "duration_ms": duration_ms,
                "result_bytes": size,
                "args": redacted_args,
            }
        )
        return result

    tool_manager.call_tool = logged_call_tool
