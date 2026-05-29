"""Opportunity-creation composite — enforces an Account link server-side.

An Opportunity at The Vines must be tied to an Account. Record-triggered flows on
Opportunity insert ("Update Future Service", "Opp Sales Stages on Account -
Create") write back to the Account and throw unhandled faults — rolling the whole
insert back — when there is no Account. So this composite resolves the person to a
single Account first and refuses to create an orphan Opportunity, instead of
emitting one that Salesforce will reject.

It's a thin, correctness-focused write tool: validate the product, resolve/require
the Account, fill sensible defaults (stage, end-of-quarter close date), create.
Slack-specific concerns (confirmation card, Slack→SF user mapping, audit trail)
stay in the consumer (Sabueso); this just guarantees a valid, Account-linked Opp.
"""

from __future__ import annotations

import re
from datetime import date, timedelta

from src.auth import require_write_access
from src.sanitize import escape_soql, escape_soql_like, validate_sf_id
from src.sf_client import create_record
from src.sf_client import query as sf_query

# Offering types — the product is encoded in the Opportunity Name (mirrors the
# values The Vines uses on Current_Services__c / in won-opp names).
OPPORTUNITY_PRODUCTS = (
    "TVOM Vineyards",
    "TVOM Housing Lot",
    "TVOM Villa Expansion",
    "TVG Membership",
)
_DEFAULT_STAGE = "Deep Discovery"

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _end_of_quarter(today: date) -> date:
    q_end_month = ((today.month - 1) // 3) * 3 + 3  # 3, 6, 9, or 12
    if q_end_month == 12:
        return date(today.year, 12, 31)
    return date(today.year, q_end_month + 1, 1) - timedelta(days=1)


def _accounts_for(person: str) -> list[dict]:
    """Resolve a name/email to candidate Accounts, deduped by account id."""
    p = (person or "").strip()
    by_id: dict[str, dict] = {}

    def _add(acc_id, name, email):
        if not acc_id:
            return
        cur = by_id.setdefault(
            acc_id, {"account_id": acc_id, "account_name": name, "email": email}
        )
        cur["account_name"] = cur.get("account_name") or name
        cur["email"] = cur.get("email") or email

    def _run(soql):
        try:
            rows = sf_query(soql)
        except Exception:  # noqa: BLE001 — resolution failure → no candidates
            return []
        return [r for r in rows if isinstance(r, dict)]

    if _EMAIL_RE.match(p):
        e = escape_soql(p.lower())
        for r in _run(
            "SELECT Id, Name, PersonEmail FROM Account "
            f"WHERE IsPersonAccount = true AND PersonEmail = '{e}' LIMIT 10"
        ):
            _add(r.get("Id"), r.get("Name"), r.get("PersonEmail"))
        for r in _run(
            "SELECT Id, AccountId, Account.Name, Email FROM Contact "
            f"WHERE Email = '{e}' LIMIT 10"
        ):
            _add(r.get("AccountId"), (r.get("Account") or {}).get("Name"), r.get("Email"))
    else:
        like = escape_soql_like(p)
        for r in _run(
            "SELECT Id, Name, PersonEmail FROM Account "
            f"WHERE Name LIKE '%{like}%' LIMIT 25"
        ):
            _add(r.get("Id"), r.get("Name"), r.get("PersonEmail"))
        tokens = [t for t in re.sub(r"[^a-z0-9 ]", " ", p.lower()).split() if len(t) >= 2]
        anchor = max(tokens, key=len) if tokens else ""
        if anchor:
            a = escape_soql_like(anchor)
            for r in _run(
                "SELECT Id, AccountId, Account.Name, Email FROM Contact "
                f"WHERE FirstName LIKE '%{a}%' OR LastName LIKE '%{a}%' LIMIT 25"
            ):
                _add(
                    r.get("AccountId"),
                    (r.get("Account") or {}).get("Name"),
                    r.get("Email"),
                )
    return list(by_id.values())


def build_create_opportunity(
    person: str = "",
    product: str = "",
    *,
    account_id: str = "",
    close_date: str = "",
    stage: str = "",
    description: str = "",
    owner_id: str = "",
    lead_source: str = "",
) -> dict:
    """Resolve/require an Account, then create an Account-linked Opportunity."""
    if product not in OPPORTUNITY_PRODUCTS:
        return {"status": "invalid_product", "expected": list(OPPORTUNITY_PRODUCTS)}

    account_name = None
    if account_id:
        try:
            validate_sf_id(account_id)
        except ValueError as e:
            return {"status": "error", "error": str(e)}
        try:
            rows = sf_query(
                f"SELECT Id, Name FROM Account WHERE Id = '{escape_soql(account_id)}' LIMIT 1"
            )
        except Exception as e:  # noqa: BLE001
            return {"status": "error", "error": str(e)}
        if not rows or not isinstance(rows[0], dict):
            return {
                "status": "account_not_found",
                "message": f"No Account found with id {account_id!r}.",
            }
        account_name = rows[0].get("Name")
    else:
        if not (person or "").strip():
            return {
                "status": "needs_account",
                "message": (
                    "Provide a person (name or email) to resolve, or an account_id "
                    "— an Opportunity must be linked to an Account."
                ),
            }
        candidates = _accounts_for(person)
        if not candidates:
            return {
                "status": "needs_account",
                "message": (
                    f"No Salesforce Account found for {person!r}. An Opportunity "
                    "can't be created without one — create or convert their Account "
                    "first, then retry (or pass account_id explicitly)."
                ),
            }
        if len(candidates) > 1:
            return {
                "status": "ambiguous_account",
                "candidates": candidates[:8],
                "message": "Multiple accounts match; pass account_id to disambiguate.",
            }
        account_id = candidates[0]["account_id"]
        account_name = candidates[0].get("account_name")

    data = {
        "Name": f"{account_name or person or account_id} - {product}",
        "StageName": stage or _DEFAULT_STAGE,
        "CloseDate": close_date or _end_of_quarter(date.today()).isoformat(),
        "AccountId": account_id,
    }
    if description:
        data["Description"] = description
    if owner_id:
        data["OwnerId"] = owner_id
    if lead_source:
        data["LeadSource"] = lead_source

    try:
        result = create_record("Opportunity", data)
    except Exception as e:  # noqa: BLE001
        return {"status": "create_failed", "error": str(e), "payload": data}

    if isinstance(result, dict) and result.get("id") and result.get("success", True):
        return {
            "status": "ok",
            "opportunity_id": result["id"],
            "account_id": account_id,
            "account_name": account_name,
            "payload": data,
        }
    return {"status": "create_failed", "error": result, "payload": data}


def register_tools(mcp):
    """Register the create_opportunity composite on the given FastMCP instance."""

    @mcp.tool()
    def create_opportunity(
        person: str = "",
        product: str = "",
        account_id: str = "",
        close_date: str = "",
        stage: str = "",
        description: str = "",
        owner_id: str = "",
        lead_source: str = "",
    ) -> dict:
        """Create an Account-linked Salesforce Opportunity in one call.

        An Opportunity at The Vines MUST be tied to an Account — record-triggered
        flows on Opportunity insert write back to the Account and hard-fail
        (rolling back the create) when there's no Account. This tool resolves the
        person to a single Account first and refuses to create an orphan, so the
        create won't fail that way. Prefer it over sf_create_record for
        Opportunities.

        Provide either `person` (a name or email to resolve to an Account) or an
        explicit `account_id`. If the person matches no Account, returns
        status="needs_account" (create/convert their Account first); if several
        Accounts match, status="ambiguous_account" with candidates (pass
        account_id).

        Args:
            person: Name or email of the person/account to link the Opp to.
                Ignored if account_id is given.
            product: One of "TVOM Vineyards", "TVOM Housing Lot",
                "TVOM Villa Expansion", "TVG Membership". Encoded into the Opp name.
            account_id: Salesforce Account Id to link to directly (skips resolution).
            close_date: ISO date (YYYY-MM-DD). Defaults to the end of the current
                calendar quarter.
            stage: StageName. Defaults to "Deep Discovery".
            description: Optional Opportunity Description.
            owner_id: Optional Salesforce User Id to own the Opp (defaults to the
                API user).
            lead_source: Optional LeadSource value.

        Returns:
            status="ok" with opportunity_id, account_id, account_name, and the
            payload; or invalid_product / needs_account / ambiguous_account /
            account_not_found / create_failed with details.
        """
        try:
            require_write_access()
        except PermissionError as e:
            return {"error": str(e)}
        return build_create_opportunity(
            person,
            product,
            account_id=account_id,
            close_date=close_date,
            stage=stage,
            description=description,
            owner_id=owner_id,
            lead_source=lead_source,
        )
