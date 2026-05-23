"""OAuth 2.0 authorization server backed by Google.

Acts as a proxy: Claude Desktop authenticates against this server, which in
turn authenticates the user against Google. We mint our own access tokens
(short-lived JWTs signed with HS256) and validate them in `load_access_token`.

Static ``MCP_API_TOKEN`` / ``MCP_WRITE_TOKEN`` also validate here, so existing
server-to-server consumers (e.g. the Sabueso Slack bot) keep working when
OAuth is enabled.
"""

from __future__ import annotations

import hmac
import secrets
import time
from typing import Optional
from urllib.parse import urlencode

import jwt
from mcp.server.auth.provider import (
    AccessToken,
    AuthorizationCode,
    AuthorizationParams,
    OAuthAuthorizationServerProvider,
    TokenError,
)
from mcp.shared.auth import OAuthClientInformationFull, OAuthToken
from pydantic import AnyUrl

GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"  # noqa: S105 — public URL, not a secret
GOOGLE_JWKS_URL = "https://www.googleapis.com/oauth2/v3/certs"

ACCESS_TOKEN_TTL_SEC = 60 * 60
REFRESH_TOKEN_TTL_SEC = 60 * 60 * 24 * 30
AUTH_CODE_TTL_SEC = 60
PENDING_AUTH_TTL_SEC = 60 * 10
JWT_AUDIENCE = "agent-b"
JWT_ALGORITHM = "HS256"

READ_SCOPE = "agent-b:read"
WRITE_SCOPE = "agent-b:write"


def _now() -> int:
    return int(time.time())


class AuthorizationCodeWithEmail(AuthorizationCode):
    """Augments AuthorizationCode with the identity we proved via Google."""

    email: str


class GoogleOAuthProvider(
    OAuthAuthorizationServerProvider[AuthorizationCodeWithEmail, "object", AccessToken]
):
    """OAuth proxy to Google + static-token compatibility for legacy clients."""

    def __init__(
        self,
        *,
        google_client_id: str,
        google_client_secret: str,
        public_url: str,
        signing_key: str,
        allowed_domain: Optional[str] = None,
        extra_allowed_emails: Optional[set[str]] = None,
        write_emails: Optional[set[str]] = None,
        static_read_token: Optional[str] = None,
        static_write_token: Optional[str] = None,
    ):
        if len(signing_key) < 32:
            raise ValueError("OAUTH_SIGNING_KEY must be at least 32 characters")

        self.google_client_id = google_client_id
        self.google_client_secret = google_client_secret
        self.public_url = public_url.rstrip("/")
        self.signing_key = signing_key
        self.allowed_domain = allowed_domain
        self.extra_allowed_emails = {
            e.strip().lower() for e in (extra_allowed_emails or set()) if e.strip()
        }
        self.write_emails = {e.strip().lower() for e in (write_emails or set()) if e.strip()}
        self.static_read_token = static_read_token
        self.static_write_token = static_write_token

        self._clients: dict[str, OAuthClientInformationFull] = {}
        self._auth_codes: dict[str, AuthorizationCodeWithEmail] = {}
        # state token issued to Google -> data needed to resume the client's flow
        self._pending: dict[str, dict] = {}
        # refresh_token -> (client_id, email, scopes, expires_at)
        self._refresh: dict[str, dict] = {}

    @property
    def callback_url(self) -> str:
        return f"{self.public_url}/oauth/callback"

    # -----------------------------------------------------------------
    # Helpers used by the /oauth/callback Starlette route
    # -----------------------------------------------------------------

    def stash_pending(self, state: str, payload: dict) -> None:
        cutoff = _now() - PENDING_AUTH_TTL_SEC
        for k in list(self._pending):
            if self._pending[k].get("created_at", 0) < cutoff:
                self._pending.pop(k, None)
        payload["created_at"] = _now()
        self._pending[state] = payload

    def pop_pending(self, state: str) -> Optional[dict]:
        payload = self._pending.pop(state, None)
        if payload and payload.get("created_at", 0) < _now() - PENDING_AUTH_TTL_SEC:
            return None
        return payload

    def is_email_allowed(self, email: str, domain_claim: Optional[str]) -> bool:
        """Allow if (a) no domain restriction, (b) domain matches via hd claim
        or email suffix, or (c) the email is in the explicit extras list.
        """
        if email.lower() in self.extra_allowed_emails:
            return True
        if not self.allowed_domain:
            return True
        if domain_claim and domain_claim.lower() == self.allowed_domain.lower():
            return True
        return email.lower().endswith(f"@{self.allowed_domain.lower()}")

    def scopes_for_email(self, email: str) -> list[str]:
        if email.lower() in self.write_emails:
            return [READ_SCOPE, WRITE_SCOPE]
        return [READ_SCOPE]

    def issue_authorization_code(
        self,
        *,
        client_id: str,
        redirect_uri: AnyUrl,
        redirect_uri_provided_explicitly: bool,
        code_challenge: str,
        scopes: list[str],
        email: str,
    ) -> str:
        code_str = secrets.token_urlsafe(48)
        self._auth_codes[code_str] = AuthorizationCodeWithEmail(
            code=code_str,
            scopes=scopes,
            expires_at=_now() + AUTH_CODE_TTL_SEC,
            client_id=client_id,
            code_challenge=code_challenge,
            redirect_uri=redirect_uri,
            redirect_uri_provided_explicitly=redirect_uri_provided_explicitly,
            email=email,
        )
        return code_str

    # -----------------------------------------------------------------
    # OAuthAuthorizationServerProvider protocol
    # -----------------------------------------------------------------

    async def get_client(self, client_id: str) -> OAuthClientInformationFull | None:
        return self._clients.get(client_id)

    async def register_client(self, client_info: OAuthClientInformationFull) -> None:
        self._clients[client_info.client_id] = client_info

    async def authorize(
        self, client: OAuthClientInformationFull, params: AuthorizationParams
    ) -> str:
        state = secrets.token_urlsafe(32)
        self.stash_pending(
            state,
            {
                "client_id": client.client_id,
                "client_state": params.state,
                "code_challenge": params.code_challenge,
                "redirect_uri": str(params.redirect_uri),
                "redirect_uri_provided_explicitly": params.redirect_uri_provided_explicitly,
                "scopes": params.scopes or [],
            },
        )
        google_params = {
            "client_id": self.google_client_id,
            "redirect_uri": self.callback_url,
            "response_type": "code",
            "scope": "openid email profile",
            "state": state,
            "access_type": "online",
            "prompt": "select_account",
        }
        # Only hint `hd` to Google when nobody is on the off-domain escape
        # hatch — otherwise Google hard-filters the account picker and those
        # users get told their account "isn't allowed". The post-callback
        # domain check (`is_email_allowed`) is the real enforcement either way.
        if self.allowed_domain and not self.extra_allowed_emails:
            google_params["hd"] = self.allowed_domain
        return f"{GOOGLE_AUTH_URL}?{urlencode(google_params)}"

    async def load_authorization_code(
        self, client: OAuthClientInformationFull, authorization_code: str
    ) -> AuthorizationCodeWithEmail | None:
        code = self._auth_codes.get(authorization_code)
        if not code:
            return None
        if code.expires_at < _now():
            self._auth_codes.pop(authorization_code, None)
            return None
        if code.client_id != client.client_id:
            return None
        return code

    async def exchange_authorization_code(
        self,
        client: OAuthClientInformationFull,
        authorization_code: AuthorizationCodeWithEmail,
    ) -> OAuthToken:
        self._auth_codes.pop(authorization_code.code, None)
        return self._mint_token_pair(
            client_id=client.client_id,
            email=authorization_code.email,
            scopes=authorization_code.scopes,
        )

    async def load_refresh_token(
        self, client: OAuthClientInformationFull, refresh_token: str
    ) -> object | None:
        rt = self._refresh.get(refresh_token)
        if not rt:
            return None
        if rt["expires_at"] < _now():
            self._refresh.pop(refresh_token, None)
            return None
        if rt["client_id"] != client.client_id:
            return None
        # The SDK only checks that this is non-None and passes it back to
        # exchange_refresh_token. We return a lightweight stand-in.
        from mcp.server.auth.provider import RefreshToken

        return RefreshToken(
            token=refresh_token,
            client_id=rt["client_id"],
            scopes=rt["scopes"],
            expires_at=rt["expires_at"],
        )

    async def exchange_refresh_token(
        self,
        client: OAuthClientInformationFull,
        refresh_token,
        scopes: list[str],
    ) -> OAuthToken:
        token_str = refresh_token.token
        record = self._refresh.pop(token_str, None)
        if not record:
            raise TokenError(error="invalid_grant", error_description="Unknown refresh token")
        # Re-derive scopes from the current allowlist so revocation takes effect
        # the next time the user refreshes.
        new_scopes = self.scopes_for_email(record["email"])
        return self._mint_token_pair(
            client_id=client.client_id,
            email=record["email"],
            scopes=new_scopes,
        )

    async def load_access_token(self, token: str) -> AccessToken | None:
        if self.static_write_token and hmac.compare_digest(token, self.static_write_token):
            return AccessToken(
                token=token,
                client_id="static-write",
                scopes=[READ_SCOPE, WRITE_SCOPE],
            )
        if self.static_read_token and hmac.compare_digest(token, self.static_read_token):
            return AccessToken(
                token=token,
                client_id="static-read",
                scopes=[READ_SCOPE],
            )

        try:
            payload = jwt.decode(
                token,
                self.signing_key,
                algorithms=[JWT_ALGORITHM],
                audience=JWT_AUDIENCE,
                options={"require": ["exp", "sub", "scope"]},
            )
        except jwt.PyJWTError:
            return None

        return AccessToken(
            token=token,
            client_id=payload.get("client_id", "unknown"),
            scopes=payload["scope"].split(),
            expires_at=int(payload["exp"]),
        )

    async def revoke_token(self, token) -> None:
        token_str = getattr(token, "token", None)
        if token_str:
            self._refresh.pop(token_str, None)

    # -----------------------------------------------------------------
    # Internal
    # -----------------------------------------------------------------

    def _mint_token_pair(self, *, client_id: str, email: str, scopes: list[str]) -> OAuthToken:
        now = _now()
        access_jwt = jwt.encode(
            {
                "sub": email,
                "client_id": client_id,
                "scope": " ".join(scopes),
                "aud": JWT_AUDIENCE,
                "iat": now,
                "exp": now + ACCESS_TOKEN_TTL_SEC,
                "jti": secrets.token_urlsafe(16),
            },
            self.signing_key,
            algorithm=JWT_ALGORITHM,
        )
        refresh_str = secrets.token_urlsafe(48)
        self._refresh[refresh_str] = {
            "client_id": client_id,
            "email": email,
            "scopes": scopes,
            "expires_at": now + REFRESH_TOKEN_TTL_SEC,
        }
        return OAuthToken(
            access_token=access_jwt,
            token_type="Bearer",
            expires_in=ACCESS_TOKEN_TTL_SEC,
            refresh_token=refresh_str,
            scope=" ".join(scopes),
        )
