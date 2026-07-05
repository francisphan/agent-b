"""OAuth 1.0 Token-Based Authentication with HMAC-SHA256."""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import time
from typing import Generator
from urllib.parse import quote, urlencode

import httpx

from .base import NetSuiteAuth
from ..models import TBAConfig


class TBAAuth(NetSuiteAuth):
    """OAuth 1.0 Token-Based Authentication with HMAC-SHA256."""

    def __init__(self, config: TBAConfig, realm: str) -> None:
        self._config = config
        self._realm = realm

    def auth_flow(self, request: httpx.Request) -> Generator[httpx.Request, httpx.Response, None]:
        nonce = secrets.token_hex(16)
        timestamp = str(int(time.time()))

        oauth_params = {
            "oauth_consumer_key": self._config.consumer_key,
            "oauth_nonce": nonce,
            "oauth_signature_method": "HMAC-SHA256",
            "oauth_timestamp": timestamp,
            "oauth_token": self._config.token_key,
            "oauth_version": "1.0",
        }

        signature = self._compute_signature(request, oauth_params)
        oauth_params["oauth_signature"] = signature

        request.headers["Authorization"] = self._build_header(oauth_params)
        yield request

    def _compute_signature(self, request: httpx.Request, oauth_params: dict[str, str]) -> str:
        method = request.method.upper()

        url = request.url.copy_with(query=None, fragment=None)
        base_url = str(url)

        all_params: dict[str, str] = dict(oauth_params)
        for key, value in request.url.params.multi_items():
            all_params[key] = value

        sorted_params = sorted(all_params.items())
        normalized_params = urlencode(sorted_params, quote_via=quote)

        base_string = "&".join(
            [
                _percent_encode(method),
                _percent_encode(base_url),
                _percent_encode(normalized_params),
            ]
        )

        signing_key = (
            _percent_encode(self._config.consumer_secret)
            + "&"
            + _percent_encode(self._config.token_secret)
        )

        hashed = hmac.new(
            signing_key.encode("utf-8"),
            base_string.encode("utf-8"),
            hashlib.sha256,
        )
        return base64.b64encode(hashed.digest()).decode("utf-8")

    def _build_header(self, oauth_params: dict[str, str]) -> str:
        parts = [f'realm="{_percent_encode(self._realm)}"']
        for key, value in sorted(oauth_params.items()):
            parts.append(f'{_percent_encode(key)}="{_percent_encode(value)}"')
        return "OAuth " + ", ".join(parts)


def _percent_encode(s: str) -> str:
    """RFC 5849 percent encoding."""
    return quote(s, safe="")
