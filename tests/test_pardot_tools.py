"""Tests for src/pardot_tools.py."""

from unittest.mock import MagicMock, patch

import pytest

from src.pardot_tools import (
    DEFAULT_CAMPAIGN_FIELDS,
    DEFAULT_EMAIL_TEMPLATE_FIELDS,
    DEFAULT_FORM_FIELDS,
    DEFAULT_LIST_FIELDS,
    DEFAULT_LIST_MEMBERSHIP_FIELDS,
    DEFAULT_PROSPECT_FIELDS,
    DEFAULT_VISITOR_ACTIVITY_FIELDS,
    register_tools,
)


@pytest.fixture
def mcp_with_tools():
    """Create a mock MCP instance and register Pardot tools on it."""
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
            "pardot_query_prospects",
            "pardot_get_prospect",
            "pardot_query_lists",
            "pardot_get_list",
            "pardot_query_list_memberships",
            "pardot_query_campaigns",
            "pardot_get_campaign",
            "pardot_query_visitor_activities",
            "pardot_query_forms",
            "pardot_get_form",
            "pardot_query_email_templates",
            "pardot_get_email_template",
            # Phase 1 additions
            "pardot_get_visitor_activity",
            "pardot_get_tracker_domain",
            "pardot_get_list_email_stats",
            # Phase 2 additions
            "pardot_query_visitors",
            "pardot_get_visitor",
            "pardot_query_visits",
            "pardot_get_visit",
            "pardot_query_prospect_accounts",
            "pardot_get_prospect_account",
            "pardot_query_opportunities",
            "pardot_get_opportunity",
            "pardot_query_lifecycle_stages",
            "pardot_get_lifecycle_stage",
            "pardot_query_lifecycle_histories",
            "pardot_get_lifecycle_history",
            "pardot_query_users",
            "pardot_get_user",
            "pardot_get_account",
            "pardot_query_folders",
            "pardot_get_folder",
            "pardot_query_folder_contents",
            # Phase 3 read additions
            "pardot_query_custom_redirects",
            "pardot_get_custom_redirect",
            "pardot_query_form_handlers",
            "pardot_get_form_handler",
            "pardot_query_form_handler_fields",
            "pardot_get_form_handler_field",
            "pardot_query_layout_templates",
            "pardot_get_layout_template",
            "pardot_query_files",
            "pardot_get_file",
            "pardot_query_landing_pages",
            "pardot_get_landing_page",
            "pardot_query_dynamic_contents",
            "pardot_get_dynamic_content",
            "pardot_query_dynamic_content_variations",
            "pardot_get_dynamic_content_variation",
            "pardot_query_form_fields",
            "pardot_get_form_field",
            # Phase 5 read additions
            "pardot_query_external_activities",
            "pardot_get_external_activity",
            # Phase 6 read additions
            "pardot_query_exports",
            "pardot_get_export",
            "pardot_download_export_results",
            # Phase 7 read additions
            "pardot_query_imports",
            "pardot_get_import",
            "pardot_download_import_errors",
        }
        assert expected.issubset(set(mcp_with_tools.keys()))


class TestQueryProspects:
    @patch("src.pardot_tools.query_prospects")
    def test_success_default_params(self, mock_qp, mcp_with_tools):
        mock_qp.return_value = {"values": [{"id": "1"}]}
        result = mcp_with_tools["pardot_query_prospects"]()
        mock_qp.assert_called_once_with({"fields": DEFAULT_PROSPECT_FIELDS})
        assert result == {"values": [{"id": "1"}]}

    @patch("src.pardot_tools.query_prospects")
    def test_success_with_params(self, mock_qp, mcp_with_tools):
        mock_qp.return_value = {"values": []}
        result = mcp_with_tools["pardot_query_prospects"](
            fields="email,firstName", order_by="created_at", limit=50
        )
        mock_qp.assert_called_once_with(
            {"fields": "email,firstName", "orderBy": "created_at", "limit": 50}
        )
        assert result == {"values": []}

    @patch("src.pardot_tools.query_prospects")
    def test_error(self, mock_qp, mcp_with_tools):
        mock_qp.side_effect = RuntimeError("API down")
        result = mcp_with_tools["pardot_query_prospects"]()
        assert result == {"error": "API down"}


class TestGetProspect:
    @patch("src.pardot_tools.get_prospect")
    def test_success(self, mock_gp, mcp_with_tools):
        mock_gp.return_value = {"id": "42", "email": "a@b.com"}
        result = mcp_with_tools["pardot_get_prospect"]("42")
        mock_gp.assert_called_once_with("42", fields=DEFAULT_PROSPECT_FIELDS)
        assert result["id"] == "42"

    @patch("src.pardot_tools.get_prospect")
    def test_error(self, mock_gp, mcp_with_tools):
        mock_gp.side_effect = Exception("not found")
        result = mcp_with_tools["pardot_get_prospect"]("999")
        assert result == {"error": "not found"}


class TestQueryLists:
    @patch("src.pardot_tools.query_lists")
    def test_success(self, mock_ql, mcp_with_tools):
        mock_ql.return_value = {"values": [{"id": "1", "name": "Test"}]}
        result = mcp_with_tools["pardot_query_lists"]()
        mock_ql.assert_called_once_with({"fields": DEFAULT_LIST_FIELDS})
        assert result == {"values": [{"id": "1", "name": "Test"}]}

    @patch("src.pardot_tools.query_lists")
    def test_error(self, mock_ql, mcp_with_tools):
        mock_ql.side_effect = Exception("fail")
        result = mcp_with_tools["pardot_query_lists"]()
        assert result == {"error": "fail"}


class TestGetList:
    @patch("src.pardot_tools.get_list")
    def test_success(self, mock_gl, mcp_with_tools):
        mock_gl.return_value = {"id": "5"}
        result = mcp_with_tools["pardot_get_list"]("5")
        assert result == {"id": "5"}

    @patch("src.pardot_tools.get_list")
    def test_error(self, mock_gl, mcp_with_tools):
        mock_gl.side_effect = Exception("err")
        assert mcp_with_tools["pardot_get_list"]("5") == {"error": "err"}


class TestQueryListMemberships:
    @patch("src.pardot_tools.query_list_memberships")
    def test_success_with_filters(self, mock_qlm, mcp_with_tools):
        mock_qlm.return_value = {"values": []}
        mcp_with_tools["pardot_query_list_memberships"](list_id="10", prospect_id="20")
        mock_qlm.assert_called_once_with({"fields": DEFAULT_LIST_MEMBERSHIP_FIELDS, "listId": "10", "prospectId": "20"})

    @patch("src.pardot_tools.query_list_memberships")
    def test_error(self, mock_qlm, mcp_with_tools):
        mock_qlm.side_effect = Exception("fail")
        result = mcp_with_tools["pardot_query_list_memberships"]()
        assert result == {"error": "fail"}


class TestQueryCampaigns:
    @patch("src.pardot_tools.query_campaigns")
    def test_success(self, mock_qc, mcp_with_tools):
        mock_qc.return_value = {"values": []}
        mcp_with_tools["pardot_query_campaigns"]()
        mock_qc.assert_called_once_with({"fields": DEFAULT_CAMPAIGN_FIELDS})

    @patch("src.pardot_tools.query_campaigns")
    def test_error(self, mock_qc, mcp_with_tools):
        mock_qc.side_effect = Exception("fail")
        assert mcp_with_tools["pardot_query_campaigns"]() == {"error": "fail"}


class TestGetCampaign:
    @patch("src.pardot_tools.get_campaign")
    def test_success(self, mock_gc, mcp_with_tools):
        mock_gc.return_value = {"id": "7"}
        assert mcp_with_tools["pardot_get_campaign"]("7") == {"id": "7"}
        mock_gc.assert_called_once_with("7", fields=DEFAULT_CAMPAIGN_FIELDS)

    @patch("src.pardot_tools.get_campaign")
    def test_error(self, mock_gc, mcp_with_tools):
        mock_gc.side_effect = Exception("fail")
        assert mcp_with_tools["pardot_get_campaign"]("7") == {"error": "fail"}


class TestQueryVisitorActivities:
    @patch("src.pardot_tools.query_visitor_activities")
    def test_success_with_filters(self, mock_qva, mcp_with_tools):
        mock_qva.return_value = {"values": []}
        mcp_with_tools["pardot_query_visitor_activities"](
            prospect_id="1", activity_type="Visit", limit=50
        )
        mock_qva.assert_called_once_with(
            {"fields": DEFAULT_VISITOR_ACTIVITY_FIELDS, "prospectId": "1", "type": "Visit", "limit": 50}
        )

    @patch("src.pardot_tools.query_visitor_activities")
    def test_error(self, mock_qva, mcp_with_tools):
        mock_qva.side_effect = Exception("fail")
        assert mcp_with_tools["pardot_query_visitor_activities"]() == {"error": "fail"}


class TestQueryForms:
    @patch("src.pardot_tools.query_forms")
    def test_success(self, mock_qf, mcp_with_tools):
        mock_qf.return_value = {"values": []}
        mcp_with_tools["pardot_query_forms"]()
        mock_qf.assert_called_once_with({"fields": DEFAULT_FORM_FIELDS})

    @patch("src.pardot_tools.query_forms")
    def test_error(self, mock_qf, mcp_with_tools):
        mock_qf.side_effect = Exception("fail")
        assert mcp_with_tools["pardot_query_forms"]() == {"error": "fail"}


class TestGetForm:
    @patch("src.pardot_tools.get_form")
    def test_success(self, mock_gf, mcp_with_tools):
        mock_gf.return_value = {"id": "9"}
        assert mcp_with_tools["pardot_get_form"]("9") == {"id": "9"}
        mock_gf.assert_called_once_with("9", fields=DEFAULT_FORM_FIELDS)

    @patch("src.pardot_tools.get_form")
    def test_error(self, mock_gf, mcp_with_tools):
        mock_gf.side_effect = Exception("fail")
        assert mcp_with_tools["pardot_get_form"]("9") == {"error": "fail"}


class TestQueryEmailTemplates:
    @patch("src.pardot_tools.query_email_templates")
    def test_success(self, mock_qet, mcp_with_tools):
        mock_qet.return_value = {"values": []}
        mcp_with_tools["pardot_query_email_templates"]()
        mock_qet.assert_called_once_with({"fields": DEFAULT_EMAIL_TEMPLATE_FIELDS})

    @patch("src.pardot_tools.query_email_templates")
    def test_error(self, mock_qet, mcp_with_tools):
        mock_qet.side_effect = Exception("fail")
        assert mcp_with_tools["pardot_query_email_templates"]() == {"error": "fail"}


class TestGetEmailTemplate:
    @patch("src.pardot_tools.get_email_template")
    def test_success(self, mock_get, mcp_with_tools):
        mock_get.return_value = {"id": "11"}
        assert mcp_with_tools["pardot_get_email_template"]("11") == {"id": "11"}
        mock_get.assert_called_once_with("11", fields=DEFAULT_EMAIL_TEMPLATE_FIELDS)

    @patch("src.pardot_tools.get_email_template")
    def test_error(self, mock_get, mcp_with_tools):
        mock_get.side_effect = Exception("fail")
        assert mcp_with_tools["pardot_get_email_template"]("11") == {"error": "fail"}
