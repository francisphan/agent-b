"""Unified NetSuite client (sync-only, TBA auth)."""

from __future__ import annotations

import logging
import time
from typing import Any

import httpx

from ._retry import calculate_backoff, parse_retry_after
from .api.metadata import MetadataApi
from .api.rest import RestApi
from .api.suiteql import SuiteQLApi
from .auth.tba import TBAAuth
from .exceptions import (
    STATUS_EXCEPTION_MAP,
    ConcurrencyLimitError,
    NetSuiteError,
)
from .models import NetSuiteConfig, NetSuiteErrorResponse

logger = logging.getLogger(__name__)

# Verbs safe to retry after a transport fault that may have reached the server.
_IDEMPOTENT_METHODS = {"GET", "HEAD", "OPTIONS"}

# Cap how much of a request/response body we write to the logs. SuiteQL queries
# and NetSuite error payloads are usually small, but record writes can be large
# and may carry PII, so we never log an unbounded body.
_MAX_LOG_BODY = 2000

# Cap how much NetSuite error detail we fold into the exception message. The full
# body is still logged; this keeps the string a tool caller (e.g. an LLM agent)
# sees actionable without dumping an unbounded payload into it.
_MAX_ERROR_DETAIL = 500


def _clip(value: Any, limit: int = _MAX_LOG_BODY) -> str:
    """Render a body for logging or messaging, truncated to a safe length."""
    text = value if isinstance(value, str) else str(value)
    if len(text) > limit:
        return f"{text[:limit]}… (+{len(text) - limit} chars)"
    return text


class NetSuiteClient:
    """Unified client for NetSuite REST API, SuiteQL, and metadata.

    Usage::

        client = NetSuiteClient(config)
        customer = client.rest.get("customer", 123)
        results = client.suiteql.query("SELECT id, companyname FROM customer")
        client.close()
    """

    def __init__(self, config: NetSuiteConfig) -> None:
        self._config = config
        self._base_url = config.computed_base_url
        self._auth = TBAAuth(config.tba, realm=config.account_id)
        self._sync_client: httpx.Client | None = None

        self.rest = RestApi(self)
        self.suiteql = SuiteQLApi(self)
        self.metadata = MetadataApi(self)

    @property
    def _sync(self) -> httpx.Client:
        if self._sync_client is None or self._sync_client.is_closed:
            self._sync_client = httpx.Client(
                base_url=self._base_url,
                auth=self._auth,
                timeout=httpx.Timeout(self._config.timeout),
                headers={"Content-Type": "application/json"},
            )
        return self._sync_client

    def _request_sync(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json: dict[str, Any] | None = None,
        extra_headers: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        headers = dict(extra_headers) if extra_headers else {}
        last_exc: Exception | None = None

        for attempt in range(self._config.max_retries + 1):
            try:
                response = self._sync.request(
                    method, path, params=params, json=json, headers=headers
                )
            except httpx.TransportError as exc:
                # Connect-phase faults never reached the server, so they're safe to
                # retry for any verb. Other transport faults (e.g. read timeout)
                # may have been received, so only retry idempotent methods to avoid
                # double-applying a write.
                connect_phase = isinstance(
                    exc, (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout)
                )
                retryable = connect_phase or method.upper() in _IDEMPOTENT_METHODS
                if retryable and attempt < self._config.max_retries:
                    wait = calculate_backoff(
                        attempt, None, self._config.retry_backoff_factor
                    )
                    time.sleep(wait)
                    last_exc = exc
                    continue
                raise

            if response.status_code < 400:
                if response.status_code == 204 or not response.content:
                    return {}
                return response.json()

            exc = self._build_exception(response)

            will_retry = (
                response.status_code == 429 or response.status_code >= 500
            ) and attempt < self._config.max_retries
            self._log_response_error(
                method, path, params, json, response, attempt, will_retry
            )

            if response.status_code == 429 and attempt < self._config.max_retries:
                retry_after = parse_retry_after(response)
                wait = calculate_backoff(
                    attempt, retry_after, self._config.retry_backoff_factor
                )
                time.sleep(wait)
                last_exc = exc
                continue

            if response.status_code >= 500 and attempt < self._config.max_retries:
                wait = calculate_backoff(
                    attempt, None, self._config.retry_backoff_factor
                )
                time.sleep(wait)
                last_exc = exc
                continue

            raise exc

        raise last_exc or NetSuiteError("Max retries exceeded")

    @staticmethod
    def _log_response_error(
        method: str,
        path: str,
        params: dict[str, Any] | None,
        body: dict[str, Any] | None,
        response: httpx.Response,
        attempt: int,
        will_retry: bool,
    ) -> None:
        """Log a non-2xx NetSuite response with the request that caused it.

        Includes the request body (e.g. the SuiteQL query) and NetSuite's error
        payload so 4xx/5xx responses are diagnosable from the logs alone.
        WARNING when the request will be retried, ERROR when it will be raised.
        """
        try:
            detail = _clip(response.text)
        except Exception:  # pragma: no cover - response body not readable
            detail = "<unreadable>"

        retry_note = " (will retry)" if will_retry else ""
        logger.log(
            logging.WARNING if will_retry else logging.ERROR,
            "NetSuite %s %s -> HTTP %d%s | params=%s | request=%s | response=%s",
            method,
            path,
            response.status_code,
            retry_note,
            params,
            _clip(body) if body is not None else None,
            detail,
        )

    @staticmethod
    def _build_exception(response: httpx.Response) -> NetSuiteError:
        try:
            body = response.json()
            error_resp = NetSuiteErrorResponse.model_validate(body)
        except Exception:
            # No parseable JSON body: still surface whatever NetSuite returned
            # (clipped) so the caller isn't left with a bare status code.
            message = f"HTTP {response.status_code}"
            try:
                text = response.text.strip()
            except Exception:  # pragma: no cover - response body not readable
                text = ""
            if text:
                message = f"{message}: {_clip(text, _MAX_ERROR_DETAIL)}"
            exc_class = STATUS_EXCEPTION_MAP.get(response.status_code, NetSuiteError)
            return exc_class(message, status=response.status_code)

        exc_class = STATUS_EXCEPTION_MAP.get(response.status_code, NetSuiteError)
        kwargs: dict[str, Any] = {
            "status": error_resp.status or response.status_code,
            "error_code": error_resp.error_code,
            "error_details": [d.model_dump() for d in error_resp.error_details],
        }
        if exc_class is ConcurrencyLimitError:
            kwargs["retry_after"] = parse_retry_after(response)

        # Fold NetSuite's o:errorDetails[].detail into the message. Without it the
        # caller only sees the generic title (e.g. "Bad Request") and can't tell a
        # SQL syntax error from a bad field name. error_details stays on the
        # exception for programmatic use. Each detail is clipped individually
        # (not the joined string) so a long first detail can't crowd out a
        # concise, actionable later one.
        prefix = error_resp.title or f"HTTP {response.status_code}"
        stripped = [s for d in error_resp.error_details if (s := d.detail.strip())]
        details = "; ".join(_clip(s, _MAX_ERROR_DETAIL) for s in stripped[:3])
        if len(stripped) > 3:
            details += f" (+{len(stripped) - 3} more details)"
        if details:
            message = f"{prefix}: {details}"
        elif not error_resp.title:
            # Valid JSON but not the {title, o:errorDetails} envelope (e.g. a
            # RESTlet-style {"message": ...}): surface the raw body rather than
            # collapsing to a bare status line.
            text = response.text.strip()
            message = f"{prefix}: {_clip(text, _MAX_ERROR_DETAIL)}" if text else prefix
        else:
            message = prefix
        return exc_class(message, **kwargs)

    def close(self) -> None:
        if self._sync_client and not self._sync_client.is_closed:
            self._sync_client.close()

    def __enter__(self) -> NetSuiteClient:
        return self

    def __exit__(self, *args: Any) -> None:
        self.close()
