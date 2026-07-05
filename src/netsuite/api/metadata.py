"""Metadata catalog discovery operations."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ..client import NetSuiteClient


class MetadataApi:
    """Metadata catalog discovery operations. Accessed via ``client.metadata``."""

    def __init__(self, client: NetSuiteClient) -> None:
        self._client = client
        self._base_path = "/services/rest/record/v1/metadata-catalog"

    def list_record_types(self, *, select: list[str] | None = None) -> dict[str, Any]:
        params: dict[str, str] = {}
        if select:
            params["select"] = ",".join(select)
        return self._client._request_sync("GET", self._base_path, params=params)

    def get_record_schema(self, record_type: str) -> dict[str, Any]:
        return self._client._request_sync("GET", f"{self._base_path}/{record_type}")
