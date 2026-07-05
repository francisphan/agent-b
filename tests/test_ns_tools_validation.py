"""ns_suiteql_query pre-flight validation: only blocking findings short-circuit."""

import asyncio
from unittest.mock import patch

from mcp.server.fastmcp import FastMCP

from src.ns_tools import register_tools


def _call(query: str, **kwargs):
    mcp = FastMCP("test")
    register_tools(mcp)
    return asyncio.run(mcp._tool_manager.call_tool("ns_suiteql_query", {"query": query, **kwargs}))


class TestSuiteqlPreflight:
    def test_limit_clause_short_circuits_before_netsuite(self):
        with patch("src.ns_tools.suiteql_query") as mock_query:
            result = _call("SELECT id FROM customer ORDER BY id LIMIT 50")
        mock_query.assert_not_called()
        # Failure shape matches the exception path: single-element error list.
        assert isinstance(result, list) and len(result) == 1
        assert "LIMIT" in result[0]["error"]
        assert "FETCH FIRST" in result[0]["error"]

    def test_advisory_findings_still_execute(self):
        # An unextractable FROM (quoted identifier) is advisory, not blocking:
        # the query must still reach NetSuite.
        with patch("src.ns_tools.suiteql_query", return_value=[{"id": "1"}]) as mock_query:
            result = _call('SELECT COUNT(*) FROM "customer"')
        mock_query.assert_called_once()
        # Records come back with the advisory warnings attached.
        assert result["records"] == [{"id": "1"}]
        assert result["warnings"]

    def test_limit_inside_string_literal_executes(self):
        with patch("src.ns_tools.suiteql_query", return_value=[]) as mock_query:
            _call("SELECT id FROM customer WHERE comments LIKE '%LIMIT 50%'")
        mock_query.assert_called_once()
