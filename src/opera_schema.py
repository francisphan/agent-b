"""Curated OPERA PMS schema for The Vines of Mendoza.

Mirrors sf_schema.py / ns_schema.py — documents the OPERA 5 Oracle tables
that matter for guest, reservation, and notes lookups. The Vines OPERA
property code is ``VINES`` and that filter belongs on every reservation
query. Real guest profiles use ``NAME_TYPE = 'D'`` (others are agents,
groups, corporate, etc.).
"""

SCHEMA: dict[str, dict] = {
    "NAME": {
        "label": "Guest Profile",
        "description": (
            "Master profile record. One row per person/company in OPERA. "
            "NAME_ID is the primary key referenced by reservations, phones, "
            "addresses, comments, and notes. NAME_TYPE = 'D' = individual guest."
        ),
        "key_fields": [
            {"name": "NAME_ID", "type": "number", "note": "Primary key"},
            {"name": "FIRST", "type": "string"},
            {"name": "LAST", "type": "string"},
            {"name": "FULL_NAME", "type": "string"},
            {
                "name": "NAME_TYPE",
                "type": "string",
                "note": "'D' = guest, 'C' = company, 'G' = group, 'S' = source/agent",
            },
            {
                "name": "LANGUAGE",
                "type": "string",
                "note": "OPERA language code (E=English, S=Spanish, P=Portuguese, etc.)",
            },
            {"name": "VIP_STATUS", "type": "string"},
            {"name": "BIRTH_DATE", "type": "date"},
            {"name": "NATIONALITY", "type": "string"},
            {"name": "INSERT_DATE", "type": "datetime"},
            {"name": "UPDATE_DATE", "type": "datetime"},
        ],
        "example_sql": [
            "SELECT NAME_ID, FIRST, LAST FROM OPERA.NAME "
            "WHERE NAME_TYPE = 'D' AND UPPER(LAST) = 'SMITH' "
            "FETCH FIRST 20 ROWS ONLY",
        ],
    },
    "NAME_PHONE": {
        "label": "Guest Phone / Email",
        "description": (
            "Phone numbers AND email addresses for a profile. Email is stored "
            "with PHONE_ROLE = 'EMAIL'; mobile/landline with PHONE_ROLE in "
            "'MOBILE'/'PHONE'/'FAX'. PRIMARY_YN = 'Y' marks the primary entry."
        ),
        "key_fields": [
            {"name": "NAME_ID", "type": "number", "note": "FK → NAME.NAME_ID"},
            {"name": "PHONE_ROLE", "type": "string", "note": "'EMAIL', 'MOBILE', 'PHONE', 'FAX'"},
            {
                "name": "PHONE_NUMBER",
                "type": "string",
                "note": "Holds email address when PHONE_ROLE = 'EMAIL'",
            },
            {"name": "PRIMARY_YN", "type": "string", "note": "'Y' for primary"},
            {"name": "INSERT_DATE", "type": "datetime"},
            {"name": "UPDATE_DATE", "type": "datetime"},
        ],
        "example_sql": [
            "SELECT NAME_ID, PHONE_NUMBER AS EMAIL FROM OPERA.NAME_PHONE "
            "WHERE PHONE_ROLE = 'EMAIL' AND PRIMARY_YN = 'Y' "
            "AND LOWER(PHONE_NUMBER) = LOWER(:email)",
        ],
    },
    "NAME_ADDRESS": {
        "label": "Guest Address",
        "description": (
            "Mailing addresses for a profile. PRIMARY_YN='Y' and "
            "INACTIVE_DATE IS NULL filters to the current primary address."
        ),
        "key_fields": [
            {"name": "NAME_ID", "type": "number", "note": "FK → NAME.NAME_ID"},
            {"name": "ADDRESS1", "type": "string"},
            {"name": "ADDRESS2", "type": "string"},
            {"name": "CITY", "type": "string"},
            {"name": "STATE", "type": "string"},
            {"name": "COUNTRY", "type": "string", "note": "ISO 3166-1 alpha-2 (e.g. 'US', 'AR')"},
            {"name": "POSTAL_CODE", "type": "string"},
            {"name": "PRIMARY_YN", "type": "string"},
            {"name": "INACTIVE_DATE", "type": "date"},
        ],
    },
    "RESERVATION_NAME": {
        "label": "Reservation",
        "description": (
            "One row per guest-on-a-reservation. RESV_NAME_ID is the PK; "
            "NAME_ID FKs to NAME. Always filter RESORT='VINES'. "
            "PARENT_RESV_NAME_ID groups companions under the primary booker."
        ),
        "key_fields": [
            {"name": "RESV_NAME_ID", "type": "number", "note": "PK"},
            {"name": "NAME_ID", "type": "number", "note": "FK → NAME"},
            {"name": "RESORT", "type": "string", "note": "Always 'VINES' here"},
            {"name": "BEGIN_DATE", "type": "date", "note": "Check-in"},
            {"name": "END_DATE", "type": "date", "note": "Check-out"},
            {
                "name": "RESV_STATUS",
                "type": "string",
                "note": "'RESERVED', 'CHECKED IN', 'CHECKED OUT', 'CANCELLED', 'NO SHOW'",
            },
            {
                "name": "PARENT_RESV_NAME_ID",
                "type": "number",
                "note": "Companion's parent reservation (NULL for primary)",
            },
            {"name": "ARRIVAL_ESTIMATE_TIME", "type": "datetime", "note": "ETA"},
            {"name": "RATE_CODE", "type": "string"},
            {"name": "MARKET_CODE", "type": "string"},
            {"name": "SOURCE_CODE", "type": "string"},
            {"name": "INSERT_DATE", "type": "datetime"},
            {"name": "UPDATE_DATE", "type": "datetime"},
        ],
        "common_filters": [
            "RESORT = 'VINES'",
            "RESV_STATUS IN ('RESERVED','CHECKED IN','CHECKED OUT')",
            "RESV_STATUS != 'CANCELLED'",
        ],
        "example_sql": [
            "SELECT rn.RESV_NAME_ID, n.FIRST, n.LAST, rn.BEGIN_DATE, rn.END_DATE, rn.RESV_STATUS "
            "FROM OPERA.RESERVATION_NAME rn "
            "JOIN OPERA.NAME n ON rn.NAME_ID = n.NAME_ID AND n.NAME_TYPE = 'D' "
            "WHERE rn.RESORT = 'VINES' "
            "AND TRUNC(rn.BEGIN_DATE) = TO_DATE(:arrival_date, 'YYYY-MM-DD') "
            "AND rn.RESV_STATUS != 'CANCELLED'",
        ],
    },
    "RESERVATION_DAILY_ELEMENT_NAME": {
        "label": "Reservation Daily — Guest Side",
        "description": (
            "Per-night per-guest record. Holds ADULTS / CHILDREN and links to "
            "RESERVATION_DAILY_ELEMENTS for the ROOM assignment for that night."
        ),
        "key_fields": [
            {"name": "RESV_NAME_ID", "type": "number", "note": "FK → RESERVATION_NAME"},
            {"name": "RESORT", "type": "string"},
            {"name": "RESERVATION_DATE", "type": "date", "note": "The specific night"},
            {
                "name": "RESV_DAILY_EL_SEQ",
                "type": "number",
                "note": "Composite key with RESORT+RESERVATION_DATE to RESERVATION_DAILY_ELEMENTS",
            },
            {"name": "ADULTS", "type": "number"},
            {"name": "CHILDREN", "type": "number"},
        ],
    },
    "RESERVATION_DAILY_ELEMENTS": {
        "label": "Reservation Daily — Room Side",
        "description": "Per-night room assignment. Join via RESORT+RESERVATION_DATE+RESV_DAILY_EL_SEQ.",
        "key_fields": [
            {"name": "RESORT", "type": "string"},
            {"name": "RESERVATION_DATE", "type": "date"},
            {"name": "RESV_DAILY_EL_SEQ", "type": "number"},
            {"name": "ROOM", "type": "string", "note": "Villa/room number"},
            {"name": "ROOM_CATEGORY", "type": "string"},
        ],
    },
    "NAME_COMMENT": {
        "label": "Profile Comment (Preferences)",
        "description": (
            "Multi-line profile comments. COMMENT_TYPE='PROF' holds guest preferences. "
            "Filter INACTIVE_DATE IS NULL to drop archived rows."
        ),
        "key_fields": [
            {"name": "NAME_ID", "type": "number"},
            {"name": "COMMENT_TYPE", "type": "string", "note": "Use 'PROF' for preferences"},
            {"name": "LINE_NO", "type": "number"},
            {"name": "COMMENTS", "type": "string"},
            {"name": "INACTIVE_DATE", "type": "date"},
        ],
    },
    "NAME$NOTES": {
        "label": "Profile Notes (Free-form, CLOB)",
        "description": (
            "Long-form profile notes — allergies (PREF), incidents (INCONV), "
            "biographical (GUESTPROF), etc. NOTES column is a CLOB. Table name "
            'contains a $ so must be double-quoted: OPERA."NAME$NOTES".'
        ),
        "key_fields": [
            {"name": "NAME_ID", "type": "number"},
            {
                "name": "NOTE_CODE",
                "type": "string",
                "note": "Common codes: PREF, INCONV, GUESTPROF",
            },
            {"name": "NOTES", "type": "clob"},
            {"name": "INACTIVE_DATE", "type": "date"},
            {"name": "INSERT_DATE", "type": "datetime"},
        ],
        "example_sql": [
            'SELECT NAME_ID, NOTE_CODE, NOTES FROM OPERA."NAME$NOTES" '
            "WHERE NAME_ID = :name_id AND INACTIVE_DATE IS NULL "
            "ORDER BY INSERT_DATE",
        ],
    },
    "RESERVATION_COMMENT": {
        "label": "Reservation Comment",
        "description": (
            "Per-reservation comments. COMMENT_TYPE in ('RESERVATION','CASHIER','IN HOUSE'). "
            "Filter RESORT='VINES'."
        ),
        "key_fields": [
            {"name": "RESV_NAME_ID", "type": "number"},
            {"name": "RESORT", "type": "string"},
            {"name": "COMMENT_TYPE", "type": "string"},
            {"name": "COMMENTS", "type": "string"},
            {"name": "INSERT_DATE", "type": "datetime"},
        ],
    },
    "RESERVATION_ALERTS": {
        "label": "Reservation Alert",
        "description": "Front-desk action alerts attached to a reservation (Check-In, Check-Out, etc.).",
        "key_fields": [
            {"name": "RESV_NAME_ID", "type": "number"},
            {"name": "RESORT", "type": "string"},
            {"name": "AREA", "type": "string", "note": "e.g. 'Check-In', 'Check-Out'"},
            {"name": "DESCRIPTION", "type": "string"},
            {"name": "INSERT_DATE", "type": "datetime"},
        ],
    },
}


GLOBAL_TIPS: list[str] = [
    "Always filter reservations to RESORT = 'VINES'.",
    "Guest profiles are NAME_TYPE = 'D' (not 'C'/'G'/'S').",
    "Email is stored in NAME_PHONE.PHONE_NUMBER with PHONE_ROLE = 'EMAIL' and PRIMARY_YN = 'Y'.",
    "Room number lives in RESERVATION_DAILY_ELEMENTS.ROOM — join via RESERVATION_DAILY_ELEMENT_NAME on (RESORT, RESERVATION_DATE, RESV_DAILY_EL_SEQ).",
    "Cancelled rows: RESV_STATUS = 'CANCELLED' — exclude unless you specifically want them.",
    "Companions share PARENT_RESV_NAME_ID with the primary booker.",
    'NAME$NOTES is double-quoted because the table name contains a $: OPERA."NAME$NOTES".',
    "Date binds: TO_DATE(:date_str, 'YYYY-MM-DD'). TRUNC() to compare to a date literal.",
    "Posting Master rooms (room prefix 'PM' or villa numbers 9000–9999) are charge-tracking accounts, not real stays.",
]
