"""Authorization context for distinguishing read-only vs read-write access.

Two code paths can grant write access:

1. **OAuth mode** — the SDK's bearer middleware validates the request and
   stores the resulting ``AccessToken`` on a contextvar. We look at its
   ``scopes`` for ``agent-b:write``.
2. **Legacy / no-OAuth mode** — ``BearerAuthMiddleware`` sets ``AUTH_LEVEL``
   directly per request based on which static token matched.

Write tools call ``require_write_access()`` before mutating. The function
checks both paths so it Just Works regardless of which middleware is mounted.
"""

import os
from contextvars import ContextVar
from typing import Optional

AUTH_LEVEL: ContextVar[str] = ContextVar("auth_level", default="none")

# Who is calling, for usage attribution (distinct from AUTH_LEVEL's read/write).
# Set at authentication time: "sabueso" for the static write token, "s2s-read"
# for the static read token, or the OAuth user's email. Defaults to "anonymous"
# when no auth is configured (local dev). This contextvar is the fallback source;
# tool_logging prefers reading the caller off the live request (reliable across
# the streamable-http task boundary, the same reason corr is read that way).
CALLER: ContextVar[str] = ContextVar("caller", default="anonymous")

# Caller labels that denote a static-token (server-to-server) client. Used to
# decide whether to trust a client-supplied X-End-User header: only static
# clients (i.e. the Sabueso bot) may attribute calls to an end user; an OAuth
# user's identity IS their email, so they can't spoof attribution rows.
STATIC_CALLERS = frozenset({"sabueso", "s2s-read"})


def caller_label(client_id: Optional[str], subject: Optional[str]) -> str:
    """Map an AccessToken's (client_id, subject) to a stable usage-attribution label.

    - the static write token → "sabueso" (the Slack bot uses it)
    - the static read token  → "s2s-read"
    - an OAuth user          → their email (the JWT ``sub`` claim)
    - anything else          → the client_id, or "anonymous" if absent
    """
    if client_id == "static-write":
        return "sabueso"
    if client_id == "static-read":
        return "s2s-read"
    if subject:
        return subject
    return client_id or "anonymous"


READ_TOKEN = os.getenv("MCP_API_TOKEN")
WRITE_TOKEN = os.getenv("MCP_WRITE_TOKEN")

# Explicit, loud opt-in for running with NO authentication (local dev only).
# Without this, a deployment that is missing both OAuth config and static tokens
# refuses to start rather than silently exposing data on a 0.0.0.0 bind.
ALLOW_INSECURE_NO_AUTH = os.getenv("MCP_ALLOW_INSECURE_NO_AUTH", "").strip().lower() in (
    "1",
    "true",
    "yes",
)


def _has_oauth_write_scope() -> bool:
    """Check the SDK-managed auth context for the write scope.

    Returns False when no OAuth-authenticated user is on the contextvar, which
    is also the case when OAuth is disabled entirely.
    """
    try:
        from mcp.server.auth.middleware.auth_context import get_access_token
    except ImportError:
        return False

    token = get_access_token()
    if token is None:
        return False
    return "agent-b:write" in (token.scopes or [])


def require_write_access() -> None:
    """Raise if the current request does not have write-level authorization.

    Call this at the top of every write tool to gate mutations behind either
    ``MCP_WRITE_TOKEN`` (legacy) or the ``agent-b:write`` OAuth scope.
    """
    if _has_oauth_write_scope():
        return
    if AUTH_LEVEL.get() == "write":
        return
    raise PermissionError(
        "Write access denied. This operation requires either the "
        "MCP_WRITE_TOKEN bearer token or an OAuth login from a "
        "user on the write allowlist."
    )
