"""Query validation and smart error suggestions for SOQL and SuiteQL.

Validates field names against curated schemas and provides fuzzy-match
suggestions when queries reference unknown fields or objects.
"""

import re
from difflib import get_close_matches

from src.sf_schema import SCHEMA as SF_SCHEMA
from src.ns_schema import SCHEMA as NS_SCHEMA


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _fuzzy_suggest(name: str, candidates: list[str], n: int = 3, cutoff: float = 0.5) -> list[str]:
    """Return close matches for name from candidates."""
    return get_close_matches(name, candidates, n=n, cutoff=cutoff)


def _extract_soql_parts(soql: str) -> dict:
    """Extract object name and field references from a SOQL query."""
    result: dict = {"object": None, "fields": [], "where_fields": []}

    # FROM clause — object name
    from_match = re.search(r"\bFROM\s+(\w+)", soql, re.IGNORECASE)
    if from_match:
        result["object"] = from_match.group(1)

    # SELECT fields (simplified — handles aliases and relationship traversals)
    select_match = re.search(r"SELECT\s+(.+?)\s+FROM\b", soql, re.IGNORECASE | re.DOTALL)
    if select_match:
        raw = select_match.group(1)
        # Remove aggregate functions but keep field inside
        raw = re.sub(r"(COUNT|SUM|AVG|MIN|MAX)\s*\(", "(", raw, flags=re.IGNORECASE)
        # Split by comma, strip
        for token in raw.split(","):
            token = token.strip()
            # Skip subqueries
            if "(" in token and "SELECT" in token.upper():
                continue
            # Handle "field alias" — take the field part
            parts = token.split()
            if parts:
                field = parts[0].strip("()")
                if field and field.upper() != "ID":
                    result["fields"].append(field)

    # WHERE clause fields
    where_match = re.search(
        r"\bWHERE\s+(.+?)(?:\bORDER\b|\bGROUP\b|\bLIMIT\b|\bOFFSET\b|$)",
        soql,
        re.IGNORECASE | re.DOTALL,
    )
    if where_match:
        raw = where_match.group(1)
        # Find field-like tokens before operators
        for match in re.finditer(r"(\w[\w.]*)\s*(?:=|!=|<|>|LIKE|IN\b|NOT\b)", raw, re.IGNORECASE):
            field = match.group(1)
            if field.upper() not in ("AND", "OR", "NOT", "NULL", "TRUE", "FALSE", "TODAY"):
                result["where_fields"].append(field)

    return result


def _get_sf_field_names(object_name: str) -> list[str] | None:
    """Get known field names for a Salesforce object from curated schema."""
    for name, schema in SF_SCHEMA.items():
        if name.lower() == object_name.lower():
            fields = [f["name"] for f in schema.get("key_fields", [])]
            fields.extend(schema.get("notable_booleans", []))
            return fields
    return None


def _extract_suiteql_tables(query: str) -> list[str]:
    """Extract table names from FROM/JOIN clauses in SuiteQL."""
    tables = []
    for match in re.finditer(r"\b(?:FROM|JOIN)\s+(\w+)", query, re.IGNORECASE):
        tables.append(match.group(1).lower())
    return tables


def _get_ns_field_names(record_type: str) -> list[str] | None:
    """Get known field names for a NetSuite record type from curated schema."""
    for name, schema in NS_SCHEMA.items():
        suiteql_table = schema.get("suiteql_table", name)
        if name.lower() == record_type.lower() or suiteql_table.lower() == record_type.lower():
            fields = [f["name"] for f in schema.get("key_fields", [])]
            # Also include lowercase SuiteQL field names
            fields.extend(schema.get("suiteql_fields", []))
            return list(set(fields))
    return None


# Long Text Area fields the live API refuses to filter on ("field 'X' can not
# be filtered in a query call"), keyed by lowercase object name. These came up
# in real bot traffic — extend as more are hit.
_NON_FILTERABLE_FIELDS: dict[str, tuple[str, ...]] = {
    "account": ("Description",),
    "tvrs_guest__c": ("Comments__c",),
}


# SuiteQL tables that are 100% certain to fail in this account/role, verified
# against live traffic — no SQL shape makes them work, so querying them blocks
# pre-flight like LIMIT does. Two classes with different fixes:
#
# Metadata tables run on the legacy search engine with a restricted column
# set — ordinary identifiers (id, label, fieldname) 400 with "Unknown
# identifier", and FETCH FIRST is a syntax error there.
_METADATA_TABLES = frozenset({"customfield", "customrecordtype"})

# These 400 with "Record 'X' was not found": the integration role lacks the
# Custom Lists permission (customlist*, customercategory, classification,
# supportcategory) and has no employee access at all (SuiteQL AND REST both
# refuse it — resolve employee references with BUILTIN.DF instead).
_UNGRANTED_TABLES = frozenset(
    {"customlist", "customercategory", "classification", "supportcategory", "employee"}
)


# ---------------------------------------------------------------------------
# Public validation
# ---------------------------------------------------------------------------


def validate_soql(soql: str) -> dict:
    """Validate a SOQL query against the curated Salesforce schema.

    Returns a dict with:
        valid: bool — True if no issues found (may still fail at API level)
        warnings: list[str] — potential issues detected
        suggestions: list[str] — actionable fix suggestions
    """
    result = {"valid": True, "warnings": [], "suggestions": []}
    parsed = _extract_soql_parts(soql)

    obj = parsed["object"]
    if not obj:
        result["valid"] = False
        result["warnings"].append("Could not parse FROM clause — missing object name.")
        return result

    # SOQL rejects scalar functions in WHERE ("Invalid aggregate function:
    # LOWER") — and doesn't need them: filters and LIKE are case-insensitive.
    where_match = re.search(
        r"\bWHERE\s+(.+?)(?:\bORDER\b|\bGROUP\b|\bLIMIT\b|\bOFFSET\b|$)",
        soql,
        re.IGNORECASE | re.DOTALL,
    )
    where_clause = where_match.group(1) if where_match else ""
    func_match = re.search(r"\b(LOWER|UPPER)\s*\(", where_clause, re.IGNORECASE)
    if func_match:
        result["valid"] = False
        result["warnings"].append(
            f"SOQL does not support {func_match.group(1).upper()}() (or any scalar "
            "function) in WHERE — it fails with 'Invalid aggregate function'."
        )
        result["suggestions"].append(
            "SOQL field filters and LIKE are already case-insensitive — drop the "
            "function wrapper (WHERE Name LIKE '%smith%')."
        )

    # Long Text Area fields can't appear in WHERE/ORDER BY — the API rejects the
    # query with "field 'X' can not be filtered in a query call".
    for obj_key, bad_fields in _NON_FILTERABLE_FIELDS.items():
        if obj.lower() != obj_key:
            continue
        for field in bad_fields:
            if re.search(
                rf"\b{field}\s*(?:=|!=|<|>|LIKE\b|IN\b|NOT\b)", where_clause, re.IGNORECASE
            ):
                result["valid"] = False
                result["warnings"].append(
                    f"'{field}' on {obj} is a Long Text Area and cannot be filtered "
                    "in SOQL (MALFORMED_QUERY)."
                )
                result["suggestions"].append(
                    "Search its text with SOSL instead: sf_search(sosl_query="
                    f'"FIND {{your text}} IN ALL FIELDS RETURNING {obj}(Id, Name, {field})").'
                )
    if not result["valid"]:
        return result

    # Check object name
    sf_objects = list(SF_SCHEMA.keys())
    matched_obj = None
    for name in sf_objects:
        if name.lower() == obj.lower():
            matched_obj = name
            break

    if matched_obj is None:
        # Not in curated schema — might still be valid, just warn
        close = _fuzzy_suggest(obj, sf_objects)
        if close:
            result["warnings"].append(
                f"Object '{obj}' not in curated schema. Did you mean: {', '.join(close)}?"
            )
            result["suggestions"].append(
                f"Use sf_get_schema to check available objects, or sf_describe_object('{close[0]}') for details."
            )
        return result

    # Check field names against known fields
    known_fields = _get_sf_field_names(matched_obj)
    if known_fields:
        all_query_fields = parsed["fields"] + parsed["where_fields"]
        for field in all_query_fields:
            # Skip relationship traversals (e.g. Contact__r.AccountId)
            if "." in field:
                continue
            # Skip aggregate/function results
            if field.startswith("(") or field.upper() in ("ID",):
                continue
            # Case-insensitive check
            field_lower = field.lower()
            known_lower = {f.lower(): f for f in known_fields}
            if field_lower not in known_lower:
                close = _fuzzy_suggest(field, known_fields, cutoff=0.4)
                if close:
                    result["valid"] = False
                    result["warnings"].append(
                        f"Field '{field}' not found on {matched_obj}. Did you mean: {', '.join(close)}?"
                    )
                    result["suggestions"].append(f"Replace '{field}' with '{close[0]}'")

    return result


# A MySQL-style row cap at the very end of the query (optionally 'LIMIT n
# OFFSET m', optional trailing semicolon). Only the trailing form is rewritten —
# a LIMIT buried mid-query (e.g. in a subquery) can't be safely translated, so
# it still blocks with the explanatory error in validate_suiteql.
_TRAILING_LIMIT_RE = re.compile(r"\bLIMIT\s+(\d+)(?:\s+OFFSET\s+(\d+))?\s*;?\s*$", re.IGNORECASE)


def rewrite_suiteql_limit(query: str) -> tuple[str, str | None]:
    """Rewrite a trailing MySQL-style 'LIMIT n [OFFSET m]' into Oracle syntax.

    SuiteQL is Oracle SQL, so 'LIMIT n' always 400s — but the intent of a
    *trailing* LIMIT is unambiguous, so instead of bouncing the query back we
    translate it to 'FETCH FIRST n ROWS ONLY' ('OFFSET m ROWS FETCH NEXT n ROWS
    ONLY' when an OFFSET is present) and tell the caller what happened.

    Returns (query, note): the possibly-rewritten query, and a human-readable
    note describing the rewrite (None when nothing was rewritten).
    """
    m = _TRAILING_LIMIT_RE.search(query)
    if not m:
        return query, None
    n, off = m.group(1), m.group(2)
    if off is not None:
        replacement = f"OFFSET {off} ROWS FETCH NEXT {n} ROWS ONLY"
    else:
        replacement = f"FETCH FIRST {n} ROWS ONLY"
    rewritten = query[: m.start()] + replacement
    note = (
        f"Rewrote trailing '{m.group(0).strip()}' to '{replacement}' — SuiteQL is "
        "Oracle SQL and does not support the 'LIMIT' keyword. Use "
        "'FETCH FIRST n ROWS ONLY' (or the tool's 'limit' parameter) directly "
        "next time."
    )
    return rewritten, note


def validate_suiteql(query: str) -> dict:
    """Validate a SuiteQL query against the curated NetSuite schema.

    Returns a dict with:
        valid: bool
        warnings: list[str]
        suggestions: list[str]
        blocking: bool — True only for findings so certain to 400 that the
            caller should not send the query to NetSuite at all. The other
            valid=False paths stay advisory: this validator is regex-based,
            not a SQL parser, so it can be wrong about unusual-but-valid SQL.
    """
    result = {"valid": True, "warnings": [], "suggestions": [], "blocking": False}

    # SuiteQL is Oracle SQL and rejects the MySQL/Postgres 'LIMIT n' clause with a
    # "syntax error ... near: LIMIT" 400. Flag a LIMIT keyword followed by a number
    # (word-boundary match, so a column like 'credit_limit' or 'limits' is not hit).
    # String literals are blanked first so data like LIKE '%LIMIT 50%' is not hit.
    scrubbed = re.sub(r"'(?:[^']|'')*'", "''", query)
    if re.search(r"\bLIMIT\s+\d+", scrubbed, re.IGNORECASE):
        result["valid"] = False
        result["blocking"] = True
        result["warnings"].append(
            "SuiteQL does not support the SQL 'LIMIT' keyword (it is Oracle SQL) — "
            "'LIMIT n' fails with a syntax error near 'LIMIT'."
        )
        result["suggestions"].append(
            "Use 'FETCH FIRST n ROWS ONLY' for a top-N (e.g. "
            "'... ORDER BY t.trandate DESC FETCH FIRST 50 ROWS ONLY'), or pass the "
            "ns_suiteql_query 'limit' parameter, which paginates server-side."
        )
        return result

    tables = _extract_suiteql_tables(query)
    if not tables:
        result["valid"] = False
        result["warnings"].append("Could not parse FROM/JOIN clause — missing table name.")
        return result

    metadata_hits = sorted({t for t in tables if t in _METADATA_TABLES})
    if metadata_hits:
        result["valid"] = False
        result["blocking"] = True
        result["warnings"].append(
            f"Metadata table(s) {', '.join(metadata_hits)} cannot be queried via SuiteQL "
            "in this account — they run on the legacy search engine with a restricted "
            "column set, so ordinary identifiers (id, label, fieldname) fail with "
            '"Unknown identifier" and FETCH FIRST is a syntax error there.'
        )
        result["suggestions"].append(
            "Use ns_get_record_schema(record_type=...) for field metadata and "
            "ns_list_record_types() for record-type metadata instead of SuiteQL."
        )
        return result

    ungranted_hits = sorted(
        {t for t in tables if t in _UNGRANTED_TABLES or t.startswith("customlist_")}
    )
    if ungranted_hits:
        result["valid"] = False
        result["blocking"] = True
        result["warnings"].append(
            f"Table(s) {', '.join(ungranted_hits)} are not queryable by this integration "
            "role — every query fails with \"Record 'X' was not found\" (the role lacks "
            "the Custom Lists permission and has no employee access, via SuiteQL or REST)."
        )
        result["suggestions"].append(
            "Do not JOIN these tables — resolve list/reference fields to display text "
            "with BUILTIN.DF() instead, e.g. BUILTIN.DF(c.category) AS category or "
            "BUILTIN.DF(c.salesrep) AS sales_rep on customer."
        )
        return result

    known_tables = set()
    for name, schema in NS_SCHEMA.items():
        known_tables.add(name.lower())
        known_tables.add(schema.get("suiteql_table", name).lower())
    known_tables.add("transactionline")

    for table in tables:
        if table not in known_tables:
            all_names = list(NS_SCHEMA.keys()) + ["transactionline"]
            close = _fuzzy_suggest(table, all_names, cutoff=0.4)
            if close:
                result["warnings"].append(
                    f"Table '{table}' not in curated schema. Did you mean: {', '.join(close)}?"
                )

    return result


def enhance_sf_error(error_message: str, soql: str) -> str:
    """Enhance a Salesforce API error message with actionable suggestions.

    Parses common error patterns and adds field/object suggestions.
    """
    suggestions = []

    # "No such column 'FieldName' on entity 'ObjectName'"
    col_match = re.search(
        r"No such column '(\w+)' on entity '(\w+)'", error_message, re.IGNORECASE
    )
    if col_match:
        bad_field, obj_name = col_match.group(1), col_match.group(2)
        known = _get_sf_field_names(obj_name)
        if known:
            close = _fuzzy_suggest(bad_field, known, cutoff=0.4)
            if close:
                suggestions.append(f"Did you mean: {', '.join(close)}?")
            suggestions.append(f"Use sf_get_schema(objects='{obj_name}') to see all known fields.")

    # "sObject type 'X' is not supported"
    obj_match = re.search(r"sObject type '(\w+)' is not supported", error_message, re.IGNORECASE)
    if obj_match:
        bad_obj = obj_match.group(1)
        close = _fuzzy_suggest(bad_obj, list(SF_SCHEMA.keys()), cutoff=0.4)
        if close:
            suggestions.append(f"Did you mean: {', '.join(close)}?")
        suggestions.append("Use sf_list_objects() to see all available objects.")

    if suggestions:
        return f"{error_message}\n\nSuggestions:\n" + "\n".join(f"  - {s}" for s in suggestions)
    return error_message


def enhance_ns_error(error_message: str, query: str) -> str:
    """Enhance a NetSuite API error message with actionable suggestions."""
    suggestions = []

    # "Invalid search column" or "field not found"
    field_match = re.search(
        r"(?:Invalid (?:search )?column|field not found)[:\s]*(\w+)", error_message, re.IGNORECASE
    )
    if field_match:
        bad_field = field_match.group(1)
        tables = _extract_suiteql_tables(query)
        for table in tables:
            known = _get_ns_field_names(table)
            if known:
                close = _fuzzy_suggest(bad_field, known, cutoff=0.4)
                if close:
                    suggestions.append(f"On '{table}': did you mean {', '.join(close)}?")

    # "Invalid table" or "table not found"
    table_match = re.search(
        r"(?:Invalid table|table not found)[:\s]*(\w+)", error_message, re.IGNORECASE
    )
    if table_match:
        bad_table = table_match.group(1)
        all_tables = list(NS_SCHEMA.keys()) + ["transactionline"]
        close = _fuzzy_suggest(bad_table, all_tables, cutoff=0.4)
        if close:
            suggestions.append(f"Did you mean: {', '.join(close)}?")
        suggestions.append("Use ns_get_netsuite_schema() to see known record types.")

    if suggestions:
        return f"{error_message}\n\nSuggestions:\n" + "\n".join(f"  - {s}" for s in suggestions)
    return error_message
