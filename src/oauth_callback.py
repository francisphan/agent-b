"""Starlette route that completes the Google leg of the OAuth proxy flow.

Receives Google's redirect back, swaps the code for an ID token, validates the
ID token signature + claims, applies the domain restriction and read/write
allowlists, then mints an internal authorization code and redirects the MCP
client back to its own ``redirect_uri``.
"""

from __future__ import annotations

import sys
from urllib.parse import urlencode

import httpx
import jwt
from pydantic import AnyUrl
from starlette.requests import Request
from starlette.responses import JSONResponse, RedirectResponse

from src.oauth_provider import (
    GOOGLE_JWKS_URL,
    GOOGLE_TOKEN_URL,
    GoogleOAuthProvider,
)


def _log(msg: str) -> None:
    """Plain print so Railway captures it without logging config."""
    print(f"[oauth] {msg}", file=sys.stderr, flush=True)

_JWKS_CLIENT: jwt.PyJWKClient | None = None


def _jwks_client() -> jwt.PyJWKClient:
    global _JWKS_CLIENT
    if _JWKS_CLIENT is None:
        _JWKS_CLIENT = jwt.PyJWKClient(GOOGLE_JWKS_URL, cache_keys=True)
    return _JWKS_CLIENT


def _error_redirect(redirect_uri: str, client_state: str | None, error: str, description: str) -> RedirectResponse:
    _log(f"REJECT error={error} description={description}")
    params = {"error": error, "error_description": description}
    if client_state:
        params["state"] = client_state
    separator = "&" if "?" in redirect_uri else "?"
    return RedirectResponse(f"{redirect_uri}{separator}{urlencode(params)}", status_code=302)


def make_oauth_callback_route(provider: GoogleOAuthProvider):
    """Return a Starlette handler closed over the provider instance."""

    async def oauth_callback(request: Request):
        state = request.query_params.get("state") or ""
        code = request.query_params.get("code")
        error = request.query_params.get("error")

        pending = provider.pop_pending(state)
        if not pending:
            return JSONResponse(
                {"error": "invalid_state", "error_description": "Unknown or expired state"},
                status_code=400,
            )

        client_redirect_uri = pending["redirect_uri"]
        client_state = pending.get("client_state")

        if error:
            return _error_redirect(client_redirect_uri, client_state, error, request.query_params.get("error_description", ""))
        if not code:
            return _error_redirect(client_redirect_uri, client_state, "invalid_request", "Missing code")

        # Exchange Google code for tokens
        async with httpx.AsyncClient(timeout=15.0) as client:
            try:
                resp = await client.post(
                    GOOGLE_TOKEN_URL,
                    data={
                        "code": code,
                        "client_id": provider.google_client_id,
                        "client_secret": provider.google_client_secret,
                        "redirect_uri": provider.callback_url,
                        "grant_type": "authorization_code",
                    },
                )
            except httpx.HTTPError as exc:
                return _error_redirect(
                    client_redirect_uri, client_state, "server_error", f"Google token exchange failed: {exc}"
                )

        if resp.status_code != 200:
            return _error_redirect(
                client_redirect_uri,
                client_state,
                "server_error",
                f"Google rejected token exchange: {resp.text[:200]}",
            )

        google_tokens = resp.json()
        id_token = google_tokens.get("id_token")
        if not id_token:
            return _error_redirect(
                client_redirect_uri, client_state, "server_error", "Google response missing id_token"
            )

        # Verify ID token signature against Google JWKS
        try:
            signing_key = _jwks_client().get_signing_key_from_jwt(id_token)
            id_claims = jwt.decode(
                id_token,
                signing_key.key,
                algorithms=["RS256"],
                audience=provider.google_client_id,
                issuer=["https://accounts.google.com", "accounts.google.com"],
                options={"require": ["exp", "sub", "email"]},
            )
        except jwt.PyJWTError as exc:
            return _error_redirect(
                client_redirect_uri, client_state, "server_error", f"Invalid Google ID token: {exc}"
            )

        if not id_claims.get("email_verified"):
            return _error_redirect(
                client_redirect_uri, client_state, "access_denied", "Email not verified by Google"
            )

        email = id_claims["email"]
        if not provider.is_email_allowed(email, id_claims.get("hd")):
            _log(
                f"REJECT by allowlist: email={email!r} hd={id_claims.get('hd')!r} "
                f"allowed_domain={provider.allowed_domain!r} "
                f"extras={sorted(provider.extra_allowed_emails)}"
            )
            return _error_redirect(
                client_redirect_uri,
                client_state,
                "access_denied",
                f"Email {email} is not permitted to use this server",
            )

        scopes = provider.scopes_for_email(email)
        _log(f"ACCEPT email={email} scopes={scopes}")
        internal_code = provider.issue_authorization_code(
            client_id=pending["client_id"],
            redirect_uri=AnyUrl(client_redirect_uri),
            redirect_uri_provided_explicitly=pending["redirect_uri_provided_explicitly"],
            code_challenge=pending["code_challenge"],
            scopes=scopes,
            email=email,
        )

        params = {"code": internal_code}
        if client_state:
            params["state"] = client_state
        separator = "&" if "?" in client_redirect_uri else "?"
        return RedirectResponse(f"{client_redirect_uri}{separator}{urlencode(params)}", status_code=302)

    return oauth_callback
