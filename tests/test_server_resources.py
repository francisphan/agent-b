"""Tests for MCP resources and prompts registered in server.py."""

import json

from src.sf_schema import SCHEMA as SF_SCHEMA
from src.ns_schema import SCHEMA as NS_SCHEMA


class TestResources:
    def test_salesforce_schema_resource(self):
        from src.server import salesforce_schema_resource
        result = json.loads(salesforce_schema_resource())
        assert set(result.keys()) == set(SF_SCHEMA.keys())

    def test_netsuite_schema_resource(self):
        from src.server import netsuite_schema_resource
        result = json.loads(netsuite_schema_resource())
        assert set(result.keys()) == set(NS_SCHEMA.keys())

    def test_salesforce_object_resource_found(self):
        from src.server import salesforce_object_resource
        result = json.loads(salesforce_object_resource("Account"))
        assert "Account" in result

    def test_salesforce_object_resource_case_insensitive(self):
        from src.server import salesforce_object_resource
        result = json.loads(salesforce_object_resource("account"))
        assert "Account" in result

    def test_salesforce_object_resource_not_found(self):
        from src.server import salesforce_object_resource
        result = json.loads(salesforce_object_resource("FakeObj"))
        assert "error" in result

    def test_netsuite_record_resource_found(self):
        from src.server import netsuite_record_resource
        result = json.loads(netsuite_record_resource("customer"))
        assert "customer" in result

    def test_netsuite_record_resource_not_found(self):
        from src.server import netsuite_record_resource
        result = json.loads(netsuite_record_resource("fakeType"))
        assert "error" in result

    def test_query_patterns_resource(self):
        from src.server import query_patterns_resource
        result = json.loads(query_patterns_resource())
        assert "soql_tips" in result
        assert "suiteql_tips" in result
        assert "cross_system" in result

    def test_suiteql_tips_include_row_limiting(self):
        from src.server import query_patterns_resource
        result = json.loads(query_patterns_resource())
        tips = " ".join(result["suiteql_tips"])
        # SuiteQL LIMIT keyword is invalid — the guide must steer to FETCH FIRST.
        assert "FETCH FIRST" in tips
        assert "LIMIT" in tips


class TestPrompts:
    def test_guest_arrivals_prompt(self):
        from src.server import guest_arrivals
        result = guest_arrivals()
        assert "TVRS_Guest__c" in result
        assert "THIS_WEEK" in result

    def test_sales_pipeline_prompt(self):
        from src.server import sales_pipeline
        result = sales_pipeline()
        assert "Opportunity" in result
        assert "StageName" in result

    def test_guest_lookup_prompt(self):
        from src.server import guest_lookup
        result = guest_lookup(email="test@example.com")
        assert "test@example.com" in result
        assert "guest_360_profile" in result

    def test_netsuite_revenue_prompt(self):
        from src.server import netsuite_revenue
        result = netsuite_revenue()
        assert "transaction" in result

    def test_stale_opportunities_prompt(self):
        from src.server import stale_opportunities
        result = stale_opportunities()
        assert "LAST_N_DAYS:30" in result

    def test_outstanding_invoices_prompt(self):
        from src.server import outstanding_invoices
        result = outstanding_invoices()
        assert "CustInvc" in result
