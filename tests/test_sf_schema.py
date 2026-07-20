"""Tests for sf_get_schema tool and sf_schema module."""

import pytest

from src.sf_schema import SCHEMA, OBJECT_NAMES, get_schema


class TestSchemaModule:
    """Tests for the sf_schema module directly."""

    def test_schema_has_expected_objects(self):
        expected = {
            "TVRS_Guest__c",
            "Account",
            "Contact",
            "Opportunity",
            "Lead",
            "Campaign",
            "CampaignMember",
            "Task",
        }
        assert expected == set(SCHEMA.keys())

    def test_object_names_sorted(self):
        assert OBJECT_NAMES == sorted(OBJECT_NAMES)

    def test_each_object_has_required_keys(self):
        for name, obj in SCHEMA.items():
            assert "label" in obj, f"{name} missing label"
            assert "description" in obj, f"{name} missing description"
            assert "key_fields" in obj, f"{name} missing key_fields"

    def test_get_schema_full(self):
        result = get_schema()
        assert result is SCHEMA

    def test_get_schema_single_object(self):
        result = get_schema("Account")
        assert "Account" in result
        assert len(result) == 1

    def test_get_schema_case_insensitive(self):
        result = get_schema("account")
        assert "Account" in result

    def test_get_schema_unknown_raises(self):
        with pytest.raises(KeyError, match="not found in curated schema"):
            get_schema("FakeObject__c")


class TestSfGetSchemaTool:
    """Tests for the sf_get_schema tool function registered on MCP."""

    @pytest.fixture
    def get_schema_tool(self):
        """Extract the sf_get_schema inner function from register_tools."""
        from unittest.mock import MagicMock

        mcp = MagicMock()
        captured = {}

        def capture_tool():
            def decorator(fn):
                captured[fn.__name__] = fn
                return fn

            return decorator

        mcp.tool = capture_tool
        from src.sf_tools import register_tools

        register_tools(mcp)
        return captured["sf_get_schema"]

    def test_returns_full_schema_when_empty(self, get_schema_tool):
        result = get_schema_tool(objects="")
        assert set(result.keys()) == set(SCHEMA.keys()) | {"_tips"}
        assert result["Account"] is SCHEMA["Account"]

    def test_returns_full_schema_when_whitespace(self, get_schema_tool):
        result = get_schema_tool(objects="   ")
        assert set(result.keys()) == set(SCHEMA.keys()) | {"_tips"}

    def test_returns_filtered_single(self, get_schema_tool):
        result = get_schema_tool(objects="Account")
        assert set(result.keys()) == {"Account", "_tips"}

    def test_returns_filtered_multiple(self, get_schema_tool):
        result = get_schema_tool(objects="Account,Contact,Opportunity")
        assert set(result.keys()) == {"Account", "Contact", "Opportunity", "_tips"}

    def test_handles_unknown_objects(self, get_schema_tool):
        result = get_schema_tool(objects="FakeObject__c")
        assert set(result.keys()) == {"_tips"}

    def test_mixed_known_and_unknown(self, get_schema_tool):
        result = get_schema_tool(objects="Account,FakeObject__c,TVRS_Guest__c")
        assert set(result.keys()) == {"Account", "TVRS_Guest__c", "_tips"}

    def test_tips_carry_soql_gotchas(self, get_schema_tool):
        result = get_schema_tool(objects="Account")
        tips = " ".join(result["_tips"])
        assert "LOWER" in tips
        assert "sf_search" in tips

    def test_case_insensitive_lookup(self, get_schema_tool):
        result = get_schema_tool(objects="account,tvrs_guest__c")
        assert "Account" in result
        assert "TVRS_Guest__c" in result

    def test_handles_whitespace_in_list(self, get_schema_tool):
        result = get_schema_tool(objects=" Account , Contact ")
        assert set(result.keys()) == {"Account", "Contact", "_tips"}
