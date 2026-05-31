"""OPERA PMS MCP tool definitions.

Read-only tools backed by :mod:`src.opera_client`. All tools wrap exceptions
into ``{"error": str(e)}`` dicts to match the SF/NS tool pattern.
"""

from src.opera_client import HARD_ROW_LIMIT, query as opera_query
from src.opera_schema import GLOBAL_TIPS, SCHEMA as OPERA_SCHEMA


VINES_RESORT = "VINES"


def register_tools(mcp):
    """Register all OPERA tools on the given FastMCP instance."""

    @mcp.tool()
    def opera_sql_query(
        sql: str, binds: dict | None = None, limit: int = 500
    ) -> list[dict] | dict:
        """Run a read-only SQL query against the OPERA PMS Oracle database.

        Only SELECT (or WITH …) statements are accepted; INSERT/UPDATE/DELETE/DDL
        is rejected before the query reaches Oracle. Bind parameters are passed
        through to the OPERA API (and on to Oracle) — reference them as :name in the SQL.

        Call opera_get_opera_schema first to see available tables, columns, and
        common filters. Always filter reservation tables to RESORT = 'VINES'.

        Args:
            sql: A SELECT or WITH-statement SQL string.
            binds: Optional dict of named bind parameters (e.g. {"name_id": 12345}).
            limit: Max rows to return (default 500, hard cap 5000).

        Returns:
            A list of row dicts (lowercase column keys). On failure, a single-element
            list containing an {"error": ...} dict.
        """
        try:
            return opera_query(sql, binds=binds or {}, limit=limit)
        except Exception as e:
            return [{"error": str(e)}]

    @mcp.tool()
    def opera_list_arrivals(date: str, include_cancelled: bool = False) -> list[dict]:
        """List guests arriving (checking in) at The Vines on a given date.

        Args:
            date: Arrival date as YYYY-MM-DD.
            include_cancelled: If True, include cancelled reservations.

        Returns:
            List of arrival dicts with name, email, country, room, ETA, status,
            and reservation IDs. Companions appear as separate rows; group them
            by parent_resv_name_id if desired.
        """
        status_clause = "" if include_cancelled else "AND rn.RESV_STATUS != 'CANCELLED'"
        sql = f"""
            SELECT rn.RESV_NAME_ID,
                   rn.PARENT_RESV_NAME_ID,
                   n.NAME_ID,
                   n.FIRST,
                   n.LAST,
                   n.LANGUAGE,
                   p.PHONE_NUMBER AS EMAIL,
                   a.COUNTRY,
                   TO_CHAR(TRUNC(rn.BEGIN_DATE), 'YYYY-MM-DD') AS CHECK_IN,
                   TO_CHAR(TRUNC(rn.END_DATE),   'YYYY-MM-DD') AS CHECK_OUT,
                   rn.RESV_STATUS,
                   TO_CHAR(rn.ARRIVAL_ESTIMATE_TIME, 'HH24:MI') AS ETA,
                   daily.ROOM,
                   daily.ADULTS,
                   daily.CHILDREN
            FROM OPERA.RESERVATION_NAME rn
            JOIN OPERA.NAME n
              ON rn.NAME_ID = n.NAME_ID AND n.NAME_TYPE = 'D'
            LEFT JOIN OPERA.NAME_PHONE p
              ON n.NAME_ID = p.NAME_ID
             AND p.PHONE_ROLE = 'EMAIL' AND p.PRIMARY_YN = 'Y'
            LEFT JOIN OPERA.NAME_ADDRESS a
              ON n.NAME_ID = a.NAME_ID
             AND a.PRIMARY_YN = 'Y' AND a.INACTIVE_DATE IS NULL
            LEFT JOIN (
                SELECT rden.RESV_NAME_ID, rde.ROOM, rden.ADULTS, rden.CHILDREN,
                       ROW_NUMBER() OVER (
                         PARTITION BY rden.RESV_NAME_ID
                         ORDER BY rden.RESERVATION_DATE
                       ) AS rn_idx
                FROM OPERA.RESERVATION_DAILY_ELEMENT_NAME rden
                JOIN OPERA.RESERVATION_DAILY_ELEMENTS rde
                  ON rden.RESORT = rde.RESORT
                 AND rden.RESERVATION_DATE = rde.RESERVATION_DATE
                 AND rden.RESV_DAILY_EL_SEQ = rde.RESV_DAILY_EL_SEQ
                WHERE rden.RESORT = :resort
            ) daily
              ON rn.RESV_NAME_ID = daily.RESV_NAME_ID
             AND daily.rn_idx = 1
            WHERE rn.RESORT = :resort
              AND TRUNC(rn.BEGIN_DATE) = TO_DATE(:date_str, 'YYYY-MM-DD')
              {status_clause}
            ORDER BY n.LAST, n.FIRST
        """
        try:
            return opera_query(sql, binds={"resort": VINES_RESORT, "date_str": date})
        except Exception as e:
            return [{"error": str(e)}]

    @mcp.tool()
    def opera_list_in_house(date: str) -> list[dict]:
        """List guests in-house at The Vines on a given date (checked in before, not yet out).

        Args:
            date: The target date as YYYY-MM-DD.

        Returns:
            List of in-house guest dicts.
        """
        sql = """
            SELECT rn.RESV_NAME_ID,
                   rn.PARENT_RESV_NAME_ID,
                   n.NAME_ID,
                   n.FIRST,
                   n.LAST,
                   p.PHONE_NUMBER AS EMAIL,
                   a.COUNTRY,
                   TO_CHAR(TRUNC(rn.BEGIN_DATE), 'YYYY-MM-DD') AS CHECK_IN,
                   TO_CHAR(TRUNC(rn.END_DATE),   'YYYY-MM-DD') AS CHECK_OUT,
                   rn.RESV_STATUS,
                   daily.ROOM,
                   daily.ADULTS,
                   daily.CHILDREN
            FROM OPERA.RESERVATION_NAME rn
            JOIN OPERA.NAME n
              ON rn.NAME_ID = n.NAME_ID AND n.NAME_TYPE = 'D'
            LEFT JOIN OPERA.NAME_PHONE p
              ON n.NAME_ID = p.NAME_ID
             AND p.PHONE_ROLE = 'EMAIL' AND p.PRIMARY_YN = 'Y'
            LEFT JOIN OPERA.NAME_ADDRESS a
              ON n.NAME_ID = a.NAME_ID
             AND a.PRIMARY_YN = 'Y' AND a.INACTIVE_DATE IS NULL
            LEFT JOIN (
                SELECT rden.RESV_NAME_ID, rde.ROOM, rden.ADULTS, rden.CHILDREN,
                       ROW_NUMBER() OVER (
                         PARTITION BY rden.RESV_NAME_ID
                         ORDER BY rden.RESERVATION_DATE
                       ) AS rn_idx
                FROM OPERA.RESERVATION_DAILY_ELEMENT_NAME rden
                JOIN OPERA.RESERVATION_DAILY_ELEMENTS rde
                  ON rden.RESORT = rde.RESORT
                 AND rden.RESERVATION_DATE = rde.RESERVATION_DATE
                 AND rden.RESV_DAILY_EL_SEQ = rde.RESV_DAILY_EL_SEQ
                WHERE rden.RESORT = :resort
            ) daily
              ON rn.RESV_NAME_ID = daily.RESV_NAME_ID
             AND daily.rn_idx = 1
            WHERE rn.RESORT = :resort
              AND rn.RESV_STATUS != 'CANCELLED'
              AND TRUNC(rn.BEGIN_DATE) <= TO_DATE(:date_str, 'YYYY-MM-DD')
              AND TRUNC(rn.END_DATE)   >  TO_DATE(:date_str, 'YYYY-MM-DD')
            ORDER BY n.LAST, n.FIRST
        """
        try:
            return opera_query(sql, binds={"resort": VINES_RESORT, "date_str": date})
        except Exception as e:
            return [{"error": str(e)}]

    @mcp.tool()
    def opera_search_profiles(
        email: str = "", last_name: str = "", first_name: str = "", limit: int = 25
    ) -> list[dict]:
        """Search OPERA guest profiles by email and/or name.

        At least one of `email`, `last_name`, or `first_name` must be non-empty.
        Email match is case-insensitive exact; name match is case-insensitive prefix.

        Args:
            email: Email address (exact match, case-insensitive).
            last_name: Last name prefix (case-insensitive).
            first_name: First name prefix (case-insensitive).
            limit: Max rows (default 25).

        Returns:
            List of profile dicts with NAME_ID, name, email, country, language.
        """
        email = (email or "").strip()
        last_name = (last_name or "").strip()
        first_name = (first_name or "").strip()
        if not (email or last_name or first_name):
            return [{"error": "Provide at least one of: email, last_name, first_name."}]

        conditions = ["n.NAME_TYPE = 'D'"]
        binds: dict = {}
        if email:
            conditions.append("LOWER(p.PHONE_NUMBER) = LOWER(:email)")
            binds["email"] = email
        if last_name:
            conditions.append("UPPER(n.LAST) LIKE UPPER(:last_name)")
            binds["last_name"] = f"{last_name}%"
        if first_name:
            conditions.append("UPPER(n.FIRST) LIKE UPPER(:first_name)")
            binds["first_name"] = f"{first_name}%"

        # Phone join is inner when filtering by email, outer otherwise.
        phone_join = "JOIN" if email else "LEFT JOIN"
        where_clause = " AND ".join(conditions)

        sql = f"""
            SELECT n.NAME_ID,
                   n.FIRST,
                   n.LAST,
                   n.LANGUAGE,
                   p.PHONE_NUMBER AS EMAIL,
                   a.CITY, a.STATE, a.COUNTRY
            FROM OPERA.NAME n
            {phone_join} OPERA.NAME_PHONE p
              ON n.NAME_ID = p.NAME_ID
             AND p.PHONE_ROLE = 'EMAIL' AND p.PRIMARY_YN = 'Y'
            LEFT JOIN OPERA.NAME_ADDRESS a
              ON n.NAME_ID = a.NAME_ID
             AND a.PRIMARY_YN = 'Y' AND a.INACTIVE_DATE IS NULL
            WHERE {where_clause}
            ORDER BY n.LAST, n.FIRST
        """
        try:
            return opera_query(sql, binds=binds, limit=min(limit, 200))
        except Exception as e:
            return [{"error": str(e)}]

    @mcp.tool()
    def opera_get_stay_history(name_id: int, limit: int = 50) -> list[dict]:
        """Return all reservations (past + future) for a guest profile.

        Args:
            name_id: OPERA NAME_ID (from opera_search_profiles).
            limit: Max stays to return (default 50).

        Returns:
            List of stay dicts ordered by check-in DESC.
        """
        sql = """
            SELECT rn.RESV_NAME_ID,
                   rn.PARENT_RESV_NAME_ID,
                   TO_CHAR(TRUNC(rn.BEGIN_DATE), 'YYYY-MM-DD') AS CHECK_IN,
                   TO_CHAR(TRUNC(rn.END_DATE),   'YYYY-MM-DD') AS CHECK_OUT,
                   rn.RESV_STATUS,
                   rn.RATE_CODE,
                   rn.MARKET_CODE,
                   rn.SOURCE_CODE,
                   daily.ROOM,
                   daily.ADULTS,
                   daily.CHILDREN
            FROM OPERA.RESERVATION_NAME rn
            LEFT JOIN (
                SELECT rden.RESV_NAME_ID, rde.ROOM, rden.ADULTS, rden.CHILDREN,
                       ROW_NUMBER() OVER (
                         PARTITION BY rden.RESV_NAME_ID
                         ORDER BY rden.RESERVATION_DATE
                       ) AS rn_idx
                FROM OPERA.RESERVATION_DAILY_ELEMENT_NAME rden
                JOIN OPERA.RESERVATION_DAILY_ELEMENTS rde
                  ON rden.RESORT = rde.RESORT
                 AND rden.RESERVATION_DATE = rde.RESERVATION_DATE
                 AND rden.RESV_DAILY_EL_SEQ = rde.RESV_DAILY_EL_SEQ
                WHERE rden.RESORT = :resort
            ) daily
              ON rn.RESV_NAME_ID = daily.RESV_NAME_ID
             AND daily.rn_idx = 1
            WHERE rn.RESORT = :resort
              AND rn.NAME_ID = :name_id
            ORDER BY rn.BEGIN_DATE DESC
        """
        try:
            return opera_query(
                sql,
                binds={"resort": VINES_RESORT, "name_id": int(name_id)},
                limit=min(limit, 500),
            )
        except Exception as e:
            return [{"error": str(e)}]

    @mcp.tool()
    def opera_get_profile_notes(name_id: int) -> dict:
        """Return all active profile-level notes and comments for an OPERA guest.

        Pulls from NAME_COMMENT (preferences) and NAME$NOTES (free-form notes
        including allergies, incidents, biographical info).

        Args:
            name_id: OPERA NAME_ID.

        Returns:
            Dict with 'comments' (list of NAME_COMMENT rows) and 'notes' (list of
            NAME$NOTES rows), or {"error": ...} on failure.
        """
        try:
            comments = opera_query(
                """
                SELECT LINE_NO, COMMENT_TYPE, COMMENTS
                FROM OPERA.NAME_COMMENT
                WHERE NAME_ID = :name_id
                  AND COMMENT_TYPE = 'PROF'
                  AND INACTIVE_DATE IS NULL
                ORDER BY LINE_NO
                """,
                binds={"name_id": int(name_id)},
            )
            notes = opera_query(
                """
                SELECT NOTE_CODE,
                       NOTES,
                       TO_CHAR(INSERT_DATE, 'YYYY-MM-DD HH24:MI') AS INSERT_DATE
                FROM OPERA."NAME$NOTES"
                WHERE NAME_ID = :name_id
                  AND INACTIVE_DATE IS NULL
                ORDER BY INSERT_DATE
                """,
                binds={"name_id": int(name_id)},
            )
            return {"name_id": int(name_id), "comments": comments, "notes": notes}
        except Exception as e:
            return {"error": str(e)}

    @mcp.tool()
    def opera_get_opera_schema(tables: str = "") -> dict:
        """Return curated schema for OPERA PMS tables including fields, filters, and example SQL.

        Call this before writing opera_sql_query so you know table names, key columns,
        and the VINES-specific filters (RESORT='VINES', NAME_TYPE='D', etc.).

        Args:
            tables: Comma-separated table names (e.g. "NAME,RESERVATION_NAME").
                    If empty, returns schema for all curated tables plus global tips.

        Returns:
            Dict keyed by table name, plus a "_tips" key with cross-table guidance.
        """
        if not tables.strip():
            return {**OPERA_SCHEMA, "_tips": GLOBAL_TIPS}

        result: dict = {}
        for raw in tables.split(","):
            name = raw.strip()
            if not name:
                continue
            for schema_name, schema_data in OPERA_SCHEMA.items():
                if schema_name.lower() == name.lower():
                    result[schema_name] = schema_data
                    break
        result["_tips"] = GLOBAL_TIPS
        return result

    # Expose constants for tests/introspection
    register_tools.row_limit = HARD_ROW_LIMIT
