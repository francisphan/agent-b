"""REST Record CRUD operations."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any, Iterator

from .._pagination import SyncPageIterator, iter_items_sync
from ..models import PaginatedResponse

if TYPE_CHECKING:
    from ..client import NetSuiteClient

_RECORD_TYPE_RE = re.compile(r"^[a-zA-Z][a-zA-Z0-9_]{0,99}$")
_RECORD_ID_RE = re.compile(r"^[a-zA-Z0-9._@+%-]+$")


class RestApi:
    """REST Record CRUD operations. Accessed via ``client.rest``."""

    def __init__(self, client: NetSuiteClient) -> None:
        self._client = client
        self._base_path = "/services/rest/record/v1"

    def _record_path(self, record_type: str, record_id: str | int | None = None) -> str:
        if not _RECORD_TYPE_RE.match(record_type):
            raise ValueError(f"Invalid record type: {record_type!r}")
        path = f"{self._base_path}/{record_type}"
        if record_id is not None:
            rid = str(record_id)
            if not rid or ".." in rid or "/" in rid or not _RECORD_ID_RE.match(rid):
                raise ValueError(f"Invalid record ID: {record_id!r}")
            path = f"{path}/{rid}"
        return path

    def get(
        self,
        record_type: str,
        record_id: str | int,
        *,
        expand_sub_resources: bool = False,
        fields: list[str] | None = None,
    ) -> dict[str, Any]:
        params: dict[str, Any] = {}
        if expand_sub_resources:
            params["expandSubResources"] = "true"
        if fields:
            params["fields"] = ",".join(fields)
        return self._client._request_sync(
            "GET", self._record_path(record_type, record_id), params=params
        )

    def create(self, record_type: str, body: dict[str, Any]) -> dict[str, Any]:
        return self._client._request_sync("POST", self._record_path(record_type), json=body)

    def update(
        self, record_type: str, record_id: str | int, body: dict[str, Any]
    ) -> dict[str, Any]:
        return self._client._request_sync(
            "PATCH", self._record_path(record_type, record_id), json=body
        )

    def upsert(
        self, record_type: str, body: dict[str, Any], *, external_id: str
    ) -> dict[str, Any]:
        return self._client._request_sync(
            "PUT", self._record_path(record_type, external_id), json=body
        )

    def delete(self, record_type: str, record_id: str | int) -> None:
        self._client._request_sync("DELETE", self._record_path(record_type, record_id))

    def list(
        self,
        record_type: str,
        *,
        limit: int = 1000,
        offset: int = 0,
        q: str | None = None,
    ) -> PaginatedResponse:
        params: dict[str, Any] = {"limit": limit, "offset": offset}
        if q:
            params["q"] = q
        raw = self._client._request_sync("GET", self._record_path(record_type), params=params)
        return PaginatedResponse.model_validate(raw)

    def list_pages(
        self, record_type: str, *, limit: int = 1000, q: str | None = None
    ) -> SyncPageIterator:
        def fetch(lim: int, off: int) -> PaginatedResponse:
            return self.list(record_type, limit=lim, offset=off, q=q)

        return SyncPageIterator(fetch, limit=limit)

    def list_all(
        self, record_type: str, *, limit: int = 1000, q: str | None = None
    ) -> Iterator[dict[str, Any]]:
        def fetch(lim: int, off: int) -> PaginatedResponse:
            return self.list(record_type, limit=lim, offset=off, q=q)

        return iter_items_sync(fetch, limit=limit)
