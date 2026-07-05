"""Tests for ns_schema module and ns_get_netsuite_schema tool."""

import pytest

from src.ns_schema import (
    SCHEMA,
    RECORD_TYPE_NAMES,
    SUITEQL_TABLES,
    GLOBAL_TIPS,
    get_schema,
)


class TestNsSchemaModule:
    """Tests for the ns_schema module directly."""

    def test_schema_has_expected_record_types(self):
        expected = {
            "customer",
            "salesOrder",
            "invoice",
            "item",
            "transaction",
            "vendor",
            "employee",
            "contact",
        }
        assert expected == set(SCHEMA.keys())

    def test_record_type_names_sorted(self):
        assert RECORD_TYPE_NAMES == sorted(RECORD_TYPE_NAMES)

    def test_customer_suiteql_uses_real_balance_column(self):
        """'balance' is a REST-only field; the SuiteQL column is 'balancesearch'.
        A bare 'balance' identifier 400s (verified against the live endpoint) —
        and it broke guest_360's whole NetSuite section in prod."""
        cust = SCHEMA["customer"]
        assert "balancesearch" in cust["suiteql_fields"]
        assert "balance" not in cust["suiteql_fields"]
        for example in cust["example_suiteql"]:
            # Aliasing balancesearch AS balance is fine; selecting/filtering on a
            # bare `balance` source column is the regression.
            cleaned = example.replace("AS balance", "")
            assert " balance " not in f" {cleaned} ".replace(",", " , "), example

    def test_each_record_has_required_keys(self):
        for name, obj in SCHEMA.items():
            assert "label" in obj, f"{name} missing label"
            assert "description" in obj, f"{name} missing description"
            assert "key_fields" in obj, f"{name} missing key_fields"
            assert "example_suiteql" in obj, f"{name} missing example_suiteql"

    def test_each_record_has_suiteql_table(self):
        for name, obj in SCHEMA.items():
            # suiteql_table is optional if the table name matches the key
            table = obj.get("suiteql_table", name)
            assert isinstance(table, str)

    def test_suiteql_tables_mapping(self):
        assert "customer" in SUITEQL_TABLES
        assert "transaction" in SUITEQL_TABLES
        assert "transactionline" in SUITEQL_TABLES

    def test_get_schema_full(self):
        result = get_schema()
        assert result is SCHEMA

    def test_get_schema_single(self):
        result = get_schema("customer")
        assert "customer" in result
        assert len(result) == 1

    def test_get_schema_case_insensitive(self):
        result = get_schema("Customer")
        assert "customer" in result

    def test_get_schema_unknown_raises(self):
        with pytest.raises(KeyError, match="not found in curated schema"):
            get_schema("fakeRecord")


class TestNsGlobalTips:
    """The GLOBAL_TIPS block mirrors opera_schema.GLOBAL_TIPS (flat list of str)."""

    def test_is_nonempty_list_of_str(self):
        assert isinstance(GLOBAL_TIPS, list)
        assert GLOBAL_TIPS
        assert all(isinstance(tip, str) for tip in GLOBAL_TIPS)

    def test_covers_limit_fetch_first(self):
        joined = " ".join(GLOBAL_TIPS)
        assert "LIMIT" in joined
        assert "FETCH FIRST" in joined

    def test_covers_known_gotchas(self):
        joined = " ".join(GLOBAL_TIPS)
        assert "internalid" in joined  # id, not internalid
        assert "balancesearch" in joined  # balance gotcha
        assert "companyname" in joined  # customer has no 'name' column


class TestNsGetNetsuiteSchema:
    """Tests for the ns_get_netsuite_schema tool function."""

    @pytest.fixture
    def get_schema_tool(self):
        from unittest.mock import MagicMock

        mcp = MagicMock()
        captured = {}

        def capture_tool():
            def decorator(fn):
                captured[fn.__name__] = fn
                return fn

            return decorator

        mcp.tool = capture_tool
        from src.ns_tools import register_tools

        register_tools(mcp)
        return captured["ns_get_netsuite_schema"]

    def test_returns_full_schema_when_empty(self, get_schema_tool):
        result = get_schema_tool(record_types="")
        # Full schema plus a cross-table tips block (mirrors opera schema tool).
        assert set(SCHEMA.keys()).issubset(result.keys())
        assert result["_tips"] == GLOBAL_TIPS

    def test_returns_filtered_single(self, get_schema_tool):
        result = get_schema_tool(record_types="customer")
        assert "customer" in result
        assert set(result.keys()) == {"customer", "_tips"}

    def test_returns_filtered_multiple(self, get_schema_tool):
        result = get_schema_tool(record_types="customer,salesOrder,invoice")
        assert set(result.keys()) == {"customer", "salesOrder", "invoice", "_tips"}

    def test_tips_included_in_filtered_result(self, get_schema_tool):
        result = get_schema_tool(record_types="customer")
        assert result["_tips"] == GLOBAL_TIPS

    def test_unknown_silently_omitted(self, get_schema_tool):
        result = get_schema_tool(record_types="fakeType")
        # No record types match, but the tips block is always present.
        assert set(result.keys()) == {"_tips"}

    def test_mixed_known_and_unknown(self, get_schema_tool):
        result = get_schema_tool(record_types="customer,fakeType,item")
        assert set(result.keys()) == {"customer", "item", "_tips"}

    def test_case_insensitive(self, get_schema_tool):
        result = get_schema_tool(record_types="CUSTOMER,SalesOrder")
        assert "customer" in result
        assert "salesOrder" in result
