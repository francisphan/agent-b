"""Tests for src/sf_write_tools.py."""

from unittest.mock import MagicMock, patch

import pytest

from src.sf_write_tools import register_tools


@pytest.fixture
def mcp_with_tools():
    """Create a mock MCP instance and register SF write tools on it."""
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
            "sf_create_record",
            "sf_update_record",
            "sf_delete_record",
            "sf_upsert_record",
            "sf_bulk_operation",
        }
        assert set(mcp_with_tools.keys()) == expected


class TestSfCreateRecord:
    @patch("src.sf_write_tools.create_record")
    @patch("src.sf_write_tools.require_write_access")
    def test_success(self, mock_auth, mock_create, mcp_with_tools):
        mock_create.return_value = {"id": "001xx", "success": True, "errors": []}
        result = mcp_with_tools["sf_create_record"]("Account", {"Name": "Acme"})
        mock_auth.assert_called_once()
        mock_create.assert_called_once_with("Account", {"Name": "Acme"})
        assert result["success"] is True

    @patch("src.sf_write_tools.require_write_access")
    def test_permission_denied(self, mock_auth, mcp_with_tools):
        mock_auth.side_effect = PermissionError("Write access denied")
        result = mcp_with_tools["sf_create_record"]("Account", {"Name": "Acme"})
        assert result == {"error": "Write access denied"}

    @patch("src.sf_write_tools.create_record")
    @patch("src.sf_write_tools.require_write_access")
    def test_api_error(self, mock_auth, mock_create, mcp_with_tools):
        mock_create.side_effect = Exception("INVALID_FIELD")
        result = mcp_with_tools["sf_create_record"]("Account", {"Bad__c": "x"})
        assert result == {"error": "INVALID_FIELD"}


class TestSfUpdateRecord:
    @patch("src.sf_write_tools.update_record")
    @patch("src.sf_write_tools.require_write_access")
    def test_success(self, mock_auth, mock_update, mcp_with_tools):
        mock_update.return_value = {"id": "001xx", "success": True}
        result = mcp_with_tools["sf_update_record"]("Account", "001xx", {"Name": "New"})
        mock_auth.assert_called_once()
        mock_update.assert_called_once_with("Account", "001xx", {"Name": "New"})
        assert result["success"] is True

    @patch("src.sf_write_tools.require_write_access")
    def test_permission_denied(self, mock_auth, mcp_with_tools):
        mock_auth.side_effect = PermissionError("Write access denied")
        result = mcp_with_tools["sf_update_record"]("Account", "001xx", {"Name": "New"})
        assert result == {"error": "Write access denied"}

    @patch("src.sf_write_tools.update_record")
    @patch("src.sf_write_tools.require_write_access")
    def test_api_error(self, mock_auth, mock_update, mcp_with_tools):
        mock_update.side_effect = Exception("NOT_FOUND")
        result = mcp_with_tools["sf_update_record"]("Account", "bad", {})
        assert result == {"error": "NOT_FOUND"}


class TestSfDeleteRecord:
    @patch("src.sf_write_tools.delete_record")
    @patch("src.sf_write_tools.require_write_access")
    def test_success(self, mock_auth, mock_delete, mcp_with_tools):
        mock_delete.return_value = {"id": "001xx", "success": True}
        result = mcp_with_tools["sf_delete_record"]("Account", "001xx")
        mock_auth.assert_called_once()
        mock_delete.assert_called_once_with("Account", "001xx")
        assert result["success"] is True

    @patch("src.sf_write_tools.require_write_access")
    def test_permission_denied(self, mock_auth, mcp_with_tools):
        mock_auth.side_effect = PermissionError("Write access denied")
        result = mcp_with_tools["sf_delete_record"]("Account", "001xx")
        assert result == {"error": "Write access denied"}

    @patch("src.sf_write_tools.delete_record")
    @patch("src.sf_write_tools.require_write_access")
    def test_api_error(self, mock_auth, mock_delete, mcp_with_tools):
        mock_delete.side_effect = Exception("ENTITY_IS_DELETED")
        result = mcp_with_tools["sf_delete_record"]("Account", "001xx")
        assert result == {"error": "ENTITY_IS_DELETED"}


class TestSfUpsertRecord:
    @patch("src.sf_write_tools.upsert_record")
    @patch("src.sf_write_tools.require_write_access")
    def test_success(self, mock_auth, mock_upsert, mcp_with_tools):
        mock_upsert.return_value = {"id": "001xx", "created": True}
        result = mcp_with_tools["sf_upsert_record"](
            "TVRS_Guest__c", "Email__c", "guest@test.com", {"Name": "Guest"}
        )
        mock_auth.assert_called_once()
        mock_upsert.assert_called_once_with(
            "TVRS_Guest__c", "Email__c", "guest@test.com", {"Name": "Guest"}
        )
        assert result["created"] is True

    @patch("src.sf_write_tools.require_write_access")
    def test_permission_denied(self, mock_auth, mcp_with_tools):
        mock_auth.side_effect = PermissionError("Write access denied")
        result = mcp_with_tools["sf_upsert_record"](
            "TVRS_Guest__c", "Email__c", "x@y.com", {}
        )
        assert result == {"error": "Write access denied"}

    @patch("src.sf_write_tools.upsert_record")
    @patch("src.sf_write_tools.require_write_access")
    def test_api_error(self, mock_auth, mock_upsert, mcp_with_tools):
        mock_upsert.side_effect = Exception("DUPLICATE_VALUE")
        result = mcp_with_tools["sf_upsert_record"](
            "Account", "ExtId__c", "123", {"Name": "A"}
        )
        assert result == {"error": "DUPLICATE_VALUE"}
