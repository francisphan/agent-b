"""Allow running as `python -m src`."""

import os

import uvicorn

from src.auth import READ_TOKEN, WRITE_TOKEN
from src.server import BearerAuthMiddleware, _oauth_provider, mcp

transport = os.getenv("MCP_TRANSPORT", "streamable-http")
app = mcp.streamable_http_app() if transport == "streamable-http" else mcp.sse_app()

# When OAuth is configured the SDK already validates bearer tokens (and our
# provider's load_access_token recognises the static tokens too), so the
# legacy middleware would only duplicate work and double-401 unauthenticated
# requests.
if _oauth_provider is None and (READ_TOKEN or WRITE_TOKEN):
    app.add_middleware(BearerAuthMiddleware)

uvicorn.run(
    app,
    host=os.getenv("MCP_HOST", "0.0.0.0"),
    port=int(os.getenv("MCP_PORT", "8000")),
)
