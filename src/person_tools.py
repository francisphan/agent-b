"""person_brief — resolve a person/owner by NAME (or email) and return one profile.

Most Slack asks to the bot are "tell me about <name>" or "what is <attribute> for
<name>" (e.g. "who is the CS rep for Barhorst", "what city is home for the
Monaghans", "when did Horacio Lopez purchase"). The existing composites
(guest_360_profile, lookup_guest_by_email) are email-keyed, so the agent had to
chain a name search → get → SuiteQL itself. This tool moves that fan-out
server-side: it resolves a free-text name across Salesforce + NetSuite, then
assembles identity, owner status, land-purchase, opportunities and (optionally)
the full cross-system guest_360 — in a single MCP round-trip.

The reliable cross-system join is email; NetSuite's stored Salesforce id is
often stale, so we match owners on email and fall back to name.

Co-ownership (co-member) and parcel-level vineyard detail (varietal, planted
year, acreage-by-varietal) live in NetSuite custom records that aren't field-
mapped yet (customrecord_ce_ownershipshare / customrecord_ce_parcel) — those are
the domain of a dedicated vineyard_lookup tool, not this one.
"""

from __future__ import annotations

import re

from src.sanitize import escape_soql, escape_soql_like, escape_suiteql, escape_suiteql_like
from src.sf_client import query as sf_query, record_url
from src.ns_client import suiteql_query
from src.cross_tools import guest_360

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

# Dropped from name queries — connectors/titles that carry no identity signal
# (handles "Emily and Art Monaghan", "Schreiner y sra.").
_NAME_STOPWORDS = frozenset(
    {"and", "y", "e", "the", "mr", "mrs", "ms", "dr", "sr", "sra", "sres", "de", "la"}
)

# NetSuite owner (customer) columns — shared by the name-search and by-email paths.
_NS_OWNER_COLS = (
    "id, entityid, companyname, firstname, lastname, email, phone, isinactive, "
    "custentity_vom_ownercode AS owner_code, "
    "custentity_vom_winebrandname AS brand, "
    "custentity37 AS vineyard, "
    "custentity_vom_numberoflots AS lots, "
    "BUILTIN.DF(custentity_vom_csam) AS cs_rep, "
    "BUILTIN.DF(custentityvom_customer_registration_stat) AS registration_status, "
    "BUILTIN.DF(custentity_vom_happinesslevel) AS happiness, "
    "custentity_secondaryemail AS secondary_email, "
    "custentity_vom_nickname AS nickname"
)


def _looks_like_email(value: str) -> bool:
    return bool(_EMAIL_RE.match(value.strip()))


def _norm(value: str) -> str:
    return re.sub(r"[^a-z0-9 ]", " ", (value or "").lower())


def _tokens(value: str) -> list[str]:
    return [t for t in _norm(value).split() if len(t) >= 2 and t not in _NAME_STOPWORDS]


def _anchor(tokens: list[str]) -> str | None:
    """Most distinctive token to search on — longest tends to be the surname."""
    return max(tokens, key=len) if tokens else None


def _name_score(query_tokens: list[str], *fields: str) -> float:
    """Fraction of query name-tokens present in the candidate's name fields."""
    if not query_tokens:
        return 0.0
    hay = set(_norm(" ".join(f for f in fields if f)).split())
    return sum(1 for t in query_tokens if t in hay) / len(query_tokens)


def _is_do_not_use(*names: str) -> bool:
    blob = " ".join(n for n in names if n).upper()
    return "NO USAR" in blob or "DO NOT USE" in blob


def _ns_is_owner(row: dict) -> bool:
    lots = row.get("lots")
    return bool(
        row.get("owner_code")
        or row.get("brand")
        or row.get("registration_status")
        or (lots not in (None, "", "0", 0))
    )


def _ns_owner_summary(row: dict) -> dict:
    return {
        "is_owner": _ns_is_owner(row),
        "ns_customer_id": row.get("id"),
        "name": row.get("companyname") or row.get("entityid"),
        "nickname": row.get("nickname"),
        "owner_code": row.get("owner_code"),
        "brand": row.get("brand"),
        "vineyard": row.get("vineyard"),
        "number_of_lots": row.get("lots"),
        "cs_rep": row.get("cs_rep"),
        "registration_status": row.get("registration_status"),
        "happiness": row.get("happiness"),
        "email": row.get("email"),
        "secondary_email": row.get("secondary_email"),
        "phone": row.get("phone"),
        "is_inactive": row.get("isinactive") == "T",
        "do_not_use": _is_do_not_use(row.get("entityid"), row.get("companyname")),
    }


def _ns_customers_by_name(anchor: str) -> list[dict]:
    a = escape_suiteql_like(anchor.lower())
    try:
        rows = suiteql_query(
            f"SELECT {_NS_OWNER_COLS} FROM customer "
            f"WHERE LOWER(entityid) LIKE '%{a}%' ESCAPE '\\' "
            f"OR LOWER(companyname) LIKE '%{a}%' ESCAPE '\\' "
            f"OR LOWER(firstname) LIKE '%{a}%' ESCAPE '\\' "
            f"OR LOWER(lastname) LIKE '%{a}%' ESCAPE '\\' "
            f"FETCH FIRST 50 ROWS ONLY"
        )
    except Exception as e:  # noqa: BLE001 — surface as no-match, not a 500
        return [{"error": str(e)}]
    return [r for r in rows if isinstance(r, dict)]


def _ns_owner_by_email(email: str) -> dict | None:
    e = escape_suiteql(email.lower())
    try:
        rows = suiteql_query(f"SELECT {_NS_OWNER_COLS} FROM customer WHERE LOWER(email) = '{e}'")
    except Exception:  # noqa: BLE001
        return None
    rows = [r for r in rows if isinstance(r, dict) and "error" not in r]
    if not rows:
        return None
    # Prefer an active, non-"NO USAR" record when duplicates exist.
    rows.sort(
        key=lambda r: (
            r.get("isinactive") == "T",
            _is_do_not_use(r.get("entityid"), r.get("companyname")),
        )
    )
    return rows[0]


def _sf_contacts_by_name(anchor: str) -> list[dict]:
    a = escape_soql_like(anchor)
    try:
        return sf_query(
            "SELECT Id, FirstName, LastName, Email, AccountId, Account.Name "
            f"FROM Contact WHERE FirstName LIKE '%{a}%' OR LastName LIKE '%{a}%' "
            "LIMIT 50"
        )
    except Exception:  # noqa: BLE001
        return []


def _sf_identity_by_email(email: str) -> dict:
    e = escape_soql(email.lower())
    try:
        rows = sf_query(
            "SELECT Guest_First_Name__c, Guest_Last_Name__c, Email__c, City__c, "
            "State_Province__c, Country__c, Language__c FROM TVRS_Guest__c "
            f"WHERE Email__c = '{e}' ORDER BY Check_In_Date__c DESC LIMIT 1"
        )
    except Exception:  # noqa: BLE001
        return {}
    if not rows or not isinstance(rows[0], dict):
        return {}
    r = rows[0]
    return {
        "first_name": r.get("Guest_First_Name__c"),
        "last_name": r.get("Guest_Last_Name__c"),
        "city": r.get("City__c"),
        "state_province": r.get("State_Province__c"),
        "country": r.get("Country__c"),
        "language": r.get("Language__c"),
    }


def _tvom_purchases(account_id: str) -> list[dict]:
    aid = escape_soql(account_id)
    try:
        rows = sf_query(
            "SELECT Purchase_Year__c, Lot__c, Acres__c, Price_Per_Acre__c, "
            "Total_Purchase_Price__c, Main_POC__c FROM TVOM_Sales__c "
            f"WHERE Account_Name__c = '{aid}' ORDER BY Purchase_Year__c"
        )
    except Exception:  # noqa: BLE001
        return []
    out = []
    for r in rows:
        if not isinstance(r, dict):
            continue
        out.append(
            {
                "purchase_year": r.get("Purchase_Year__c"),
                "lot": r.get("Lot__c"),
                "acres": r.get("Acres__c"),
                "price_per_acre": r.get("Price_Per_Acre__c"),
                "total_purchase_price": r.get("Total_Purchase_Price__c"),
                "main_poc": r.get("Main_POC__c"),
            }
        )
    return out


def _account_opportunities(account_id: str) -> list[dict]:
    aid = escape_soql(account_id)
    try:
        rows = sf_query(
            "SELECT Id, Name, Type, StageName, IsWon, IsClosed, Amount, CloseDate, "
            f"Owner.Name FROM Opportunity WHERE AccountId = '{aid}' "
            "ORDER BY CloseDate DESC LIMIT 50"
        )
    except Exception:  # noqa: BLE001
        return []
    return [r for r in rows if isinstance(r, dict)]


def _classify_offering(name: str | None, otype: str | None) -> str:
    """Map an opportunity to a Vines offering by its name + Type picklist.

    Opportunity.Name carries the product as free text (with typos/variants like
    "TVOM Vineyard" / "TVOM Private Vineyards" / "TVOM Housing Lot"); Type adds a
    clean "Villa"/"PV" signal. Villa/housing is checked first so a "Housing Lot"
    isn't miscounted as a vineyard.
    """
    t = f"{name or ''} {otype or ''}".lower()
    if "villa" in t or "housing" in t:
        return "villa"
    if "membership" in t or "tvg" in t:
        return "membership"
    if "vineyard" in t or (otype or "").strip().lower() == "pv":
        return "vineyard"
    return "other"


def _slim_opp(o: dict) -> dict:
    return {
        "id": o.get("Id"),
        "name": o.get("Name"),
        "type": o.get("Type"),
        "stage": o.get("StageName"),
        "is_won": o.get("IsWon"),
        "amount": o.get("Amount"),
        "close_date": o.get("CloseDate"),
        "owner": (o.get("Owner") or {}).get("Name"),
        "url": record_url("Opportunity", o.get("Id")),
    }


def _account_services(account_id: str) -> dict:
    """Account-level services picklists — the authoritative ownership signal.

    Current_Services__c is a semicolon-delimited multi-select (e.g.
    "TVOM Vineyards;TVG Membership;TVOM Villa Expansion"). The Vines maintains it
    as the source of truth for what someone currently owns/uses, so it's a better
    ownership measure than inferring from won opportunities.
    """
    aid = escape_soql(account_id)
    try:
        rows = sf_query(
            "SELECT Current_Services__c, Past_Services__c, Future_Services__c, "
            f"of_Owned_Lots__pc FROM Account WHERE Id = '{aid}' LIMIT 1"
        )
    except Exception:  # noqa: BLE001
        return {}
    if not rows or not isinstance(rows[0], dict):
        return {}
    r = rows[0]

    def _split(v):
        return [s.strip() for s in (v or "").split(";") if s.strip()]

    return {
        "current": _split(r.get("Current_Services__c")),
        "past": _split(r.get("Past_Services__c")),
        "future": _split(r.get("Future_Services__c")),
        "owned_lots": r.get("of_Owned_Lots__pc"),
    }


def _services_match(services: list[str], *keywords: str) -> list[str]:
    return [s for s in services if any(k in s.lower() for k in keywords)]


def _slim_candidate(c: dict) -> dict:
    owner = c.get("owner") or {}
    return {
        "name": c.get("name"),
        "email": c.get("email"),
        "account_name": c.get("account_name"),
        "is_owner": owner.get("is_owner", False),
        "cs_rep": owner.get("cs_rep"),
        "sf_account_id": c.get("sf_account_id"),
        "ns_customer_id": owner.get("ns_customer_id"),
        "score": round(c.get("score", 0.0), 2),
    }


def _resolve(query: str) -> tuple[list[dict], str]:
    """Resolve a name/email to ranked candidate persons.

    Returns (candidates, key) where candidates are merged across SF+NS on email
    and sorted best-first, and key is the matching strategy used ("email"/"name").
    """
    if _looks_like_email(query):
        email = query.strip().lower()
        owner_row = _ns_owner_by_email(email)
        sf_rows = []
        try:
            e = escape_soql(email)
            sf_rows = sf_query(
                "SELECT Id, FirstName, LastName, Email, AccountId, Account.Name "
                f"FROM Contact WHERE Email = '{e}' LIMIT 5"
            )
        except Exception:  # noqa: BLE001
            sf_rows = []
        sf0 = sf_rows[0] if sf_rows and isinstance(sf_rows[0], dict) else {}
        name = (
            (owner_row or {}).get("companyname")
            or " ".join(p for p in [sf0.get("FirstName"), sf0.get("LastName")] if p).strip()
            or email
        )
        cand = {
            "name": name,
            "email": email,
            "sf_contact_id": sf0.get("Id"),
            "sf_account_id": sf0.get("AccountId"),
            "account_name": (sf0.get("Account") or {}).get("Name"),
            "owner": _ns_owner_summary(owner_row) if owner_row else None,
            "score": 1.0,
        }
        return ([cand], "email") if (owner_row or sf0) else ([], "email")

    tokens = _tokens(query)
    anchor = _anchor(tokens)
    if not anchor:
        return [], "name"

    sf_rows = _sf_contacts_by_name(anchor)
    ns_rows = [r for r in _ns_customers_by_name(anchor) if "error" not in r]

    merged: dict[str, dict] = {}

    def _key(email: str | None, name: str) -> str:
        return (email or "").lower() or f"name:{_norm(name)}"

    for r in sf_rows:
        if not isinstance(r, dict):
            continue
        name = " ".join(p for p in [r.get("FirstName"), r.get("LastName")] if p).strip()
        email = (r.get("Email") or "").lower() or None
        k = _key(email, name)
        c = merged.setdefault(k, {"name": name, "email": email, "owner": None, "score": 0.0})
        c["name"] = c["name"] or name
        c["email"] = c["email"] or email
        c["sf_contact_id"] = r.get("Id")
        c["sf_account_id"] = r.get("AccountId")
        c["account_name"] = (r.get("Account") or {}).get("Name")
        c["score"] = max(c["score"], _name_score(tokens, name))

    for r in ns_rows:
        name = r.get("companyname") or r.get("entityid") or ""
        email = (r.get("email") or "").lower() or None
        k = _key(email, name)
        c = merged.setdefault(k, {"name": name, "email": email, "owner": None, "score": 0.0})
        c["name"] = c["name"] or name
        c["email"] = c["email"] or email
        c["owner"] = _ns_owner_summary(r)
        c["account_name"] = c.get("account_name")
        c["score"] = max(
            c["score"],
            _name_score(tokens, name, r.get("firstname"), r.get("lastname")),
        )

    # Demote do-not-use / inactive owner rows so a live record wins ties.
    def _rank(c: dict) -> tuple:
        owner = c.get("owner") or {}
        return (
            c.get("score", 0.0),
            not owner.get("do_not_use", False),
            not owner.get("is_inactive", False),
        )

    candidates = sorted(merged.values(), key=_rank, reverse=True)
    return [c for c in candidates if c.get("score", 0.0) > 0], "name"


def build_person_brief(query: str, deep: bool = True) -> dict:
    """Resolve a person/owner by name or email and build a consolidated profile."""
    q = (query or "").strip()
    out: dict = {"query": q, "resolution": {"status": "not_found"}, "candidates": []}
    if not q:
        out["error"] = "query is required — a person's name or email address."
        return out

    candidates, key = _resolve(q)
    out["candidates"] = [_slim_candidate(c) for c in candidates[:8]]

    if not candidates:
        return out

    top = candidates[0]
    # Ambiguous when the runner-up is about as good a name match (e.g. a couple or
    # duplicate accounts sharing a surname). Email/single matches are unambiguous.
    if key == "name" and len(candidates) > 1:
        second = candidates[1].get("score", 0.0)
        if top.get("score", 0.0) - second < 0.34:
            out["resolution"] = {
                "status": "ambiguous",
                "note": (
                    "Multiple people match this name; pick one (by email or "
                    "ns_customer_id) and call again, or pass a fuller name."
                ),
            }
            return out

    email = top.get("email")
    account_id = top.get("sf_account_id")
    out["resolution"] = {
        "status": "matched",
        "matched_name": top.get("name"),
        "email": email,
        "matched_by": key,
    }

    # NetSuite owner record — the authoritative vineyard-owner source. Reuse what
    # resolution fetched, else look up by email.
    ns_owner = top.get("owner")
    if ns_owner is None and email:
        row = _ns_owner_by_email(email)
        ns_owner = _ns_owner_summary(row) if row else None
    ns_owner = ns_owner or {}

    # Opportunities (one query) → classify the won ones into Vines offerings.
    opps = _account_opportunities(account_id) if account_id else []
    won = [o for o in opps if o.get("IsWon")]
    by_kind: dict[str, list[dict]] = {"vineyard": [], "villa": [], "membership": []}
    for o in won:
        kind = _classify_offering(o.get("Name"), o.get("Type"))
        if kind in by_kind:
            by_kind[kind].append(o)
    land = _tvom_purchases(account_id) if account_id else []
    services = _account_services(account_id) if account_id else {}
    current = services.get("current", [])
    cur_vineyard = _services_match(current, "vineyard")
    cur_villa = _services_match(current, "villa", "housing")
    cur_membership = _services_match(current, "membership", "tvg")

    # An "owner" at The Vines owns a vineyard and/or a villa. Collect the evidence
    # each system offers, for transparency.
    vy_signals = []
    if cur_vineyard:
        vy_signals.append("current_services")
    if ns_owner.get("is_owner"):
        vy_signals.append("netsuite_owner_record")
    if by_kind["vineyard"]:
        vy_signals.append("won_opportunity")
    if land:
        vy_signals.append("tvom_land_sale")
    villa_signals = []
    if cur_villa:
        villa_signals.append("current_services")
    if by_kind["villa"]:
        villa_signals.append("won_opportunity")

    # Current_Services__c is The Vines' source of truth for ownership: when it's
    # set, trust it for is_owner and treat the other systems as corroborating
    # detail only (a stale won opp must NOT flip is_owner against it). Fall back to
    # inferring from those systems only when the Account has no services recorded.
    has_services = bool(current)
    if has_services:
        vineyard_owner, villa_owner, tvg_member = (
            bool(cur_vineyard),
            bool(cur_villa),
            bool(cur_membership),
        )
        basis = "current_services"
    else:
        vineyard_owner = bool(vy_signals)
        villa_owner = bool(villa_signals)
        tvg_member = bool(by_kind["membership"])
        basis = "inferred"

    # CS rep: NetSuite account manager for vineyard owners; else the owner of the
    # most recent won opportunity (covers villa owners with no NetSuite record).
    cs_rep = ns_owner.get("cs_rep") or (_slim_opp(won[0]).get("owner") if won else None)

    out["ownership"] = {
        "is_owner": vineyard_owner or villa_owner,
        "basis": basis,
        "cs_rep": cs_rep,
        "current_services": current,
        "owned_lots": services.get("owned_lots"),
        "vineyard": {
            "is_owner": vineyard_owner,
            "signals": vy_signals,
            "current_services": cur_vineyard,
            "owner_code": ns_owner.get("owner_code"),
            "brand": ns_owner.get("brand"),
            "vineyard_name": ns_owner.get("vineyard"),
            "number_of_lots": ns_owner.get("number_of_lots"),
            "registration_status": ns_owner.get("registration_status"),
            "land_purchases": land,
            "won_opportunities": [_slim_opp(o) for o in by_kind["vineyard"]],
        },
        "villa": {
            "is_owner": villa_owner,
            "signals": villa_signals,
            "current_services": cur_villa,
            "won_opportunities": [_slim_opp(o) for o in by_kind["villa"]],
        },
    }
    out["membership"] = {
        "tvg_member": tvg_member,
        "current_services": cur_membership,
        "won_opportunities": [_slim_opp(o) for o in by_kind["membership"]],
    }
    out["services"] = services

    out["identity"] = {
        "name": top.get("name"),
        "email": email,
        "account_name": top.get("account_name"),
        **(_sf_identity_by_email(email) if email else {}),
    }
    out["opportunities"] = [_slim_opp(o) for o in opps]
    out["system_ids"] = {
        "sf_contact_id": top.get("sf_contact_id"),
        "sf_account_id": account_id,
        "ns_customer_id": ns_owner.get("ns_customer_id"),
    }
    # Primary record links for a human to open/verify in Salesforce. Link the
    # account first; the won-opportunity links live under ownership.
    out["links"] = {
        "account": record_url("Account", account_id),
        "contact": record_url("Contact", top.get("sf_contact_id")),
    }

    # Deep enrichment: the full cross-system pull (stays, financials, marketing,
    # OPERA) — only meaningful with an email to join on.
    if deep and email:
        try:
            p = guest_360(email)
            out["stays"] = p.get("stays")
            out["stays_opera"] = p.get("stays_opera")
            out["financials"] = p.get("financials")
            out["marketing"] = p.get("marketing")
            out["system_ids"].update({k: v for k, v in (p.get("system_ids") or {}).items() if v})
            if p.get("_errors"):
                out["_errors"] = p["_errors"]
        except Exception as e:  # noqa: BLE001 — deep section is best-effort
            out["_errors"] = [f"guest_360: {e}"]

    return out


def register_tools(mcp):
    """Register the person_brief tool on the given FastMCP instance."""

    @mcp.tool()
    def person_brief(query: str, deep: bool = True) -> dict:
        """Look up a person/owner by NAME (or email) and return one consolidated profile.

        Built for "tell me about <name>" style asks so you don't have to chain a
        search → get → SuiteQL yourself. Resolves a free-text name across
        Salesforce (Contact/Account/TVRS_Guest) and NetSuite (customer), then
        returns identity, ownership, membership, opportunities and — when
        deep=True — the full cross-system guest_360 (stays, financials, marketing,
        OPERA).

        Ownership at The Vines means owning a VINEYARD and/or a VILLA (one, the
        other, or both). The authoritative measure is the Account's
        Current_Services__c picklist (e.g. "TVOM Vineyards", "TVOM Villas",
        "TVOM Villa Expansion", "TVOM Housing Lot"); the NetSuite owner record,
        won opportunities, and TVOM_Sales land records corroborate it. TVG
        membership and guest/prospect status are reported SEPARATELY — they are
        NOT ownership.

        Answers e.g.: "is X an owner?" (ownership.is_owner + .vineyard/.villa),
        "who is the CS rep for Barhorst" (ownership.cs_rep), "what city is home for
        the Monaghans" (identity.city/country), "when did Horacio Lopez purchase /
        how many acres" (ownership.vineyard.land_purchases[].purchase_year/.acres).

        Resolution: email is the reliable cross-system key. If several people match
        a name (a couple, or duplicate accounts), resolution.status is "ambiguous"
        and `candidates` lists the options — pick one and call again with their
        email. "NO USAR"/inactive duplicate records are demoted automatically.

        Out of scope (use other tools): co-member/co-ownership and parcel-level
        varietal/planted-year/acreage-by-varietal live in NetSuite records not yet
        mapped — those are for a dedicated vineyard_lookup.

        Args:
            query: A person's name ("Mirta Guzman", "Barhorst") or email address.
            deep: Also attach the full cross-system guest_360 sections (stays,
                financials, marketing, OPERA). Set False for a fast core profile
                (identity + ownership + membership + opportunities) when you only
                need a single attribute (default True).

        Returns:
            A dict with: resolution (status matched|ambiguous|not_found,
            matched_name, email), candidates (when ambiguous), identity,
            ownership (is_owner, cs_rep, current_services, vineyard{is_owner,
            signals, brand, owner_code, vineyard_name, number_of_lots,
            land_purchases, won_opportunities}, villa{is_owner, signals,
            won_opportunities}), membership (tvg_member), services (current/past/
            future), opportunities, system_ids, links (Salesforce account/contact
            URLs for a human to open; each won_opportunities entry also has a
            `url`), and — when deep — stays, stays_opera, financials, marketing.
            Partial failures are in _errors.
        """
        return build_person_brief(query, deep=deep)
