"""Tests for src/oauth_provider.py — JWT mint/verify, static-token coexistence,
domain restriction, and scope assignment."""

from __future__ import annotations

import time

import jwt
import pytest
from mcp.shared.auth import OAuthClientInformationFull
from pydantic import AnyUrl

from src.oauth_provider import (
    JWT_ALGORITHM,
    JWT_AUDIENCE,
    READ_SCOPE,
    WRITE_SCOPE,
    GoogleOAuthProvider,
)


SIGNING_KEY = "k" * 48


def make_provider(**overrides) -> GoogleOAuthProvider:
    defaults = dict(
        google_client_id="goog-client.apps.googleusercontent.com",
        google_client_secret="goog-secret",
        public_url="https://agent-b.example.com",
        signing_key=SIGNING_KEY,
        allowed_domain="vinesofmendoza.com",
        write_emails={"alice@vinesofmendoza.com"},
        static_read_token="read-only-secret",
        static_write_token="write-secret",
    )
    defaults.update(overrides)
    return GoogleOAuthProvider(**defaults)


class TestSigningKeyValidation:
    def test_rejects_short_signing_key(self):
        with pytest.raises(ValueError, match="32 characters"):
            GoogleOAuthProvider(
                google_client_id="x",
                google_client_secret="x",
                public_url="https://x.example.com",
                signing_key="too-short",
            )


class TestStaticTokens:
    async def test_write_token_grants_write_scope(self):
        provider = make_provider()
        token = await provider.load_access_token("write-secret")
        assert token is not None
        assert set(token.scopes) == {READ_SCOPE, WRITE_SCOPE}
        assert token.client_id == "static-write"

    async def test_read_token_grants_only_read(self):
        provider = make_provider()
        token = await provider.load_access_token("read-only-secret")
        assert token is not None
        assert token.scopes == [READ_SCOPE]

    async def test_unknown_token_rejected(self):
        provider = make_provider()
        assert await provider.load_access_token("not-a-token") is None

    async def test_static_tokens_not_required(self):
        provider = make_provider(static_read_token=None, static_write_token=None)
        assert await provider.load_access_token("anything") is None


class TestJwtRoundTrip:
    async def test_mint_then_load_succeeds(self):
        provider = make_provider()
        oauth_token = provider._mint_token_pair(
            client_id="claude-desktop-1",
            email="alice@vinesofmendoza.com",
            scopes=[READ_SCOPE, WRITE_SCOPE],
        )
        loaded = await provider.load_access_token(oauth_token.access_token)
        assert loaded is not None
        assert set(loaded.scopes) == {READ_SCOPE, WRITE_SCOPE}
        assert loaded.client_id == "claude-desktop-1"

    async def test_expired_jwt_rejected(self):
        provider = make_provider()
        expired = jwt.encode(
            {
                "sub": "alice@vinesofmendoza.com",
                "client_id": "x",
                "scope": READ_SCOPE,
                "aud": JWT_AUDIENCE,
                "iat": int(time.time()) - 7200,
                "exp": int(time.time()) - 3600,
            },
            SIGNING_KEY,
            algorithm=JWT_ALGORITHM,
        )
        assert await provider.load_access_token(expired) is None

    async def test_wrong_audience_rejected(self):
        provider = make_provider()
        bad_aud = jwt.encode(
            {
                "sub": "alice@vinesofmendoza.com",
                "client_id": "x",
                "scope": READ_SCOPE,
                "aud": "some-other-service",
                "iat": int(time.time()),
                "exp": int(time.time()) + 3600,
            },
            SIGNING_KEY,
            algorithm=JWT_ALGORITHM,
        )
        assert await provider.load_access_token(bad_aud) is None

    async def test_tampered_signature_rejected(self):
        provider = make_provider()
        forged = jwt.encode(
            {
                "sub": "evil@example.com",
                "client_id": "x",
                "scope": WRITE_SCOPE,
                "aud": JWT_AUDIENCE,
                "iat": int(time.time()),
                "exp": int(time.time()) + 3600,
            },
            "wrong-key" * 8,
            algorithm=JWT_ALGORITHM,
        )
        assert await provider.load_access_token(forged) is None


class TestDomainRestriction:
    def test_allows_matching_hd_claim(self):
        provider = make_provider()
        assert provider.is_email_allowed(
            "alice@vinesofmendoza.com", "vinesofmendoza.com"
        )

    def test_allows_matching_email_suffix_without_hd(self):
        provider = make_provider()
        # Personal Workspace accounts don't always set `hd`; the email suffix
        # still anchors them to the org.
        assert provider.is_email_allowed("alice@vinesofmendoza.com", None)

    def test_rejects_outside_domain(self):
        provider = make_provider()
        assert not provider.is_email_allowed("stranger@gmail.com", None)
        assert not provider.is_email_allowed(
            "stranger@gmail.com", "gmail.com"
        )

    def test_no_restriction_when_domain_unset(self):
        provider = make_provider(allowed_domain=None)
        assert provider.is_email_allowed("stranger@gmail.com", None)

    def test_extras_bypass_domain_restriction(self):
        provider = make_provider(extra_allowed_emails={"fs.phan@gmail.com"})
        # Explicitly listed off-domain email is allowed despite the restriction
        assert provider.is_email_allowed("fs.phan@gmail.com", None)
        # And the restriction still bites for unrelated off-domain emails
        assert not provider.is_email_allowed("stranger@gmail.com", None)

    def test_extras_match_is_case_insensitive(self):
        provider = make_provider(extra_allowed_emails={"fs.phan@gmail.com"})
        assert provider.is_email_allowed("FS.Phan@Gmail.com", None)


class TestScopeAssignment:
    def test_listed_email_gets_write(self):
        provider = make_provider()
        assert set(provider.scopes_for_email("alice@vinesofmendoza.com")) == {
            READ_SCOPE,
            WRITE_SCOPE,
        }

    def test_unlisted_email_gets_read_only(self):
        provider = make_provider()
        assert provider.scopes_for_email("bob@vinesofmendoza.com") == [READ_SCOPE]

    def test_write_email_matching_is_case_insensitive(self):
        provider = make_provider()
        assert WRITE_SCOPE in provider.scopes_for_email("ALICE@vinesofmendoza.com")


class TestAuthorizeRedirect:
    async def test_authorize_redirects_to_google_with_hd(self):
        provider = make_provider()
        client = OAuthClientInformationFull(
            client_id="cli-1",
            redirect_uris=[AnyUrl("http://localhost:33419/callback")],
        )
        from mcp.server.auth.provider import AuthorizationParams

        params = AuthorizationParams(
            state="client-state-abc",
            scopes=[READ_SCOPE],
            code_challenge="some-challenge",
            redirect_uri=AnyUrl("http://localhost:33419/callback"),
            redirect_uri_provided_explicitly=True,
        )
        url = await provider.authorize(client, params)
        assert url.startswith("https://accounts.google.com/o/oauth2/v2/auth?")
        assert "hd=vinesofmendoza.com" in url
        assert "client_id=goog-client.apps.googleusercontent.com" in url
        # The state forwarded to Google is our internal handle, not the client's
        assert "state=client-state-abc" not in url
        # One pending record should be stashed for the callback to consume
        assert len(provider._pending) == 1

    async def test_authorize_omits_hd_when_extras_present(self):
        # If any email is on the off-domain escape hatch we cannot let Google
        # hard-filter the account picker — otherwise those users get a
        # "this account isn't allowed" error before our callback runs.
        provider = make_provider(extra_allowed_emails={"fs.phan@gmail.com"})
        client = OAuthClientInformationFull(
            client_id="cli-1",
            redirect_uris=[AnyUrl("http://localhost:33419/callback")],
        )
        from mcp.server.auth.provider import AuthorizationParams

        params = AuthorizationParams(
            state="x",
            scopes=[READ_SCOPE],
            code_challenge="ch",
            redirect_uri=AnyUrl("http://localhost:33419/callback"),
            redirect_uri_provided_explicitly=True,
        )
        url = await provider.authorize(client, params)
        assert "hd=" not in url


class TestPendingStateLifecycle:
    def test_pop_consumes_once(self):
        provider = make_provider()
        provider.stash_pending("st-1", {"client_id": "cli", "redirect_uri": "x"})
        assert provider.pop_pending("st-1") is not None
        assert provider.pop_pending("st-1") is None

    def test_pop_expired_returns_none(self, monkeypatch):
        provider = make_provider()
        # Stash, then warp time forward past the TTL
        provider.stash_pending("st-2", {"client_id": "cli", "redirect_uri": "x"})
        from src import oauth_provider

        monkeypatch.setattr(
            oauth_provider,
            "_now",
            lambda: int(time.time()) + oauth_provider.PENDING_AUTH_TTL_SEC + 5,
        )
        assert provider.pop_pending("st-2") is None


class TestAuthorizationCodeRoundTrip:
    async def test_issue_load_exchange_roundtrip(self):
        provider = make_provider()
        client = OAuthClientInformationFull(
            client_id="cli-2",
            redirect_uris=[AnyUrl("http://localhost:33419/callback")],
        )
        code = provider.issue_authorization_code(
            client_id="cli-2",
            redirect_uri=AnyUrl("http://localhost:33419/callback"),
            redirect_uri_provided_explicitly=True,
            code_challenge="ch",
            scopes=[READ_SCOPE],
            email="alice@vinesofmendoza.com",
        )
        loaded = await provider.load_authorization_code(client, code)
        assert loaded is not None
        assert loaded.email == "alice@vinesofmendoza.com"

        token = await provider.exchange_authorization_code(client, loaded)
        assert token.access_token
        # Code is single-use
        assert await provider.load_authorization_code(client, code) is None

    async def test_load_wrong_client_returns_none(self):
        provider = make_provider()
        client_b = OAuthClientInformationFull(
            client_id="cli-b", redirect_uris=[AnyUrl("http://localhost:2/cb")]
        )
        code = provider.issue_authorization_code(
            client_id="cli-a",
            redirect_uri=AnyUrl("http://localhost:1/cb"),
            redirect_uri_provided_explicitly=True,
            code_challenge="ch",
            scopes=[READ_SCOPE],
            email="alice@vinesofmendoza.com",
        )
        assert await provider.load_authorization_code(client_b, code) is None


class TestRefreshTokenFlow:
    async def test_refresh_rotates_and_reissues(self):
        provider = make_provider()
        client = OAuthClientInformationFull(
            client_id="cli-3",
            redirect_uris=[AnyUrl("http://localhost:1/cb")],
        )
        first = provider._mint_token_pair(
            client_id="cli-3",
            email="alice@vinesofmendoza.com",
            scopes=[READ_SCOPE, WRITE_SCOPE],
        )
        loaded_rt = await provider.load_refresh_token(client, first.refresh_token)
        assert loaded_rt is not None

        second = await provider.exchange_refresh_token(client, loaded_rt, [])
        assert second.access_token != first.access_token
        assert second.refresh_token != first.refresh_token

        # Old refresh token must be gone
        assert await provider.load_refresh_token(client, first.refresh_token) is None

    async def test_refresh_rederives_scopes_from_current_allowlist(self):
        provider = make_provider()
        client = OAuthClientInformationFull(
            client_id="cli-4",
            redirect_uris=[AnyUrl("http://localhost:1/cb")],
        )
        first = provider._mint_token_pair(
            client_id="cli-4",
            email="alice@vinesofmendoza.com",
            scopes=[READ_SCOPE, WRITE_SCOPE],
        )
        # Operator pulls alice off the write list
        provider.write_emails.discard("alice@vinesofmendoza.com")

        loaded_rt = await provider.load_refresh_token(client, first.refresh_token)
        second = await provider.exchange_refresh_token(client, loaded_rt, [])
        loaded_access = await provider.load_access_token(second.access_token)
        assert loaded_access.scopes == [READ_SCOPE]
