"""Agent B — MCP server exposing Salesforce, NetSuite, and Pardot tools."""

import hmac
import json
import os

from dotenv import load_dotenv
from mcp.server.fastmcp import FastMCP
from starlette.middleware import Middleware
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

from src.auth import AUTH_LEVEL, READ_TOKEN, WRITE_TOKEN

load_dotenv()


class BearerAuthMiddleware(BaseHTTPMiddleware):
    """Authenticate requests using bearer tokens.

    - MCP_API_TOKEN  → read-only access (existing behaviour)
    - MCP_WRITE_TOKEN → read + write access

    If neither env var is set, all requests are allowed with write access
    (for local development).
    """

    async def dispatch(self, request, call_next):
        # Skip auth for health check
        if request.url.path == "/health":
            return await call_next(request)

        # If no tokens configured, allow everything (local dev)
        if not READ_TOKEN and not WRITE_TOKEN:
            AUTH_LEVEL.set("write")
            return await call_next(request)

        bearer = request.headers.get("authorization", "").removeprefix("Bearer ").strip()

        # Check write token first (it grants superset of read access)
        if WRITE_TOKEN and hmac.compare_digest(bearer, WRITE_TOKEN):
            AUTH_LEVEL.set("write")
            return await call_next(request)

        # Check read token
        if READ_TOKEN and hmac.compare_digest(bearer, READ_TOKEN):
            AUTH_LEVEL.set("read")
            return await call_next(request)

        return JSONResponse({"error": "Unauthorized"}, status_code=401)


mcp = FastMCP(
    "Agent B",
    host=os.getenv("MCP_HOST", "0.0.0.0"),
    port=int(os.getenv("MCP_PORT", "8000")),
)


@mcp.custom_route("/health", methods=["GET"])
async def health(request):
    return JSONResponse({"status": "ok"})


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------
from src.sf_tools import register_tools as register_sf_tools  # noqa: E402
from src.ns_tools import register_tools as register_ns_tools  # noqa: E402
from src.pardot_tools import register_tools as register_pardot_tools  # noqa: E402
from src.opera_tools import register_tools as register_opera_tools  # noqa: E402
from src.cross_tools import register_tools as register_cross_tools  # noqa: E402

register_sf_tools(mcp)
register_ns_tools(mcp)
register_pardot_tools(mcp)
register_opera_tools(mcp)
register_cross_tools(mcp)

# Write tools — gated behind MCP_WRITE_TOKEN
from src.sf_write_tools import register_tools as register_sf_write_tools  # noqa: E402
from src.ns_write_tools import register_tools as register_ns_write_tools  # noqa: E402
from src.pardot_write_tools import register_tools as register_pardot_write_tools  # noqa: E402

register_sf_write_tools(mcp)
register_ns_write_tools(mcp)
register_pardot_write_tools(mcp)


# ---------------------------------------------------------------------------
# Schema cache management
# ---------------------------------------------------------------------------
from src.schema_cache import schema_cache  # noqa: E402


@mcp.tool()
def schema_cache_stats() -> dict:
    """Return cache statistics for the live API schema cache.

    Shows TTL, refresh interval, and per-entry age/staleness — useful for
    diagnosing whether cached data is current.
    """
    return schema_cache.stats()


@mcp.tool()
def schema_cache_invalidate(key: str = "") -> dict:
    """Force-invalidate the schema cache so the next read fetches fresh data.

    Args:
        key: Specific cache key to invalidate (from schema_cache_stats).
             If empty, invalidates the entire cache.

    Returns:
        Confirmation dict.
    """
    if key.strip():
        schema_cache.invalidate(key.strip())
        return {"invalidated": key.strip()}
    schema_cache.invalidate()
    return {"invalidated": "all"}


# ---------------------------------------------------------------------------
# Resources — browsable context for AI clients
# ---------------------------------------------------------------------------
from src.sf_schema import SCHEMA as SF_SCHEMA  # noqa: E402
from src.ns_schema import SCHEMA as NS_SCHEMA  # noqa: E402
from src.opera_schema import SCHEMA as OPERA_SCHEMA, GLOBAL_TIPS as OPERA_TIPS  # noqa: E402


@mcp.resource("schema://salesforce")
def salesforce_schema_resource() -> str:
    """Complete curated Salesforce schema with fields, relationships, and example SOQL.

    Covers: TVRS_Guest__c, Account, Contact, Opportunity, Lead, Campaign, CampaignMember, Task.
    """
    return json.dumps(SF_SCHEMA, indent=2)


@mcp.resource("schema://netsuite")
def netsuite_schema_resource() -> str:
    """Complete curated NetSuite schema with fields, SuiteQL tables, and example queries.

    Covers: customer, salesOrder, invoice, item, transaction, vendor, employee, contact.
    """
    return json.dumps(NS_SCHEMA, indent=2)


@mcp.resource("schema://salesforce/{object_name}")
def salesforce_object_resource(object_name: str) -> str:
    """Schema for a specific Salesforce object (e.g. schema://salesforce/TVRS_Guest__c)."""
    for name, schema in SF_SCHEMA.items():
        if name.lower() == object_name.lower():
            return json.dumps({name: schema}, indent=2)
    return json.dumps({"error": f"Object '{object_name}' not found in curated schema."})


@mcp.resource("schema://netsuite/{record_type}")
def netsuite_record_resource(record_type: str) -> str:
    """Schema for a specific NetSuite record type (e.g. schema://netsuite/customer)."""
    for name, schema in NS_SCHEMA.items():
        if name.lower() == record_type.lower():
            return json.dumps({name: schema}, indent=2)
    return json.dumps({"error": f"Record type '{record_type}' not found in curated schema."})


@mcp.resource("schema://opera")
def opera_schema_resource() -> str:
    """Curated OPERA PMS Oracle schema — tables, fields, filters, and example SQL.

    Covers: NAME, NAME_PHONE, NAME_ADDRESS, RESERVATION_NAME, RESERVATION_DAILY_ELEMENT_NAME,
    RESERVATION_DAILY_ELEMENTS, NAME_COMMENT, NAME$NOTES, RESERVATION_COMMENT, RESERVATION_ALERTS.
    """
    return json.dumps({**OPERA_SCHEMA, "_tips": OPERA_TIPS}, indent=2)


@mcp.resource("schema://opera/{table}")
def opera_table_resource(table: str) -> str:
    """Schema for a specific OPERA table (e.g. schema://opera/RESERVATION_NAME)."""
    for name, schema in OPERA_SCHEMA.items():
        if name.lower() == table.lower():
            return json.dumps({name: schema}, indent=2)
    return json.dumps({"error": f"Table '{table}' not found in curated OPERA schema."})


@mcp.resource("guide://query-patterns")
def query_patterns_resource() -> str:
    """Common query patterns and tips for Salesforce SOQL and NetSuite SuiteQL."""
    return json.dumps({
        "soql_tips": [
            "Use sf_get_schema before writing SOQL to discover field names",
            "TVRS_Guest__c.Contact__r.AccountId traverses Guest -> Contact -> Account",
            "Person Accounts: query Account with IsPersonAccount = true, use PersonEmail",
            "Aggregate queries: SELECT StageName, COUNT(Id) FROM Opportunity GROUP BY StageName",
            "Date literals: TODAY, LAST_N_DAYS:30, THIS_YEAR, NEXT_WEEK",
            "Subqueries: SELECT Id, (SELECT Id FROM Contacts) FROM Account",
        ],
        "suiteql_tips": [
            "Use ns_get_netsuite_schema before writing SuiteQL to discover table/field names",
            "All transactions live in 'transaction' table — filter by type column",
            "Transaction type codes: SalesOrd, CustInvc, CustPymt, VendBill, PurchOrd, Journal",
            "Line items: JOIN transactionline tl ON t.id = tl.transaction",
            "Customer booleans: use 'T'/'F' strings, not true/false",
            "Date functions: TO_DATE('2025-01-01', 'YYYY-MM-DD'), SYSDATE",
            "Case-sensitive string compare — use LOWER() for case-insensitive matching",
        ],
        "cross_system": [
            "Use lookup_guest_by_email for quick cross-system existence checks",
            "Use guest_360_profile for a unified view with stay history + financials + marketing",
            "Email is the primary cross-system key (SF Email__c, NS email, Pardot email)",
            "guest_360_profile pulls OPERA stay history when an OPERA NAME match is found by email",
        ],
        "opera_sql_tips": OPERA_TIPS,
    }, indent=2)


# ---------------------------------------------------------------------------
# Prompts — pre-built templates for common operations
# ---------------------------------------------------------------------------

@mcp.prompt()
def guest_arrivals() -> str:
    """Find all guests arriving this week with their details."""
    return (
        "Find all guests arriving this week at The Vines. "
        "First call sf_get_schema(objects='TVRS_Guest__c') to see the fields, "
        "then query: SELECT Id, Guest_First_Name__c, Guest_Last_Name__c, Email__c, "
        "Check_In_Date__c, Check_Out_Date__c, Villa_number__c, City__c, Country__c, "
        "Language__c, Comments__c FROM TVRS_Guest__c "
        "WHERE Check_In_Date__c = THIS_WEEK ORDER BY Check_In_Date__c ASC"
    )


@mcp.prompt()
def sales_pipeline() -> str:
    """Show current open opportunities grouped by stage."""
    return (
        "Show the current sales pipeline. Query open opportunities grouped by stage: "
        "SELECT StageName, COUNT(Id) cnt, SUM(Amount) total "
        "FROM Opportunity WHERE IsClosed = false "
        "GROUP BY StageName ORDER BY SUM(Amount) DESC. "
        "Then also get the top 10 open opportunities by amount: "
        "SELECT Id, Name, StageName, Amount, CloseDate, Owner.Name, Account.Name "
        "FROM Opportunity WHERE IsClosed = false ORDER BY Amount DESC LIMIT 10"
    )


@mcp.prompt()
def guest_lookup(email: str) -> str:
    """Look up a guest across all systems by email."""
    return (
        f"Look up the guest with email '{email}' across all systems. "
        f"Use the guest_360_profile tool to get their unified profile including "
        f"stay history, financial data, and marketing engagement."
    )


@mcp.prompt()
def netsuite_revenue() -> str:
    """Summarize recent NetSuite revenue by transaction type."""
    return (
        "Summarize recent NetSuite revenue. First call ns_get_netsuite_schema(record_types='transaction') "
        "to understand the table structure, then query: "
        "SELECT type, COUNT(*) AS cnt, SUM(foreigntotal) AS total "
        "FROM transaction "
        "WHERE trandate >= TO_DATE('2025-01-01', 'YYYY-MM-DD') "
        "GROUP BY type ORDER BY total DESC"
    )


@mcp.prompt()
def stale_opportunities() -> str:
    """Find stale opportunities that haven't been updated recently."""
    return (
        "Find stale opportunities — open deals that haven't been modified in 30+ days. "
        "Query: SELECT Id, Name, StageName, Amount, CloseDate, LastModifiedDate, "
        "Owner.Name, Account.Name FROM Opportunity "
        "WHERE IsClosed = false AND LastModifiedDate < LAST_N_DAYS:30 "
        "ORDER BY LastModifiedDate ASC LIMIT 20"
    )


@mcp.prompt()
def opera_arrivals_today() -> str:
    """Show OPERA arrivals at The Vines for today (Argentina time)."""
    return (
        "Show all guests checking in at The Vines today. "
        "Call opera_list_arrivals with today's date in YYYY-MM-DD (America/Argentina/Buenos_Aires). "
        "Group companion rows (parent_resv_name_id) under their primary booker and "
        "summarize by villa with ETA."
    )


@mcp.prompt()
def opera_guest_stay_history(email: str) -> str:
    """Pull OPERA stay history for a guest by email."""
    return (
        f"Look up the OPERA stay history for '{email}'. "
        "1) Call opera_search_profiles(email=...) to get NAME_ID. "
        "2) For each match, call opera_get_stay_history(name_id=...) and "
        "opera_get_profile_notes(name_id=...). "
        "Summarize: total stays, last stay, favorite villa, any allergies/preferences "
        "found in the profile notes."
    )


@mcp.prompt()
def outstanding_invoices() -> str:
    """Find unpaid NetSuite invoices."""
    return (
        "Find outstanding (unpaid) NetSuite invoices. "
        "First check ns_get_netsuite_schema(record_types='invoice') for field names, then query: "
        "SELECT t.id, t.tranid, t.trandate, t.duedate, t.status, "
        "t.foreigntotal, t.foreignamountremaining, c.companyname, c.email "
        "FROM transaction t JOIN customer c ON t.entity = c.id "
        "WHERE t.type = 'CustInvc' AND t.foreignamountremaining > 0 "
        "ORDER BY t.duedate ASC"
    )


if __name__ == "__main__":
    transport = os.getenv("MCP_TRANSPORT", "streamable-http")
    mcp.run(transport=transport)
