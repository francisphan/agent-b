"""Tests for query_validator module."""


from src.query_validator import (
    validate_soql,
    validate_suiteql,
    enhance_sf_error,
    enhance_ns_error,
)


class TestValidateSoql:
    def test_valid_query_passes(self):
        result = validate_soql("SELECT Id, Name FROM Account LIMIT 10")
        assert result["valid"] is True
        assert result["warnings"] == []

    def test_no_from_clause(self):
        result = validate_soql("SELECT Id, Name")
        assert result["valid"] is False
        assert any("FROM" in w for w in result["warnings"])

    def test_unknown_object_warns(self):
        result = validate_soql("SELECT Id FROM FakeObj__c LIMIT 1")
        # Unknown objects get a warning but not invalid (could be a real object not in curated schema)
        assert result["valid"] is True  # we don't block execution

    def test_misspelled_object_suggests(self):
        result = validate_soql("SELECT Id FROM Accountt LIMIT 1")
        assert any("Account" in w for w in result["warnings"])

    def test_wrong_field_suggests_correction(self):
        result = validate_soql(
            "SELECT Id, Emal__c FROM TVRS_Guest__c LIMIT 1"
        )
        assert result["valid"] is False
        assert any("Email__c" in w for w in result["warnings"])

    def test_relationship_traversal_not_flagged(self):
        result = validate_soql(
            "SELECT Id, Contact__r.AccountId FROM TVRS_Guest__c LIMIT 1"
        )
        # Relationship fields (dotted) should not be validated as bare fields
        assert result["valid"] is True

    def test_valid_where_clause(self):
        result = validate_soql(
            "SELECT Id, Email__c FROM TVRS_Guest__c WHERE Check_In_Date__c >= TODAY"
        )
        assert result["valid"] is True


class TestValidateSuiteql:
    def test_valid_query_passes(self):
        result = validate_suiteql("SELECT id, companyname FROM customer")
        assert result["valid"] is True
        assert result["warnings"] == []

    def test_no_from_clause(self):
        result = validate_suiteql("SELECT id, name")
        assert result["valid"] is False

    def test_unknown_table_warns(self):
        result = validate_suiteql("SELECT id FROM custommer")
        assert any("customer" in w.lower() for w in result["warnings"])

    def test_known_table_no_warning(self):
        result = validate_suiteql("SELECT id FROM transaction WHERE type = 'SalesOrd'")
        assert result["warnings"] == []

    def test_join_tables_validated(self):
        result = validate_suiteql(
            "SELECT t.id FROM transaction t JOIN transactionline tl ON t.id = tl.transaction"
        )
        assert result["valid"] is True
        assert result["warnings"] == []


class TestEnhanceSfError:
    def test_no_such_column_suggests_fix(self):
        error = "No such column 'Emal__c' on entity 'TVRS_Guest__c'"
        enhanced = enhance_sf_error(error, "SELECT Emal__c FROM TVRS_Guest__c")
        assert "Email__c" in enhanced
        assert "sf_get_schema" in enhanced

    def test_unknown_object_suggests(self):
        error = "sObject type 'Accountt' is not supported"
        enhanced = enhance_sf_error(error, "SELECT Id FROM Accountt")
        assert "Account" in enhanced

    def test_unrecognized_error_passes_through(self):
        error = "Some random error"
        enhanced = enhance_sf_error(error, "SELECT Id FROM Account")
        assert enhanced == error


class TestEnhanceNsError:
    def test_unrecognized_error_passes_through(self):
        error = "Some random error"
        enhanced = enhance_ns_error(error, "SELECT id FROM customer")
        assert enhanced == error

    def test_invalid_table_suggests(self):
        error = "Invalid table: custommer"
        enhanced = enhance_ns_error(error, "SELECT id FROM custommer")
        assert "customer" in enhanced
