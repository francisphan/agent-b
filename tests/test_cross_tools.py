"""Tests for cross-system lookup tools."""

from unittest.mock import MagicMock, patch



class TestCrossToolRegistration:
    """Test that cross-system tools register correctly."""

    def test_registers_two_tools(self):
        mcp = MagicMock()
        captured = {}

        def capture_tool():
            def decorator(fn):
                captured[fn.__name__] = fn
                return fn
            return decorator

        mcp.tool = capture_tool
        from src.cross_tools import register_tools
        register_tools(mcp)

        assert "lookup_guest_by_email" in captured
        assert "guest_360_profile" in captured


class TestLookupGuest:
    @patch("src.cross_tools.query_prospects")
    @patch("src.cross_tools.suiteql_query")
    @patch("src.cross_tools.sf_query")
    def test_returns_all_three_systems(self, mock_sf, mock_ns, mock_pardot):
        mock_sf.return_value = []
        mock_ns.return_value = []
        mock_pardot.return_value = {"values": []}

        from src.cross_tools import lookup_guest
        result = lookup_guest("Test@Example.com")

        assert result["email"] == "test@example.com"
        assert "salesforce" in result
        assert "netsuite" in result
        assert "pardot" in result

    @patch("src.cross_tools.query_prospects")
    @patch("src.cross_tools.suiteql_query")
    @patch("src.cross_tools.sf_query")
    def test_handles_sf_error_gracefully(self, mock_sf, mock_ns, mock_pardot):
        mock_sf.side_effect = Exception("SF down")
        mock_ns.return_value = []
        mock_pardot.return_value = {"values": []}

        from src.cross_tools import lookup_guest
        result = lookup_guest("test@example.com")

        assert "error" in result["salesforce"]
        assert result["netsuite"] is not None
        assert result["pardot"] is not None

    @patch("src.cross_tools.query_prospects")
    @patch("src.cross_tools.suiteql_query")
    @patch("src.cross_tools.sf_query")
    def test_handles_ns_error_gracefully(self, mock_sf, mock_ns, mock_pardot):
        mock_sf.return_value = []
        mock_ns.side_effect = Exception("NS down")
        mock_pardot.return_value = {"values": []}

        from src.cross_tools import lookup_guest
        result = lookup_guest("test@example.com")

        assert result["salesforce"] is not None
        assert "error" in result["netsuite"]


class TestGuest360:
    @patch("src.cross_tools.query_prospects")
    @patch("src.cross_tools.suiteql_query")
    @patch("src.cross_tools.sf_query")
    def test_builds_unified_profile(self, mock_sf, mock_ns, mock_pardot):
        mock_sf.side_effect = [
            # First call: guest stays
            [
                {
                    "Id": "a01xx",
                    "Guest_First_Name__c": "John",
                    "Guest_Last_Name__c": "Doe",
                    "Email__c": "john@example.com",
                    "Check_In_Date__c": "2025-06-01",
                    "Check_Out_Date__c": "2025-06-05",
                    "Villa_number__c": "V12",
                    "City__c": "New York",
                    "Country__c": "USA",
                    "Language__c": "English",
                    "Comments__c": "VIP",
                    "Contact__c": "003xx",
                    "Contact__r": {"AccountId": "001xx"},
                },
            ],
            # Second call: opportunities
            [{"Id": "006xx", "Name": "Villa Sale", "StageName": "Negotiation", "Amount": 50000}],
        ]
        mock_ns.side_effect = [
            # First call: customer
            [{"id": 123, "entityid": "CUST-123", "email": "john@example.com",
              "firstname": "John", "lastname": "Doe", "balance": 1500}],
            # Second call: transactions
            [{"id": 456, "tranid": "INV-001", "type": "CustInvc",
              "trandate": "2025-05-01", "foreigntotal": 1500}],
        ]
        mock_pardot.return_value = {
            "values": [
                {"id": 789, "email": "john@example.com", "score": 85,
                 "grade": "A", "lastActivityAt": "2025-05-15"},
            ]
        }

        from src.cross_tools import guest_360
        profile = guest_360("john@example.com")

        assert profile["identity"]["first_name"] == "John"
        assert profile["identity"]["last_name"] == "Doe"
        assert len(profile["stays"]) == 1
        assert profile["stays"][0]["villa"] == "V12"
        assert profile["system_ids"]["sf_contact_id"] == "003xx"
        assert profile["system_ids"]["ns_customer_id"] == 123
        assert profile["system_ids"]["pardot_prospect_id"] == 789
        assert profile["financials"]["ns_balance"] == 1500
        assert profile["marketing"]["pardot_score"] == 85

    @patch("src.cross_tools.query_prospects")
    @patch("src.cross_tools.suiteql_query")
    @patch("src.cross_tools.sf_query")
    def test_partial_failure_still_returns(self, mock_sf, mock_ns, mock_pardot):
        mock_sf.side_effect = Exception("SF timeout")
        mock_ns.return_value = []
        mock_pardot.return_value = {"values": []}

        from src.cross_tools import guest_360
        profile = guest_360("test@example.com")

        assert "_errors" in profile
        assert any("Salesforce" in e for e in profile["_errors"])
        # Other sections should still be populated
        assert profile["netsuite" if "netsuite" in profile else "financials"] is not None


class TestPardotDisabled:
    """With PARDOT_TOOLS_ENABLED off, the composites' Pardot leg short-circuits at
    the client (no HTTP/auth) and the composites degrade gracefully. Here the real
    query_prospects runs — only Salesforce/NetSuite are mocked — so the client-level
    guard is exercised end-to-end through the composite."""

    @patch("src.cross_tools.suiteql_query")
    @patch("src.cross_tools.sf_query")
    def test_guest_360_notes_pardot_disabled(self, mock_sf, mock_ns, monkeypatch):
        monkeypatch.setenv("PARDOT_TOOLS_ENABLED", "false")
        mock_sf.return_value = []
        mock_ns.return_value = []

        from src.cross_tools import guest_360
        profile = guest_360("test@example.com")

        assert "_errors" in profile
        assert any("Pardot" in e and "disabled" in e for e in profile["_errors"])
        # The rest of the profile is still returned intact.
        assert profile["email"] == "test@example.com"
        assert "marketing" in profile

    @patch("src.cross_tools.suiteql_query")
    @patch("src.cross_tools.sf_query")
    def test_lookup_guest_reports_pardot_disabled(self, mock_sf, mock_ns, monkeypatch):
        monkeypatch.setenv("PARDOT_TOOLS_ENABLED", "false")
        mock_sf.return_value = []
        mock_ns.return_value = []

        from src.cross_tools import lookup_guest
        result = lookup_guest("test@example.com")

        assert "error" in result["pardot"]
        assert "disabled" in result["pardot"]["error"]
        # Other systems are unaffected.
        assert result["salesforce"] is not None
        assert result["netsuite"] is not None
