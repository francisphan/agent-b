"""NetSuite MCP tool definitions."""

from src.ns_client import (
    rest_get,
    rest_list,
    suiteql_query,
    suiteql_query_page,
)
from src.schema_cache import schema_cache
from src.ns_schema import SCHEMA as NS_SCHEMA, GLOBAL_TIPS as NS_TIPS
from src.query_validator import validate_suiteql, enhance_ns_error


def register_tools(mcp):
    """Register all NetSuite tools on the given FastMCP instance."""

    @mcp.tool()
    def ns_suiteql_query(
        query: str, limit: int = 1000, offset: int = 0
    ) -> list[dict] | dict:
        """Execute a SuiteQL query against NetSuite and return matching records.

        Key tables: customer, transaction (filter by type: SalesOrd, CustInvc, etc.),
        transactionline, item, vendor, employee, contact.

        Column gotchas (these differ from the REST field names and 400 otherwise):
        the row id is 'id' (NOT 'internalid'); customer has no 'name' column (use
        entityid / companyname / firstname / lastname); customer balance is
        'balancesearch' / 'overduebalancesearch' (NOT 'balance').

        Row limiting: SuiteQL is Oracle SQL and does NOT support the MySQL-style
        'LIMIT n' clause — appending "LIMIT 50" 400s with a syntax error near 'LIMIT'.
        To cap rows, either write 'FETCH FIRST n ROWS ONLY' inside the SQL (e.g.
        "... ORDER BY t.trandate DESC FETCH FIRST 50 ROWS ONLY", optionally after
        'OFFSET n ROWS') or pass the 'limit' parameter below (which paginates
        server-side).

        Use ns_get_netsuite_schema to explore fields, tables, and example SuiteQL before querying.

        Pagination: By default, all pages are fetched and concatenated. To paginate manually,
        set offset > 0 to retrieve a single page starting at that row. The response will include
        'hasMore' and 'offset' so you can fetch subsequent pages.

        Args:
            query: A valid SuiteQL query string (e.g. "SELECT id, companyname FROM customer").
            limit: Max rows per page (default 1000).
            offset: Starting row offset (default 0). When 0 all pages are fetched automatically.
                    Set to any value > 0 to get a single page at that offset (useful for manual pagination).

        Returns:
            If offset is 0: a list of all result dicts (auto-paginated).
            If offset > 0: a dict with 'items', 'hasMore', 'offset', and 'totalResults'.
            On failure: a single-element list or dict with an error message.
        """
        # Pre-flight validation. Only findings the validator marks blocking (a
        # SQL LIMIT clause, which SuiteQL always rejects) short-circuit here so
        # the actionable message reaches the caller instead of a raw NetSuite
        # 400. Everything else (e.g. an unextractable FROM clause) stays
        # advisory and the query still executes — the validator is regex-based
        # and can be wrong about unusual-but-valid SQL.
        validation = validate_suiteql(query)
        if validation["blocking"]:
            msg = "; ".join(validation["warnings"])
            if validation["suggestions"]:
                msg += " Suggestion: " + " ".join(validation["suggestions"])
            return [{"error": msg}]

        try:
            if offset > 0:
                # Manual pagination — return a single page
                result = suiteql_query_page(query, limit=limit, offset=offset)
                if validation["warnings"]:
                    result["warnings"] = validation["warnings"]
                    result["suggestions"] = validation["suggestions"]
                return result

            # Default: fetch all pages
            records = suiteql_query(query, limit=limit)
            if validation["warnings"]:
                return {
                    "records": records,
                    "warnings": validation["warnings"],
                    "suggestions": validation["suggestions"],
                }
            return records
        except Exception as e:
            enhanced = enhance_ns_error(str(e), query)
            return [{"error": enhanced}]

    @mcp.tool()
    def ns_rest_get(
        record_type: str, record_id: str, expand_sub_resources: bool = False
    ) -> dict:
        """Get a single NetSuite record by its type and internal ID.

        Args:
            record_type: REST record type name (e.g. "customer", "salesOrder", "invoice").
            record_id: The internal ID of the record.
            expand_sub_resources: If True, include all sublists/subrecords in the response.

        Returns:
            The record as a dict, or an error dict on failure.
        """
        try:
            return rest_get(
                record_type, record_id, expand_sub_resources=expand_sub_resources
            )
        except Exception as e:
            return {"error": str(e)}

    @mcp.tool()
    def ns_rest_list(
        record_type: str,
        limit: int = 100,
        offset: int = 0,
        q: str | None = None,
    ) -> dict:
        """List NetSuite records of a given type with optional filtering.

        Args:
            record_type: REST record type name (e.g. "customer", "salesOrder").
            limit: Maximum number of records to return (default 100).
            offset: Starting offset for pagination (default 0).
            q: Optional query filter string.

        Returns:
            A dict with items, count, hasMore, and pagination info, or an error dict on failure.
        """
        try:
            return rest_list(record_type, limit=limit, offset=offset, q=q)
        except Exception as e:
            return {"error": str(e)}

    @mcp.tool()
    def ns_list_record_types() -> dict:
        """List all available NetSuite record types from the metadata catalog.

        Results are cached and refreshed automatically in the background.

        Returns:
            A dict of record type metadata, or an error dict on failure.
        """
        try:
            return schema_cache.ns_list_record_types()
        except Exception as e:
            return {"error": str(e)}

    @mcp.tool()
    def ns_get_record_schema(record_type: str) -> dict:
        """Get the field schema for a NetSuite record type from the REST metadata API.

        Results are cached and refreshed automatically in the background.

        For common record types (customer, salesOrder, invoice, item, transaction,
        vendor, employee, contact), prefer ns_get_netsuite_schema which returns
        curated fields with SuiteQL examples.

        Args:
            record_type: REST record type name (e.g. "customer", "salesOrder").

        Returns:
            A dict describing the record's fields and structure, or an error dict on failure.
        """
        try:
            return schema_cache.ns_get_record_schema(record_type)
        except Exception as e:
            return {"error": str(e)}

    @mcp.tool()
    def ns_get_netsuite_schema(record_types: str = "") -> dict:
        """Return curated schema for NetSuite record types including fields, SuiteQL tables, and example queries.

        Call this before writing SuiteQL queries to discover field names, table mappings,
        transaction type codes, and query patterns.

        Covers: customer, salesOrder, invoice, item, transaction, vendor, employee, contact.

        Args:
            record_types: Comma-separated record type names (e.g. "customer,salesOrder").
                          If empty, returns schema for all curated record types.

        Returns:
            A dict keyed by record type with field, table, and example query info,
            plus a "_tips" key with cross-table SuiteQL gotchas (LIMIT, id, balance).
            Unknown types are silently omitted.
        """
        if not record_types.strip():
            result = dict(NS_SCHEMA)
        else:
            result = {}
            for name in record_types.split(","):
                name = name.strip()
                if not name:
                    continue
                for schema_name, schema_data in NS_SCHEMA.items():
                    if schema_name.lower() == name.lower():
                        result[schema_name] = schema_data
                        break

        result["_tips"] = NS_TIPS
        return result
