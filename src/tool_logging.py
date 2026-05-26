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
from datetime import datetime, timezone
from threading import Lock
from typing import Any

logger = logging.getLogger("agent_b.usage")

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
        redacted_args = redact(arguments) if isinstance(arguments, dict) else arguments
        try:
            result = await original_call_tool(name, arguments, *args, **kwargs)
        except Exception as exc:
            duration_ms = round((time.monotonic() - started) * 1000)
            logger.info(
                "tool=%s auth=%s status=error dur=%dms error=%s: %s | args=%s",
                name, auth, duration_ms, type(exc).__name__, _clip(str(exc)),
                redacted_args,
            )
            _write_usage_record(
                {
                    "ts": datetime.now(timezone.utc).isoformat(),
                    "event": "tool_call",
                    "tool": name,
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
            "tool=%s auth=%s status=ok dur=%dms result=%sB | args=%s",
            name, auth, duration_ms, size if size is not None else "?",
            redacted_args,
        )
        _write_usage_record(
            {
                "ts": datetime.now(timezone.utc).isoformat(),
                "event": "tool_call",
                "tool": name,
                "auth": auth,
                "status": "ok",
                "duration_ms": duration_ms,
                "result_bytes": size,
                "args": redacted_args,
            }
        )
        return result

    tool_manager.call_tool = logged_call_tool
