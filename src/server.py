"""Agent B — MCP server exposing Salesforce, NetSuite, and Pardot tools."""

import hmac
import json
import logging
import os
import time
from datetime import datetime, timezone
from typing import Optional

from dotenv import load_dotenv
from mcp.server.auth.settings import AuthSettings, ClientRegistrationOptions
from mcp.server.fastmcp import FastMCP
from pydantic import AnyHttpUrl
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

from src import usage_store
from src.auth import ALLOW_INSECURE_NO_AUTH, AUTH_LEVEL, CALLER, READ_TOKEN, WRITE_TOKEN
from src.oauth_callback import make_oauth_callback_route
from src.oauth_provider import GoogleOAuthProvider, READ_SCOPE
from src.tool_logging import configure_logging, instrument
from src.usage_report import weekly_report

load_dotenv()
configure_logging()
logger = logging.getLogger(__name__)


def _build_oauth_provider() -> Optional[GoogleOAuthProvider]:
    """Construct the OAuth provider iff Google credentials are configured.

    Falls back to None when any required env var is missing, which keeps the
    legacy BearerAuthMiddleware path intact for local dev and existing
    deployments.
    """
    client_id = os.getenv("OAUTH_GOOGLE_CLIENT_ID")
    client_secret = os.getenv("OAUTH_GOOGLE_CLIENT_SECRET")
    public_url = os.getenv("OAUTH_PUBLIC_URL")
    signing_key = os.getenv("OAUTH_SIGNING_KEY")
    if not (client_id and client_secret and public_url and signing_key):
        return None

    write_emails = {e.strip() for e in os.getenv("OAUTH_WRITE_EMAILS", "").split(",") if e.strip()}
    extra_allowed = {
        e.strip() for e in os.getenv("OAUTH_EXTRA_ALLOWED_EMAILS", "").split(",") if e.strip()
    }
    return GoogleOAuthProvider(
        google_client_id=client_id,
        google_client_secret=client_secret,
        public_url=public_url,
        signing_key=signing_key,
        allowed_domain=os.getenv("OAUTH_ALLOWED_DOMAIN") or None,
        extra_allowed_emails=extra_allowed,
        write_emails=write_emails,
        static_read_token=READ_TOKEN,
        static_write_token=WRITE_TOKEN,
    )


class BearerAuthMiddleware(BaseHTTPMiddleware):
    """Static bearer-token auth — used only when OAuth is not configured.

    When OAuth IS configured the SDK's own bearer middleware takes over, and
    ``GoogleOAuthProvider.load_access_token`` validates both OAuth-issued JWTs
    and the same static tokens — so server-to-server consumers keep working
    either way.

    - MCP_API_TOKEN   → read-only access
    - MCP_WRITE_TOKEN → read + write access
    - Neither set     → denied (503), unless MCP_ALLOW_INSECURE_NO_AUTH is set
                        to explicitly opt into a fully-open local-dev server.
    """

    async def dispatch(self, request, call_next):
        if request.url.path == "/health":
            return await call_next(request)

        if not READ_TOKEN and not WRITE_TOKEN:
            # Fail CLOSED on a misconfigured deploy. Full-open access is only
            # granted when the operator explicitly opts in for local dev.
            if ALLOW_INSECURE_NO_AUTH:
                AUTH_LEVEL.set("write")
                CALLER.set("anonymous")
                return await call_next(request)
            return JSONResponse(
                {"error": "Server authentication is not configured."}, status_code=503
            )

        bearer = request.headers.get("authorization", "").removeprefix("Bearer ").strip()

        if WRITE_TOKEN and hmac.compare_digest(bearer, WRITE_TOKEN):
            AUTH_LEVEL.set("write")
            CALLER.set("sabueso")
            return await call_next(request)

        if READ_TOKEN and hmac.compare_digest(bearer, READ_TOKEN):
            AUTH_LEVEL.set("read")
            CALLER.set("s2s-read")
            return await call_next(request)

        return JSONResponse({"error": "Unauthorized"}, status_code=401)


_oauth_provider = _build_oauth_provider()

_fastmcp_kwargs: dict = {
    "host": os.getenv("MCP_HOST", "0.0.0.0"),
    "port": int(os.getenv("MCP_PORT", "8000")),
}
if _oauth_provider is not None:
    _issuer = AnyHttpUrl(_oauth_provider.public_url)
    _fastmcp_kwargs["auth_server_provider"] = _oauth_provider
    _fastmcp_kwargs["auth"] = AuthSettings(
        issuer_url=_issuer,
        resource_server_url=_issuer,
        required_scopes=[READ_SCOPE],
        client_registration_options=ClientRegistrationOptions(
            enabled=True,
            valid_scopes=[READ_SCOPE],
            default_scopes=[READ_SCOPE],
        ),
    )

mcp = FastMCP("Agent B", **_fastmcp_kwargs)


@mcp.custom_route("/health", methods=["GET"])
async def health(request):
    return JSONResponse({"status": "ok"})


def _usage_report_authorized(request) -> bool:
    """Bearer check for /usage/report — accepts either static token.

    Custom Starlette routes are NOT behind the SDK's auth middleware (that
    guards only the MCP endpoint), and BearerAuthMiddleware is only mounted in
    the no-OAuth path — so this route can't rely on either and checks the bearer
    itself, accepting MCP_API_TOKEN *or* MCP_WRITE_TOKEN. When neither is
    configured (local dev) the endpoint is open, matching BearerAuthMiddleware's
    posture. Reads the module-level tokens so tests can monkeypatch them.
    """
    if not READ_TOKEN and not WRITE_TOKEN:
        return True
    bearer = request.headers.get("authorization", "").removeprefix("Bearer ").strip()
    if WRITE_TOKEN and hmac.compare_digest(bearer, WRITE_TOKEN):
        return True
    if READ_TOKEN and hmac.compare_digest(bearer, READ_TOKEN):
        return True
    return False


@mcp.custom_route("/usage/report", methods=["GET"])
async def usage_report_route(request):
    """Serve the weekly tool-usage report as JSON (bearer-protected).

    Consumed by the usage-report GitHub Actions cron. ``days`` (default 7,
    clamped to 1..30) sets the window; the immediately preceding window of the
    same length is returned as ``prev_totals`` for week-over-week context. Reads
    the Redis usage mirror — on a store outage returns 503 rather than a
    misleadingly-empty report.
    """
    if not _usage_report_authorized(request):
        return JSONResponse({"error": "Unauthorized"}, status_code=401)

    try:
        days = int(request.query_params.get("days", "7"))
    except (TypeError, ValueError):
        days = 7
    days = max(1, min(30, days))

    now = time.time()
    window = days * 86400
    since_ts = now - window
    prev_since_ts = since_ts - window

    try:
        # One read covering both windows; split locally to avoid a second LRANGE.
        everything = usage_store.fetch(prev_since_ts)
    except Exception as exc:
        return JSONResponse({"error": f"usage store unavailable: {exc}"}, status_code=503)

    records = [
        r for r in everything if isinstance(r.get("ts"), (int, float)) and r["ts"] >= since_ts
    ]
    prev_records = [
        r for r in everything if isinstance(r.get("ts"), (int, float)) and r["ts"] < since_ts
    ]

    report = weekly_report(records, prev_records)
    report["days"] = days
    report["generated_at"] = datetime.now(timezone.utc).isoformat()
    report["since"] = datetime.fromtimestamp(since_ts, tz=timezone.utc).isoformat()
    report["until"] = datetime.fromtimestamp(now, tz=timezone.utc).isoformat()
    return JSONResponse(report)


if _oauth_provider is not None:
    mcp.custom_route("/oauth/callback", methods=["GET"])(
        make_oauth_callback_route(_oauth_provider)
    )


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------
from src.sf_tools import register_tools as register_sf_tools  # noqa: E402
from src.ns_tools import register_tools as register_ns_tools  # noqa: E402
from src.opera_tools import register_tools as register_opera_tools  # noqa: E402
from src.cross_tools import register_tools as register_cross_tools  # noqa: E402
from src.wine_tools import register_tools as register_wine_tools  # noqa: E402
from src.person_tools import register_tools as register_person_tools  # noqa: E402

register_sf_tools(mcp)
register_ns_tools(mcp)
register_cross_tools(mcp)
register_wine_tools(mcp)
register_person_tools(mcp)

# OPERA: tools are gated behind OPERA_TOOLS_ENABLED and OFF by default. OPERA is
# reached via the opera-pms-api HTTP service (see src/opera_client.py). The
# guest_360_profile composite calls opera_client directly and wraps it in
# try/except, so it degrades gracefully when OPERA is unavailable or the API is
# unconfigured. Set OPERA_TOOLS_ENABLED=true once OPERA_API_BASE_URL/TOKEN are set.
# opera_client.opera_enabled() is the single definition of the flag — query()
# also checks it per-call, covering the composites that bypass registration.
from src.opera_client import opera_enabled  # noqa: E402

if opera_enabled():
    register_opera_tools(mcp)
    logger.info("OPERA MCP tools: enabled")
else:
    logger.info("OPERA MCP tools: disabled (set OPERA_TOOLS_ENABLED=true to enable)")

# Write tools — gated behind MCP_WRITE_TOKEN (each tool calls require_write_access)
from src.sf_write_tools import register_tools as register_sf_write_tools  # noqa: E402
from src.ns_write_tools import register_tools as register_ns_write_tools  # noqa: E402
from src.opportunity_tools import register_tools as register_opportunity_tools  # noqa: E402

register_sf_write_tools(mcp)
register_ns_write_tools(mcp)
register_opportunity_tools(mcp)

# Pardot: pardot_enabled() (default ON) is the single on/off gate, shared with
# pardot_client so the cross-system composites short-circuit at the client when
# Pardot is off (guest_360_profile / lookup_guest_by_email hit Pardot via
# cross_tools' query_prospects leg; person_brief / wine_owner_lookup reach it
# transitively through guest_360). When enabled, pardot_full_surface() decides
# breadth: an explicit truthy PARDOT_TOOLS_ENABLED lights up the full read+write
# surface, while unset keeps only the curated read subset (the lean default the
# Sabueso bot uses — the rest was built for pv-campaign-2026 and stays parked in
# code). Set PARDOT_TOOLS_ENABLED=false to switch Pardot off entirely: registers
# ZERO Pardot tools AND no-ops the composites' Pardot leg.
from src.pardot_client import pardot_enabled, pardot_full_surface  # noqa: E402
from src.pardot_tools import (  # noqa: E402
    CURATED_READ_TOOLS as PARDOT_CURATED_READ_TOOLS,
    register_tools as register_pardot_tools,
)

if not pardot_enabled():
    logger.info("Pardot MCP tools: disabled (PARDOT_TOOLS_ENABLED is off)")
elif pardot_full_surface():
    from src.pardot_write_tools import (  # noqa: E402
        register_tools as register_pardot_write_tools,
    )

    register_pardot_tools(mcp)
    register_pardot_write_tools(mcp)
    logger.info("Pardot MCP tools: full surface enabled (all reads + writes)")
else:
    register_pardot_tools(mcp, include=PARDOT_CURATED_READ_TOOLS)
    logger.info(
        "Pardot MCP tools: curated read subset only (%d tools); "
        "set PARDOT_TOOLS_ENABLED=true for the full surface",
        len(PARDOT_CURATED_READ_TOOLS),
    )

# Wrap the tool dispatcher so every call is logged (redacted) for usage analysis.
instrument(mcp)


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
from src.ns_schema import SCHEMA as NS_SCHEMA, GLOBAL_TIPS as NS_TIPS  # noqa: E402
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
    return json.dumps(
        {
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
                # Cross-table gotchas (LIMIT/FETCH FIRST, id, balancesearch, T/F, dates)
                # come from the single source in ns_schema.GLOBAL_TIPS, like OPERA_TIPS below.
                *NS_TIPS,
                "Line items: JOIN transactionline tl ON t.id = tl.transaction",
            ],
            "cross_system": [
                "Use lookup_guest_by_email for quick cross-system existence checks",
                "Use guest_360_profile for a unified view with stay history + financials + marketing",
                "Email is the primary cross-system key (SF Email__c, NS email, Pardot email)",
                "guest_360_profile pulls OPERA stay history when an OPERA NAME match is found by email",
            ],
            "opera_sql_tips": OPERA_TIPS,
        },
        indent=2,
    )


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
