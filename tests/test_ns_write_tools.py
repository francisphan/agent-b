"""Tests for src/ns_write_tools.py."""

from unittest.mock import MagicMock, patch

import pytest

from src.ns_write_tools import register_tools


@pytest.fixture
def mcp_with_tools():
    """Create a mock MCP instance and register NS write tools on it."""
    mcp = MagicMock()
    registered = {}

    def tool_decorator():
        def wrapper(func):
            registered[func.__name__] = func
            return func

        return wrapper

    mcp.tool = tool_decorator
    register_tools(mcp)
    return registered


class TestToolRegistration:
    def test_all_tools_registered(self, mcp_with_tools):
        expected = {
            "ns_create_record",
            "ns_update_record",
            "ns_upsert_record",
            "ns_delete_record",
        }
        assert set(mcp_with_tools.keys()) == expected


class TestNsCreateRecord:
    @patch("src.ns_write_tools.rest_create")
    @patch("src.ns_write_tools.require_write_access")
    def test_success(self, mock_auth, mock_create, mcp_with_tools):
        mock_create.return_value = {"id": "123", "type": "customer"}
        result = mcp_with_tools["ns_create_record"]("customer", {"companyName": "Acme"})
        mock_auth.assert_called_once()
        mock_create.assert_called_once_with("customer", {"companyName": "Acme"})
        assert result["id"] == "123"

    @patch("src.ns_write_tools.require_write_access")
    def test_permission_denied(self, mock_auth, mcp_with_tools):
        mock_auth.side_effect = PermissionError("Write access denied")
        result = mcp_with_tools["ns_create_record"]("customer", {"companyName": "Acme"})
        assert result == {"error": "Write access denied"}

    @patch("src.ns_write_tools.rest_create")
    @patch("src.ns_write_tools.require_write_access")
    def test_api_error(self, mock_auth, mock_create, mcp_with_tools):
        mock_create.side_effect = Exception("INVALID_RECORD_TYPE")
        result = mcp_with_tools["ns_create_record"]("badType", {})
        assert result == {"error": "INVALID_RECORD_TYPE"}


class TestNsUpdateRecord:
    @patch("src.ns_write_tools.rest_update")
    @patch("src.ns_write_tools.require_write_access")
    def test_success(self, mock_auth, mock_update, mcp_with_tools):
        mock_update.return_value = {"id": "123", "companyName": "Updated"}
        result = mcp_with_tools["ns_update_record"]("customer", "123", {"companyName": "Updated"})
        mock_auth.assert_called_once()
        mock_update.assert_called_once_with("customer", "123", {"companyName": "Updated"})
        assert result["companyName"] == "Updated"

    @patch("src.ns_write_tools.require_write_access")
    def test_permission_denied(self, mock_auth, mcp_with_tools):
        mock_auth.side_effect = PermissionError("Write access denied")
        result = mcp_with_tools["ns_update_record"]("customer", "123", {})
        assert result == {"error": "Write access denied"}

    @patch("src.ns_write_tools.rest_update")
    @patch("src.ns_write_tools.require_write_access")
    def test_api_error(self, mock_auth, mock_update, mcp_with_tools):
        mock_update.side_effect = Exception("RECORD_NOT_FOUND")
        result = mcp_with_tools["ns_update_record"]("customer", "999", {})
        assert result == {"error": "RECORD_NOT_FOUND"}


class TestNsUpsertRecord:
    @patch("src.ns_write_tools.rest_upsert")
    @patch("src.ns_write_tools.require_write_access")
    def test_success(self, mock_auth, mock_upsert, mcp_with_tools):
        mock_upsert.return_value = {"id": "123", "created": True}
        result = mcp_with_tools["ns_upsert_record"]("customer", {"companyName": "Acme"}, "ext-001")
        mock_auth.assert_called_once()
        mock_upsert.assert_called_once_with("customer", {"companyName": "Acme"}, "ext-001")
        assert result["created"] is True

    @patch("src.ns_write_tools.require_write_access")
    def test_permission_denied(self, mock_auth, mcp_with_tools):
        mock_auth.side_effect = PermissionError("Write access denied")
        result = mcp_with_tools["ns_upsert_record"]("customer", {}, "ext-001")
        assert result == {"error": "Write access denied"}

    @patch("src.ns_write_tools.rest_upsert")
    @patch("src.ns_write_tools.require_write_access")
    def test_api_error(self, mock_auth, mock_upsert, mcp_with_tools):
        mock_upsert.side_effect = Exception("UPSERT_FAILED")
        result = mcp_with_tools["ns_upsert_record"]("customer", {}, "ext-001")
        assert result == {"error": "UPSERT_FAILED"}


class TestNsDeleteRecord:
    @patch("src.ns_write_tools.rest_delete")
    @patch("src.ns_write_tools.require_write_access")
    def test_success(self, mock_auth, mock_delete, mcp_with_tools):
        mock_delete.return_value = None
        result = mcp_with_tools["ns_delete_record"]("customer", "123")
        mock_auth.assert_called_once()
        mock_delete.assert_called_once_with("customer", "123")
        assert result == {"success": True, "record_type": "customer", "record_id": "123"}

    @patch("src.ns_write_tools.require_write_access")
    def test_permission_denied(self, mock_auth, mcp_with_tools):
        mock_auth.side_effect = PermissionError("Write access denied")
        result = mcp_with_tools["ns_delete_record"]("customer", "123")
        assert result == {"error": "Write access denied"}

    @patch("src.ns_write_tools.rest_delete")
    @patch("src.ns_write_tools.require_write_access")
    def test_api_error(self, mock_auth, mock_delete, mcp_with_tools):
        mock_delete.side_effect = Exception("RECORD_NOT_FOUND")
        result = mcp_with_tools["ns_delete_record"]("customer", "999")
        assert result == {"error": "RECORD_NOT_FOUND"}
