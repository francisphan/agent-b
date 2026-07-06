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
import json
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

from src.auth import CALLER, caller_label
from src.oauth_store import OAuthStore, build_oauth_store

GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"  # noqa: S105 — public URL, not a secret
GOOGLE_JWKS_URL = "https://www.googleapis.com/oauth2/v3/certs"

ACCESS_TOKEN_TTL_SEC = 60 * 60
# Effectively "never expire" so an idle Claude Desktop connection doesn't get
# kicked back to a full re-auth. The token still rotates on every refresh, and
# revocation/scope changes take effect at the next refresh — this only governs
# how long an *unused* connection survives. Dial back here if that policy tightens.
REFRESH_TOKEN_TTL_SEC = 60 * 60 * 24 * 365 * 10
AUTH_CODE_TTL_SEC = 60
PENDING_AUTH_TTL_SEC = 60 * 10
JWT_AUDIENCE = "agent-b"
JWT_ALGORITHM = "HS256"

READ_SCOPE = "agent-b:read"
WRITE_SCOPE = "agent-b:write"

# Key prefixes in the shared store, namespaced so Redis can be reused for other
# things without collisions.
_CLIENT_PREFIX = "agentb:oauth:client:"
_REFRESH_PREFIX = "agentb:oauth:refresh:"  # noqa: S105 — key prefix, not a secret
_AUTHCODE_PREFIX = "agentb:oauth:authcode:"
_PENDING_PREFIX = "agentb:oauth:pending:"


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
        store: Optional[OAuthStore] = None,
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

        # Registered DCR clients, refresh tokens, authorization codes, and
        # pending-auth handles all live here so they survive restarts. Redis
        # when REDIS_URL is set, otherwise process-local (see oauth_store).
        self._store: OAuthStore = store or build_oauth_store()

    @property
    def callback_url(self) -> str:
        return f"{self.public_url}/oauth/callback"

    # -----------------------------------------------------------------
    # Helpers used by the /oauth/callback Starlette route
    # -----------------------------------------------------------------

    async def stash_pending(self, state: str, payload: dict) -> None:
        payload["created_at"] = _now()
        await self._store.set(
            _PENDING_PREFIX + state, json.dumps(payload), ttl_seconds=PENDING_AUTH_TTL_SEC
        )

    async def pop_pending(self, state: str) -> Optional[dict]:
        raw = await self._store.getdel(_PENDING_PREFIX + state)
        if not raw:
            return None
        payload = json.loads(raw)
        if payload.get("created_at", 0) < _now() - PENDING_AUTH_TTL_SEC:
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

    async def issue_authorization_code(
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
        code = AuthorizationCodeWithEmail(
            code=code_str,
            scopes=scopes,
            expires_at=_now() + AUTH_CODE_TTL_SEC,
            client_id=client_id,
            code_challenge=code_challenge,
            redirect_uri=redirect_uri,
            redirect_uri_provided_explicitly=redirect_uri_provided_explicitly,
            email=email,
        )
        await self._store.set(
            _AUTHCODE_PREFIX + code_str, code.model_dump_json(), ttl_seconds=AUTH_CODE_TTL_SEC
        )
        return code_str

    # -----------------------------------------------------------------
    # OAuthAuthorizationServerProvider protocol
    # -----------------------------------------------------------------

    async def get_client(self, client_id: str) -> OAuthClientInformationFull | None:
        raw = await self._store.get(_CLIENT_PREFIX + client_id)
        if not raw:
            return None
        return OAuthClientInformationFull.model_validate_json(raw)

    async def register_client(self, client_info: OAuthClientInformationFull) -> None:
        # DCR clients persist indefinitely so a reconnecting Claude Desktop can
        # reuse its client_id across deploys instead of re-registering.
        await self._store.set(
            _CLIENT_PREFIX + client_info.client_id, client_info.model_dump_json()
        )

    async def authorize(
        self, client: OAuthClientInformationFull, params: AuthorizationParams
    ) -> str:
        state = secrets.token_urlsafe(32)
        await self.stash_pending(
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
        raw = await self._store.get(_AUTHCODE_PREFIX + authorization_code)
        if not raw:
            return None
        code = AuthorizationCodeWithEmail.model_validate_json(raw)
        if code.expires_at < _now():
            await self._store.delete(_AUTHCODE_PREFIX + authorization_code)
            return None
        if code.client_id != client.client_id:
            return None
        return code

    async def exchange_authorization_code(
        self,
        client: OAuthClientInformationFull,
        authorization_code: AuthorizationCodeWithEmail,
    ) -> OAuthToken:
        await self._store.delete(_AUTHCODE_PREFIX + authorization_code.code)
        return await self._mint_token_pair(
            client_id=client.client_id,
            email=authorization_code.email,
            scopes=authorization_code.scopes,
        )

    async def load_refresh_token(
        self, client: OAuthClientInformationFull, refresh_token: str
    ) -> object | None:
        raw = await self._store.get(_REFRESH_PREFIX + refresh_token)
        if not raw:
            return None
        rt = json.loads(raw)
        if rt["expires_at"] < _now():
            await self._store.delete(_REFRESH_PREFIX + refresh_token)
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
        # getdel rotates the token: the old one is consumed atomically.
        raw = await self._store.getdel(_REFRESH_PREFIX + token_str)
        if not raw:
            raise TokenError(error="invalid_grant", error_description="Unknown refresh token")
        record = json.loads(raw)
        # Re-derive scopes from the current allowlist so revocation takes effect
        # the next time the user refreshes.
        new_scopes = self.scopes_for_email(record["email"])
        return await self._mint_token_pair(
            client_id=client.client_id,
            email=record["email"],
            scopes=new_scopes,
        )

    async def load_access_token(self, token: str) -> AccessToken | None:
        # As each credential resolves, stamp CALLER so usage logging can attribute
        # the call. We also carry the OAuth user's email on AccessToken.subject so
        # the tool dispatcher can recover it off the live request (reliable across
        # the streamable-http task boundary, where a contextvar may be stale).
        if self.static_write_token and hmac.compare_digest(token, self.static_write_token):
            CALLER.set(caller_label("static-write", None))
            return AccessToken(
                token=token,
                client_id="static-write",
                scopes=[READ_SCOPE, WRITE_SCOPE],
            )
        if self.static_read_token and hmac.compare_digest(token, self.static_read_token):
            CALLER.set(caller_label("static-read", None))
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

        client_id = payload.get("client_id", "unknown")
        subject = payload.get("sub")
        CALLER.set(caller_label(client_id, subject))
        return AccessToken(
            token=token,
            client_id=client_id,
            scopes=payload["scope"].split(),
            expires_at=int(payload["exp"]),
            subject=subject,
        )

    async def revoke_token(self, token) -> None:
        token_str = getattr(token, "token", None)
        if token_str:
            await self._store.delete(_REFRESH_PREFIX + token_str)

    # -----------------------------------------------------------------
    # Internal
    # -----------------------------------------------------------------

    async def _mint_token_pair(
        self, *, client_id: str, email: str, scopes: list[str]
    ) -> OAuthToken:
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
        await self._store.set(
            _REFRESH_PREFIX + refresh_str,
            json.dumps(
                {
                    "client_id": client_id,
                    "email": email,
                    "scopes": scopes,
                    "expires_at": now + REFRESH_TOKEN_TTL_SEC,
                }
            ),
            ttl_seconds=REFRESH_TOKEN_TTL_SEC,
        )
        return OAuthToken(
            access_token=access_jwt,
            token_type="Bearer",
            expires_in=ACCESS_TOKEN_TTL_SEC,
            refresh_token=refresh_str,
            scope=" ".join(scopes),
        )
