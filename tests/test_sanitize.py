"""Tests for the sanitize module — the injection-safety layer every
string-interpolated SOQL/SuiteQL/SOSL query depends on."""

import pytest

from src.sanitize import (
    escape_soql,
    escape_soql_like,
    escape_sosl,
    escape_suiteql,
    escape_suiteql_like,
    validate_object_name,
    validate_path_segment,
    validate_sf_id,
)


class TestEscapeSoql:
    def test_plain_string_unchanged(self):
        assert escape_soql("John Smith") == "John Smith"

    def test_single_quote_backslash_escaped(self):
        assert escape_soql("O'Brien") == "O\\'Brien"

    def test_backslash_doubled_before_quote_escape(self):
        # A trailing backslash must not be able to neutralize the closing quote.
        assert escape_soql("evil\\") == "evil\\\\"
        assert escape_soql("a\\'b") == "a\\\\\\'b"

    def test_control_characters_escaped(self):
        assert escape_soql("a\nb\tc") == "a\\nb\\tc"

    def test_null_byte_stripped(self):
        assert escape_soql("a\0b") == "ab"

    def test_wildcards_passed_through(self):
        # Equality context: % and _ are literal, no escaping needed.
        assert escape_soql("100%_done") == "100%_done"


class TestEscapeSoqlLike:
    def test_wildcards_escaped(self):
        assert escape_soql_like("100%") == "100\\%"
        assert escape_soql_like("a_b") == "a\\_b"

    def test_quote_still_escaped(self):
        assert escape_soql_like("O'%") == "O\\'\\%"


class TestEscapeSuiteql:
    def test_plain_string_unchanged(self):
        assert escape_suiteql("John Smith") == "John Smith"

    def test_single_quote_doubled(self):
        assert escape_suiteql("O'Brien") == "O''Brien"

    def test_backslash_preserved(self):
        # Oracle treats backslash literally in string literals; doubling it
        # would make equality comparisons silently miss (e.g. LOWER(email) = ...).
        assert escape_suiteql("a\\b") == "a\\b"

    def test_null_byte_stripped(self):
        assert escape_suiteql("a\0b") == "ab"

    def test_injection_attempt_neutralized(self):
        # The classic close-quote-and-inject payload stays inside the literal.
        assert escape_suiteql("' OR 1=1 --") == "'' OR 1=1 --"


class TestEscapeSuiteqlLike:
    def test_wildcards_escaped(self):
        assert escape_suiteql_like("100%") == "100\\%"
        assert escape_suiteql_like("a_b") == "a\\_b"

    def test_quote_still_doubled(self):
        assert escape_suiteql_like("O'%") == "O''\\%"

    def test_backslash_doubled_for_escape_clause(self):
        # With ESCAPE '\' declared, a literal backslash must arrive as \\.
        assert escape_suiteql_like("a\\b") == "a\\\\b"

    def test_match_everything_payload_neutralized(self):
        # A bare "%" anchor must not become a match-everything LIKE pattern.
        assert escape_suiteql_like("%") == "\\%"


class TestEscapeSosl:
    def test_reserved_characters_escaped(self):
        assert escape_sosl("a?b") == "a\\?b"
        assert escape_sosl("x AND y") == "x AND y"
        assert escape_sosl("O'Brien-Smith") == "O\\'Brien\\-Smith"

    def test_braces_escaped(self):
        # Braces could otherwise close the FIND {...} clause.
        assert escape_sosl("a}b{c") == "a\\}b\\{c"


class TestValidateSfId:
    def test_valid_15_and_18_char_ids(self):
        assert validate_sf_id("001000000000001") == "001000000000001"
        assert validate_sf_id("001000000000001AAA") == "001000000000001AAA"

    @pytest.mark.parametrize(
        "bad", ["", "short", "001000000000001'", "0010000000000010", "x" * 19]
    )
    def test_invalid_ids_rejected(self, bad):
        with pytest.raises(ValueError):
            validate_sf_id(bad)


class TestValidateObjectName:
    def test_valid_names(self):
        assert validate_object_name("Account") == "Account"
        assert validate_object_name("TVRS_Guest__c") == "TVRS_Guest__c"

    @pytest.mark.parametrize("bad", ["", "1Account", "Account; DROP", "a b", "a" * 101])
    def test_invalid_names_rejected(self, bad):
        with pytest.raises(ValueError):
            validate_object_name(bad)


class TestValidatePathSegment:
    def test_valid_segments(self):
        assert validate_path_segment("user@example.com") == "user@example.com"
        assert validate_path_segment("rec-123_x") == "rec-123_x"

    @pytest.mark.parametrize("bad", ["", "..", "a/../b", "a/b", "a b", "a?b"])
    def test_traversal_and_unsafe_rejected(self, bad):
        with pytest.raises(ValueError):
            validate_path_segment(bad)
