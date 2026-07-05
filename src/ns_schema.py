"""Curated NetSuite schema for The Vines of Mendoza.

Mirrors sf_schema.py — provides a SCHEMA dict documenting the most useful fields,
relationships, and SuiteQL patterns for key NetSuite record types. Used by the
ns_get_schema MCP tool to help AI agents write correct SuiteQL without needing
to call ns_get_record_schema or ns_list_record_types first.

Record types covered:
  - customer       (guests, wine club members, accounts)
  - salesOrder     (wine orders, experience packages)
  - invoice        (billing records)
  - item           (wine products, experiences, services)
  - transaction    (unified transaction view for SuiteQL)
  - vendor         (wineries, suppliers)
  - employee       (staff records)
  - contact        (contacts linked to companies)
"""

SCHEMA: dict[str, dict] = {
    "customer": {
        "label": "Customer",
        "description": (
            "Core record for guests, wine club members, and business accounts. "
            "Maps to Salesforce Account/Contact. Use email or externalId for "
            "cross-system lookups. isPerson=true for individuals."
        ),
        "geo_filtering_note": (
            "There is NO usable state/region field on the customer record via SuiteQL, "
            "and the address sub-records are not reachable for this read-only user: "
            "customerAddressbook / customerAddressbookEntityAddress return zero rows, "
            "and the Address table 400s. Do NOT try to join them or filter on a 'state' "
            "column (it does not exist). The only geography on customer is "
            "'thirdpartycountry' (a country code like 'US'). "
            "To list/filter people by US state or region, query SALESFORCE instead: "
            "Account.BillingState (stored as the FULL name, e.g. 'Texas', not 'TX'). "
            "If you then need NetSuite owner/wine data for those people, look each up by "
            "email in customer (LOWER(email) = ...)."
        ),
        "rest_type": "customer",
        "key_fields": [
            {"name": "id", "type": "integer", "note": "Internal ID"},
            {"name": "entityId", "type": "string", "note": "Display name/number"},
            {"name": "companyName", "type": "string", "note": "Company or full name"},
            {"name": "firstName", "type": "string"},
            {"name": "lastName", "type": "string"},
            {"name": "email", "type": "string"},
            {"name": "phone", "type": "string"},
            {"name": "isPerson", "type": "boolean", "note": "True for individuals"},
            {"name": "category", "type": "reference", "note": "Customer category"},
            {"name": "subsidiary", "type": "reference", "note": "Subsidiary (multi-sub orgs)"},
            {"name": "salesRep", "type": "reference", "note": "Assigned sales rep (employee)"},
            {"name": "status", "type": "string", "note": "Customer status"},
            {"name": "dateCreated", "type": "datetime"},
            {"name": "lastModifiedDate", "type": "datetime"},
            {"name": "externalId", "type": "string", "note": "External ID for integrations"},
            {
                "name": "balance",
                "type": "currency",
                "note": "Outstanding balance. REST field only — in SuiteQL the column "
                "is 'balancesearch' (use 'balancesearch AS balance'); a bare "
                "'balance' identifier is a 400. Overdue portion: "
                "'overduebalancesearch'.",
            },
            {
                "name": "thirdpartycountry",
                "type": "string",
                "note": "Country code (e.g. 'US') — the ONLY geo field on customer. "
                "No state/region is available here; see geo_filtering_note.",
            },
            {
                "name": "custentity_vom_ownercode",
                "type": "string",
                "note": "Vineyard owner code (e.g. 'BOYH'); non-null marks a wine/vineyard owner.",
            },
        ],
        "suiteql_table": "customer",
        "suiteql_fields": [
            "id",
            "entityid",
            "companyname",
            "firstname",
            "lastname",
            "email",
            "phone",
            "isperson",
            "datecreated",
            "lastmodifieddate",
            "externalid",
            "balancesearch",
            "overduebalancesearch",
            "subsidiary",
            "salesrep",
            "thirdpartycountry",
            "custentity_vom_ownercode",
        ],
        "example_suiteql": [
            (
                "SELECT id, entityid, companyname, firstname, lastname, email, phone, "
                "isperson, balancesearch AS balance "
                "FROM customer "
                "WHERE email IS NOT NULL "
                "ORDER BY lastmodifieddate DESC"
            ),
            (
                "SELECT id, entityid, email, balancesearch AS balance "
                "FROM customer "
                "WHERE LOWER(email) = 'guest@example.com'"
            ),
            (
                "SELECT id, companyname, email, balancesearch AS balance "
                "FROM customer "
                "WHERE isperson = 'T' AND balancesearch > 0 "
                "ORDER BY balancesearch DESC"
            ),
        ],
    },
    "salesOrder": {
        "label": "Sales Order",
        "description": (
            "Sales orders for wine purchases, experience packages, and other products. "
            "Linked to customer via entity field. Line items are in the 'item' sublist."
        ),
        "rest_type": "salesOrder",
        "key_fields": [
            {"name": "id", "type": "integer", "note": "Internal ID"},
            {"name": "tranId", "type": "string", "note": "Transaction number (e.g. SO-12345)"},
            {"name": "entity", "type": "reference", "note": "Customer reference"},
            {"name": "tranDate", "type": "date", "note": "Transaction date"},
            {"name": "status", "type": "string"},
            {"name": "total", "type": "currency"},
            {"name": "subtotal", "type": "currency"},
            {"name": "memo", "type": "string"},
            {"name": "subsidiary", "type": "reference"},
            {"name": "salesRep", "type": "reference"},
            {"name": "shipDate", "type": "date"},
            {"name": "shipMethod", "type": "reference"},
            {"name": "createdDate", "type": "datetime"},
            {"name": "lastModifiedDate", "type": "datetime"},
        ],
        "suiteql_table": "transaction",
        "suiteql_note": (
            "SuiteQL queries against 'transaction' table with type = 'SalesOrd'. "
            "Join to transactionline for line items."
        ),
        "example_suiteql": [
            (
                "SELECT t.id, t.tranid, t.trandate, t.status, "
                "t.foreigntotal, c.companyname, c.email "
                "FROM transaction t "
                "JOIN customer c ON t.entity = c.id "
                "WHERE t.type = 'SalesOrd' "
                "ORDER BY t.trandate DESC"
            ),
            (
                "SELECT t.id, t.tranid, tl.item, i.itemid, tl.quantity, tl.rate, tl.amount "
                "FROM transaction t "
                "JOIN transactionline tl ON t.id = tl.transaction "
                "JOIN item i ON tl.item = i.id "
                "WHERE t.type = 'SalesOrd' AND t.id = 12345"
            ),
        ],
    },
    "invoice": {
        "label": "Invoice",
        "description": (
            "Billing records linked to customers. Created from sales orders or standalone. "
            "Query via transaction table with type = 'CustInvc'."
        ),
        "rest_type": "invoice",
        "key_fields": [
            {"name": "id", "type": "integer"},
            {"name": "tranId", "type": "string", "note": "Invoice number"},
            {"name": "entity", "type": "reference", "note": "Customer"},
            {"name": "tranDate", "type": "date"},
            {"name": "dueDate", "type": "date"},
            {"name": "status", "type": "string"},
            {"name": "total", "type": "currency"},
            {"name": "amountRemaining", "type": "currency"},
            {"name": "memo", "type": "string"},
            {"name": "createdDate", "type": "datetime"},
        ],
        "suiteql_table": "transaction",
        "suiteql_note": "Use type = 'CustInvc' to filter invoices in the transaction table.",
        "example_suiteql": [
            (
                "SELECT t.id, t.tranid, t.trandate, t.duedate, t.status, "
                "t.foreigntotal, t.foreignamountremaining, c.companyname "
                "FROM transaction t "
                "JOIN customer c ON t.entity = c.id "
                "WHERE t.type = 'CustInvc' "
                "ORDER BY t.trandate DESC"
            ),
            (
                "SELECT t.id, t.tranid, t.foreignamountremaining "
                "FROM transaction t "
                "WHERE t.type = 'CustInvc' AND t.foreignamountremaining > 0 "
                "ORDER BY t.duedate ASC"
            ),
        ],
    },
    "item": {
        "label": "Item (Product)",
        "description": (
            "Products including wines, experiences, and services. "
            "Various subtypes: inventoryItem, nonInventoryItem, serviceItem, etc. "
            "SuiteQL table is 'item' which unions all subtypes."
        ),
        "rest_type": "inventoryItem",
        "key_fields": [
            {"name": "id", "type": "integer"},
            {"name": "itemId", "type": "string", "note": "Display name / SKU"},
            {"name": "displayName", "type": "string"},
            {"name": "description", "type": "string"},
            {
                "name": "type",
                "type": "string",
                "note": "Item type (Inventory, NonInventory, Service, etc.)",
            },
            {"name": "basePrice", "type": "currency"},
            {"name": "cost", "type": "currency"},
            {
                "name": "quantityAvailable",
                "type": "number",
                "note": "Stock on hand (inventory items)",
            },
            {"name": "quantityOnOrder", "type": "number"},
            {"name": "isInactive", "type": "boolean"},
            {"name": "parent", "type": "reference", "note": "Parent item (for matrix items)"},
            {"name": "class", "type": "reference", "note": "Classification"},
        ],
        "suiteql_table": "item",
        "example_suiteql": [
            (
                "SELECT id, itemid, displayname, description, type, baseprice "
                "FROM item "
                "WHERE isinactive = 'F' "
                "ORDER BY itemid"
            ),
            (
                "SELECT id, itemid, displayname, quantityavailable, quantityonorder "
                "FROM item "
                "WHERE type = 'InvtPart' AND quantityavailable > 0 "
                "ORDER BY quantityavailable DESC"
            ),
        ],
    },
    "transaction": {
        "label": "Transaction (Unified)",
        "description": (
            "Unified transaction table in SuiteQL — all transaction types accessible here. "
            "Filter by 'type' column: SalesOrd, CustInvc, CustPymt, VendBill, PurchOrd, etc. "
            "Join to transactionline for line-level detail."
        ),
        "key_fields": [
            {"name": "id", "type": "integer"},
            {"name": "tranId", "type": "string", "note": "Document number"},
            {
                "name": "type",
                "type": "string",
                "note": "SalesOrd, CustInvc, CustPymt, VendBill, etc.",
            },
            {"name": "entity", "type": "reference", "note": "Customer/Vendor"},
            {"name": "tranDate", "type": "date"},
            {"name": "status", "type": "string"},
            {"name": "foreignTotal", "type": "currency"},
            {"name": "memo", "type": "string"},
            {"name": "subsidiary", "type": "reference"},
            {"name": "createdDate", "type": "datetime"},
            {"name": "lastModifiedDate", "type": "datetime"},
        ],
        "suiteql_table": "transaction",
        "type_codes": {
            "SalesOrd": "Sales Order",
            "CustInvc": "Invoice",
            "CustPymt": "Customer Payment",
            "CustCred": "Credit Memo",
            "VendBill": "Vendor Bill",
            "PurchOrd": "Purchase Order",
            "Journal": "Journal Entry",
            "Estimate": "Estimate/Quote",
        },
        "example_suiteql": [
            (
                "SELECT id, tranid, type, trandate, status, foreigntotal, memo "
                "FROM transaction "
                "WHERE trandate >= TO_DATE('2025-01-01', 'YYYY-MM-DD') "
                "ORDER BY trandate DESC"
            ),
            (
                "SELECT type, COUNT(*) AS cnt, SUM(foreigntotal) AS total "
                "FROM transaction "
                "WHERE trandate >= TO_DATE('2025-01-01', 'YYYY-MM-DD') "
                "GROUP BY type "
                "ORDER BY total DESC"
            ),
        ],
    },
    "vendor": {
        "label": "Vendor",
        "description": (
            "Wineries, suppliers, and service providers. "
            "Linked to vendor bills and purchase orders via entity field."
        ),
        "rest_type": "vendor",
        "key_fields": [
            {"name": "id", "type": "integer"},
            {"name": "entityId", "type": "string"},
            {"name": "companyName", "type": "string"},
            {"name": "email", "type": "string"},
            {"name": "phone", "type": "string"},
            {"name": "subsidiary", "type": "reference"},
            {"name": "balance", "type": "currency"},
            {"name": "isInactive", "type": "boolean"},
        ],
        "suiteql_table": "vendor",
        "example_suiteql": [
            (
                "SELECT id, entityid, companyname, email, phone, balance "
                "FROM vendor "
                "WHERE isinactive = 'F' "
                "ORDER BY companyname"
            ),
        ],
    },
    "employee": {
        "label": "Employee",
        "description": "Staff records. Referenced as salesRep on customers and transactions.",
        "rest_type": "employee",
        "key_fields": [
            {"name": "id", "type": "integer"},
            {"name": "entityId", "type": "string"},
            {"name": "firstName", "type": "string"},
            {"name": "lastName", "type": "string"},
            {"name": "email", "type": "string"},
            {"name": "title", "type": "string"},
            {"name": "department", "type": "reference"},
            {"name": "supervisor", "type": "reference"},
            {"name": "isInactive", "type": "boolean"},
        ],
        "suiteql_table": "employee",
        "example_suiteql": [
            (
                "SELECT id, entityid, firstname, lastname, email, title "
                "FROM employee "
                "WHERE isinactive = 'F' "
                "ORDER BY lastname, firstname"
            ),
        ],
    },
    "contact": {
        "label": "Contact",
        "description": (
            "Contacts linked to customer or vendor records via the company field. "
            "Separate from customer — used for multi-contact companies."
        ),
        "rest_type": "contact",
        "key_fields": [
            {"name": "id", "type": "integer"},
            {"name": "entityId", "type": "string"},
            {"name": "firstName", "type": "string"},
            {"name": "lastName", "type": "string"},
            {"name": "email", "type": "string"},
            {"name": "phone", "type": "string"},
            {"name": "company", "type": "reference", "note": "Linked customer or vendor"},
            {"name": "title", "type": "string"},
            {"name": "isInactive", "type": "boolean"},
        ],
        "suiteql_table": "contact",
        "example_suiteql": [
            (
                "SELECT c.id, c.firstname, c.lastname, c.email, c.phone, "
                "cust.companyname AS company "
                "FROM contact c "
                "LEFT JOIN customer cust ON c.company = cust.id "
                "WHERE c.isinactive = 'F' "
                "ORDER BY c.lastname"
            ),
        ],
    },
}

# Cross-table SuiteQL guidance surfaced by the ns_get_netsuite_schema tool.
# Mirrors opera_schema.GLOBAL_TIPS (a flat list of plain-string tips). These are
# the gotchas that make LLM-authored SuiteQL 400 against the live endpoint.
GLOBAL_TIPS: list[str] = [
    "Row limiting: SuiteQL (Oracle) does NOT support the MySQL-style 'LIMIT n' "
    "clause — it 400s with a syntax error near 'LIMIT'. Use "
    "'FETCH FIRST n ROWS ONLY' for a top-N in the SQL (optionally after "
    "'OFFSET n ROWS'), or pass the tool's 'limit' parameter (which paginates "
    "server-side).",
    "The row id column is 'id', NOT 'internalid' — a bare 'internalid' 400s.",
    "Customer balance is 'balancesearch' (overdue portion 'overduebalancesearch'), "
    "NOT 'balance'. Alias it as 'balancesearch AS balance'.",
    "The customer table has no 'name' column — use entityid / companyname / firstname / lastname.",
    "All transaction types share the 'transaction' table — filter by the 'type' "
    "column (SalesOrd, CustInvc, CustPymt, VendBill, PurchOrd, Journal).",
    "Booleans compare against 'T'/'F' strings, not true/false.",
    "String compares are case-sensitive — wrap with LOWER() for case-insensitive "
    "matching. Dates: TO_DATE('2025-01-01', 'YYYY-MM-DD'), SYSDATE.",
]


# Convenience lookups
RECORD_TYPE_NAMES = sorted(SCHEMA.keys())

# SuiteQL table-to-record-type mapping for validation
SUITEQL_TABLES = {entry.get("suiteql_table", name): name for name, entry in SCHEMA.items()}
# Also add transactionline as a known table
SUITEQL_TABLES["transactionline"] = "transactionline"


def get_schema(record_type: str | None = None) -> dict:
    """Return schema for a specific record type, or the full SCHEMA dict.

    Args:
        record_type: REST record type name (e.g. "customer", "salesOrder").
                     Case-insensitive. If None, returns entire schema.

    Returns:
        Schema dict for the requested record type, or full schema.

    Raises:
        KeyError: If record_type not found in curated schema.
    """
    if record_type is None:
        return SCHEMA

    for name, schema in SCHEMA.items():
        if name.lower() == record_type.lower():
            return {name: schema}

    available = ", ".join(RECORD_TYPE_NAMES)
    raise KeyError(
        f"Record type '{record_type}' not found in curated schema. "
        f"Available: {available}. "
        f"Use ns_get_record_schema for types not in this list."
    )
