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
    def test_trailing_limit_rewritten_and_executes(self):
        # A trailing MySQL-style LIMIT has unambiguous intent — translate it to
        # Oracle syntax and run the query, teaching the fix via a warning.
        with patch("src.ns_tools.suiteql_query", return_value=[{"id": "1"}]) as mock_query:
            result = _call("SELECT id FROM customer ORDER BY id LIMIT 50")
        mock_query.assert_called_once()
        sent_query = mock_query.call_args[0][0]
        assert sent_query.endswith("FETCH FIRST 50 ROWS ONLY")
        assert "LIMIT" not in sent_query
        assert result["records"] == [{"id": "1"}]
        assert any("Rewrote" in w for w in result["warnings"])

    def test_mid_query_limit_short_circuits_before_netsuite(self):
        # A LIMIT that is NOT at the end (e.g. in a subquery) can't be safely
        # rewritten — it still short-circuits with the explanatory error.
        with patch("src.ns_tools.suiteql_query") as mock_query:
            result = _call(
                "SELECT id FROM customer WHERE id IN "
                "(SELECT entity FROM transaction LIMIT 5) ORDER BY id"
            )
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
