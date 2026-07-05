"""Allow running as `python -m src`."""

import os

import uvicorn

from src.auth import ALLOW_INSECURE_NO_AUTH, READ_TOKEN, WRITE_TOKEN
from src.server import BearerAuthMiddleware, _oauth_provider, mcp
from src.tool_logging import CorrelationIdMiddleware

transport = os.getenv("MCP_TRANSPORT", "streamable-http")
app = mcp.streamable_http_app() if transport == "streamable-http" else mcp.sse_app()

# Capture the bot's per-turn X-Correlation-ID on every request (both auth
# modes) so tool-dispatch logs can be tied back to the originating bot turn.
app.add_middleware(CorrelationIdMiddleware)

# Decide the auth posture explicitly so a misconfigured deploy fails CLOSED
# instead of silently serving data on its 0.0.0.0 bind.
if _oauth_provider is not None:
    # OAuth mode: the SDK already validates bearer tokens (and our provider's
    # load_access_token recognises the static tokens too), so adding the legacy
    # middleware would only duplicate work and double-401 unauthenticated requests.
    pass
elif READ_TOKEN or WRITE_TOKEN:
    app.add_middleware(BearerAuthMiddleware)
elif ALLOW_INSECURE_NO_AUTH:
    print(
        "WARNING: starting with NO authentication "
        "(MCP_ALLOW_INSECURE_NO_AUTH set) — every request gets write access. "
        "Use this for local development only.",
        flush=True,
    )
    app.add_middleware(BearerAuthMiddleware)
else:
    raise RuntimeError(
        "Refusing to start: no authentication configured. Set MCP_API_TOKEN "
        "and/or MCP_WRITE_TOKEN, configure OAuth (OAUTH_GOOGLE_CLIENT_ID, ...), "
        "or set MCP_ALLOW_INSECURE_NO_AUTH=true to explicitly run open for local dev."
    )

uvicorn.run(
    app,
    host=os.getenv("MCP_HOST", "0.0.0.0"),
    port=int(os.getenv("MCP_PORT", "8000")),
    # Don't let uvicorn's own dictConfig re-raise uvicorn.access to INFO and
    # overwrite the levels configure_logging() set. Our root stdout handler
    # already formats and emits every logger (see src/tool_logging.py).
    log_config=None,
)
