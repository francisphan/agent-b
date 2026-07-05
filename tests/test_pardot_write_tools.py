"""Tests for src/pardot_write_tools.py."""

from unittest.mock import MagicMock, patch

import pytest

from src.pardot_write_tools import register_tools


@pytest.fixture
def mcp_with_tools():
    """Create a mock MCP instance and register Pardot write tools on it."""
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
        # Core tools that must always be present
        expected = {
            "pardot_create_prospect",
            "pardot_update_prospect",
            "pardot_delete_prospect",
            "pardot_upsert_prospect",
            "pardot_undelete_prospect",
            "pardot_create_email_template",
            "pardot_update_email_template",
            "pardot_delete_email_template",
            "pardot_create_list",
            "pardot_update_list",
            "pardot_delete_list",
            "pardot_create_list_membership",
            "pardot_update_list_membership",
            "pardot_delete_list_membership",
            "pardot_create_email",
            "pardot_create_list_email",
            "pardot_create_custom_field",
            "pardot_update_custom_field",
            "pardot_delete_custom_field",
            "pardot_create_tag",
            "pardot_update_tag",
            "pardot_delete_tag",
            "pardot_create_engagement_studio_program",
            # Phase 3 write additions
            "pardot_create_custom_redirect",
            "pardot_update_custom_redirect",
            "pardot_delete_custom_redirect",
            "pardot_create_form_handler",
            "pardot_update_form_handler",
            "pardot_delete_form_handler",
            "pardot_create_form_handler_field",
            "pardot_update_form_handler_field",
            "pardot_delete_form_handler_field",
            "pardot_create_layout_template",
            "pardot_update_layout_template",
            "pardot_delete_layout_template",
            "pardot_create_file",
            "pardot_update_file",
            "pardot_delete_file",
            "pardot_create_landing_page",
            "pardot_create_dynamic_content",
            "pardot_create_dynamic_content_variation",
            "pardot_create_form_field",
            "pardot_create_form",
            "pardot_delete_form",
            # Phase 4 tag operations
            "pardot_add_tag",
            "pardot_remove_tag",
            # Phase 5 special actions
            "pardot_assign_visitor",
            "pardot_connect_campaign_to_sf",
            "pardot_merge_tags",
            "pardot_create_external_activity",
            # Phase 6 export
            "pardot_create_export",
            # Phase 7 import
            "pardot_create_import",
            "pardot_upload_import_batch",
            "pardot_submit_import",
            # pardot_download_import_errors is in read tools (pardot_tools.py)
        }
        assert expected.issubset(set(mcp_with_tools.keys()))


class TestPardotCreateProspect:
    @patch("src.pardot_write_tools.create_prospect")
    @patch("src.pardot_write_tools.require_write_access")
    def test_success(self, mock_auth, mock_create, mcp_with_tools):
        mock_create.return_value = {"id": "100", "email": "new@test.com"}
        result = mcp_with_tools["pardot_create_prospect"](
            {"email": "new@test.com", "firstName": "Jane", "lastName": "Doe"}
        )
        mock_auth.assert_called_once()
        mock_create.assert_called_once_with(
            {"email": "new@test.com", "firstName": "Jane", "lastName": "Doe"}
        )
        assert result["id"] == "100"

    @patch("src.pardot_write_tools.require_write_access")
    def test_permission_denied(self, mock_auth, mcp_with_tools):
        mock_auth.side_effect = PermissionError("Write access denied")
        result = mcp_with_tools["pardot_create_prospect"]({"email": "a@b.com"})
        assert result == {"error": "Write access denied"}

    @patch("src.pardot_write_tools.create_prospect")
    @patch("src.pardot_write_tools.require_write_access")
    def test_api_error(self, mock_auth, mock_create, mcp_with_tools):
        mock_create.side_effect = Exception("Email already exists")
        result = mcp_with_tools["pardot_create_prospect"]({"email": "dup@test.com"})
        assert result == {"error": "Email already exists"}


class TestPardotUpdateProspect:
    @patch("src.pardot_write_tools.update_prospect")
    @patch("src.pardot_write_tools.require_write_access")
    def test_success(self, mock_auth, mock_update, mcp_with_tools):
        mock_update.return_value = {"id": "100", "firstName": "Updated"}
        result = mcp_with_tools["pardot_update_prospect"]("100", {"firstName": "Updated"})
        mock_auth.assert_called_once()
        mock_update.assert_called_once_with("100", {"firstName": "Updated"})
        assert result["firstName"] == "Updated"

    @patch("src.pardot_write_tools.require_write_access")
    def test_permission_denied(self, mock_auth, mcp_with_tools):
        mock_auth.side_effect = PermissionError("Write access denied")
        result = mcp_with_tools["pardot_update_prospect"]("100", {"firstName": "X"})
        assert result == {"error": "Write access denied"}

    @patch("src.pardot_write_tools.update_prospect")
    @patch("src.pardot_write_tools.require_write_access")
    def test_api_error(self, mock_auth, mock_update, mcp_with_tools):
        mock_update.side_effect = Exception("Prospect not found")
        result = mcp_with_tools["pardot_update_prospect"]("999", {"firstName": "X"})
        assert result == {"error": "Prospect not found"}
