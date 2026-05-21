"""Cross-system lookup tools that correlate data across Salesforce, NetSuite, and Pardot."""

from src.sanitize import escape_soql, escape_suiteql, validate_sf_id
from src.sf_client import query as sf_query
from src.ns_client import suiteql_query
from src.pardot_client import query_prospects
from src.opera_client import query as opera_query


def lookup_guest(email: str) -> dict:
    """Search for a guest/customer by email across all three systems.

    Returns a dict with results from each system (or error/null per system).
    """
    email_lower = email.strip().lower()
    result = {"email": email_lower, "salesforce": None, "netsuite": None, "pardot": None}

    # Salesforce — TVRS_Guest__c + Contact + Account
    try:
        sf_records = sf_query(
            f"SELECT Id, Guest_First_Name__c, Guest_Last_Name__c, Email__c, "
            f"Check_In_Date__c, Check_Out_Date__c, Villa_number__c, "
            f"Contact__c, Contact__r.AccountId "
            f"FROM TVRS_Guest__c "
            f"WHERE Email__c = '{escape_soql(email_lower)}' "
            f"ORDER BY Check_In_Date__c DESC LIMIT 5"
        )
        # Also search Person Account
        sf_accounts = sf_query(
            f"SELECT Id, Name, PersonEmail, IsPersonAccount "
            f"FROM Account "
            f"WHERE PersonEmail = '{escape_soql(email_lower)}' LIMIT 1"
        )
        result["salesforce"] = {
            "guest_records": sf_records,
            "account": sf_accounts[0] if sf_accounts else None,
        }
    except Exception as e:
        result["salesforce"] = {"error": str(e)}

    # NetSuite — customer by email
    try:
        ns_records = suiteql_query(
            f"SELECT id, entityid, companyname, firstname, lastname, email, "
            f"phone, isperson, balance "
            f"FROM customer "
            f"WHERE LOWER(email) = '{escape_suiteql(email_lower)}'"
        )
        result["netsuite"] = {"customers": ns_records}
    except Exception as e:
        result["netsuite"] = {"error": str(e)}

    # Pardot — prospect by email
    try:
        pardot_result = query_prospects({"fields": "id,email,firstName,lastName,createdAt",
                                         "email": email_lower})
        result["pardot"] = pardot_result
    except Exception as e:
        result["pardot"] = {"error": str(e)}

    return result


def guest_360(email: str) -> dict:
    """Build a unified guest profile from all three systems.

    Returns enriched data: basic info, stay history, financial summary,
    marketing engagement, and cross-system IDs.
    """
    email_lower = email.strip().lower()
    profile: dict = {
        "email": email_lower,
        "identity": {},
        "stays": [],
        "financials": {},
        "marketing": {},
        "system_ids": {},
    }

    # --- Salesforce ---
    try:
        # Guest stays
        stays = sf_query(
            f"SELECT Id, Guest_First_Name__c, Guest_Last_Name__c, Email__c, "
            f"Check_In_Date__c, Check_Out_Date__c, Villa_number__c, "
            f"City__c, Country__c, Language__c, Comments__c, "
            f"Contact__c, Contact__r.AccountId "
            f"FROM TVRS_Guest__c "
            f"WHERE Email__c = '{escape_soql(email_lower)}' "
            f"ORDER BY Check_In_Date__c DESC"
        )
        if stays:
            first = stays[0]
            profile["identity"]["first_name"] = first.get("Guest_First_Name__c")
            profile["identity"]["last_name"] = first.get("Guest_Last_Name__c")
            profile["identity"]["city"] = first.get("City__c")
            profile["identity"]["country"] = first.get("Country__c")
            profile["identity"]["language"] = first.get("Language__c")
            profile["stays"] = [
                {
                    "check_in": s.get("Check_In_Date__c"),
                    "check_out": s.get("Check_Out_Date__c"),
                    "villa": s.get("Villa_number__c"),
                    "comments": s.get("Comments__c"),
                }
                for s in stays
            ]
            profile["system_ids"]["sf_contact_id"] = first.get("Contact__c")
            contact_r = first.get("Contact__r") or {}
            profile["system_ids"]["sf_account_id"] = contact_r.get("AccountId")

        # Opportunities via Account
        account_id = profile["system_ids"].get("sf_account_id")
        if account_id:
            validate_sf_id(account_id)
            opps = sf_query(
                f"SELECT Id, Name, StageName, Amount, CloseDate, IsClosed, IsWon "
                f"FROM Opportunity "
                f"WHERE AccountId = '{escape_soql(account_id)}' "
                f"ORDER BY CloseDate DESC LIMIT 10"
            )
            profile["financials"]["sf_opportunities"] = opps
            profile["system_ids"]["sf_guest_ids"] = [s.get("Id") for s in stays]
    except Exception as e:
        profile["_errors"] = profile.get("_errors", [])
        profile["_errors"].append(f"Salesforce: {e}")

    # --- NetSuite ---
    try:
        customers = suiteql_query(
            f"SELECT id, entityid, companyname, firstname, lastname, email, "
            f"phone, isperson, balance "
            f"FROM customer "
            f"WHERE LOWER(email) = '{escape_suiteql(email_lower)}'"
        )
        if customers:
            cust = customers[0]
            profile["system_ids"]["ns_customer_id"] = cust.get("id")
            profile["financials"]["ns_balance"] = cust.get("balance")
            # Backfill identity if SF didn't have it
            if not profile["identity"].get("first_name"):
                profile["identity"]["first_name"] = cust.get("firstname")
                profile["identity"]["last_name"] = cust.get("lastname")

            # Recent transactions
            cust_id = int(cust["id"])
            txns = suiteql_query(
                f"SELECT t.id, t.tranid, t.type, t.trandate, t.status, t.foreigntotal "
                f"FROM transaction t "
                f"WHERE t.entity = {cust_id} "
                f"ORDER BY t.trandate DESC"
            )
            profile["financials"]["ns_transactions"] = txns
    except Exception as e:
        profile["_errors"] = profile.get("_errors", [])
        profile["_errors"].append(f"NetSuite: {e}")

    # --- Pardot ---
    try:
        pardot_result = query_prospects({"fields": "id,email,firstName,lastName,score,grade,"
                                                    "createdAt,lastActivityAt",
                                         "email": email_lower})
        values = pardot_result.get("values", [])
        if values:
            prospect = values[0]
            profile["system_ids"]["pardot_prospect_id"] = prospect.get("id")
            profile["marketing"]["pardot_score"] = prospect.get("score")
            profile["marketing"]["pardot_grade"] = prospect.get("grade")
            profile["marketing"]["last_activity"] = prospect.get("lastActivityAt")
        profile["marketing"]["pardot_raw"] = pardot_result
    except Exception as e:
        profile["_errors"] = profile.get("_errors", [])
        profile["_errors"].append(f"Pardot: {e}")

    # --- OPERA PMS ---
    try:
        name_rows = opera_query(
            "SELECT n.NAME_ID, n.FIRST, n.LAST, n.LANGUAGE "
            "FROM OPERA.NAME n "
            "JOIN OPERA.NAME_PHONE p ON n.NAME_ID = p.NAME_ID "
            "WHERE p.PHONE_ROLE = 'EMAIL' AND p.PRIMARY_YN = 'Y' "
            "AND LOWER(p.PHONE_NUMBER) = LOWER(:email) "
            "AND n.NAME_TYPE = 'D'",
            binds={"email": email_lower},
            limit=5,
        )
        if name_rows:
            name_id = name_rows[0]["name_id"]
            profile["system_ids"]["opera_name_id"] = name_id
            if not profile["identity"].get("first_name"):
                profile["identity"]["first_name"] = name_rows[0].get("first")
                profile["identity"]["last_name"] = name_rows[0].get("last")
            stays = opera_query(
                "SELECT rn.RESV_NAME_ID, "
                "TO_CHAR(TRUNC(rn.BEGIN_DATE), 'YYYY-MM-DD') AS CHECK_IN, "
                "TO_CHAR(TRUNC(rn.END_DATE),   'YYYY-MM-DD') AS CHECK_OUT, "
                "rn.RESV_STATUS, rn.RATE_CODE, rn.MARKET_CODE, rn.SOURCE_CODE "
                "FROM OPERA.RESERVATION_NAME rn "
                "WHERE rn.RESORT = 'VINES' AND rn.NAME_ID = :name_id "
                "ORDER BY rn.BEGIN_DATE DESC",
                binds={"name_id": int(name_id)},
                limit=100,
            )
            profile["stays_opera"] = stays
    except Exception as e:
        profile["_errors"] = profile.get("_errors", [])
        profile["_errors"].append(f"OPERA: {e}")

    return profile


def register_tools(mcp):
    """Register cross-system tools on the given FastMCP instance."""

    @mcp.tool()
    def lookup_guest_by_email(email: str) -> dict:
        """Search for a guest/customer by email across Salesforce, NetSuite, and Pardot.

        Returns results from each system in a single call — useful for quickly
        checking if a person exists in any system.

        Args:
            email: The email address to search for (case-insensitive).

        Returns:
            A dict with 'salesforce', 'netsuite', and 'pardot' keys, each containing
            the matching records or an error message if that system failed.
        """
        return lookup_guest(email)

    @mcp.tool()
    def guest_360_profile(email: str) -> dict:
        """Build a unified 360-degree guest profile from all three systems.

        Pulls together: identity info, stay history (Salesforce TVRS_Guest__c),
        financial data (NetSuite transactions + SF Opportunities), marketing
        engagement (Pardot score/grade/activity), and cross-system record IDs.

        Args:
            email: The guest's email address (case-insensitive).

        Returns:
            A unified profile dict with sections: identity, stays, financials,
            marketing, and system_ids. Partial results are returned if some
            systems fail (errors listed in _errors).
        """
        return guest_360(email)
