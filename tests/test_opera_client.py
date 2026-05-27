"""Tests for the OPERA read-only SQL guard and schema helpers."""

from unittest.mock import patch

import oracledb
import pytest

from src import opera_client
from src.opera_client import assert_read_only, query


class TestAssertReadOnly:
    def test_simple_select_passes(self):
        assert_read_only("SELECT 1 FROM DUAL")

    def test_select_with_named_binds_passes(self):
        assert_read_only(
            "SELECT NAME_ID FROM OPERA.NAME WHERE LAST = :last AND NAME_TYPE = 'D'"
        )

    def test_with_clause_passes(self):
        assert_read_only(
            "WITH recent AS (SELECT NAME_ID FROM OPERA.RESERVATION_NAME WHERE RESORT = 'VINES') "
            "SELECT * FROM recent"
        )

    def test_lowercase_select_passes(self):
        assert_read_only("select 1 from dual")

    def test_trailing_semicolon_allowed(self):
        assert_read_only("SELECT 1 FROM DUAL;")

    @pytest.mark.parametrize(
        "sql",
        [
            "INSERT INTO OPERA.NAME (FIRST) VALUES ('x')",
            "UPDATE OPERA.NAME SET FIRST = 'x'",
            "DELETE FROM OPERA.NAME",
            "DROP TABLE OPERA.NAME",
            "ALTER TABLE OPERA.NAME ADD COLUMN x VARCHAR2(10)",
            "TRUNCATE TABLE OPERA.NAME",
            "CREATE TABLE foo (x INT)",
            "MERGE INTO OPERA.NAME USING dual ON (1=1) WHEN MATCHED THEN UPDATE SET FIRST = 'x'",
            "GRANT SELECT ON OPERA.NAME TO public",
            "EXECUTE my_procedure",
            "CALL my_procedure()",
            "BEGIN do_something; END;",
        ],
    )
    def test_write_statements_rejected(self, sql):
        with pytest.raises(ValueError):
            assert_read_only(sql)

    def test_multiple_statements_rejected(self):
        with pytest.raises(ValueError, match="Multiple statements"):
            assert_read_only("SELECT 1 FROM DUAL; SELECT 2 FROM DUAL")

    def test_empty_rejected(self):
        with pytest.raises(ValueError):
            assert_read_only("")
        with pytest.raises(ValueError):
            assert_read_only("   \n  ")

    def test_only_comments_rejected(self):
        with pytest.raises(ValueError):
            assert_read_only("-- just a comment")
        with pytest.raises(ValueError):
            assert_read_only("/* block comment */")

    def test_forbidden_keyword_in_comment_ignored(self):
        # Comments must not trigger false positives.
        assert_read_only("SELECT 1 FROM DUAL -- not really a DROP TABLE")
        assert_read_only("SELECT /* DELETE */ 1 FROM DUAL")

    def test_forbidden_keyword_in_string_literal_ignored(self):
        # The word "UPDATE" inside a string literal should not trigger rejection.
        assert_read_only("SELECT 'this is an UPDATE log entry' FROM DUAL")

    def test_keyword_in_identifier_not_flagged(self):
        # Identifiers containing forbidden substrings (no word boundary) should pass.
        # e.g. "UPDATER_NAME" includes UPDATE substring but isn't the keyword.
        assert_read_only("SELECT UPDATER_NAME FROM SOME_TABLE")


class TestQueryErrorLogging:
    @patch("src.opera_client.time.sleep")
    @patch("src.opera_client.get_connection")
    def test_db_error_logs_sql_and_error(self, mock_conn, _sleep, caplog):
        opera_client._conn_holder[0] = None
        mock_conn.side_effect = oracledb.DatabaseError(
            "ORA-00942: table or view does not exist"
        )
        sql = "SELECT NAME_ID FROM OPERA.NONEXISTENT WHERE LAST = :last"

        with caplog.at_level("WARNING", logger="src.opera_client"):
            with pytest.raises(oracledb.DatabaseError):
                query(sql, binds={"last": "Smith"})

        # Retried attempts log WARNING; the final attempt logs ERROR.
        assert sum(r.levelname == "WARNING" for r in caplog.records) == 2
        rec = next(r for r in caplog.records if r.levelname == "ERROR")
        msg = rec.getMessage()
        assert "OPERA.NONEXISTENT" in msg  # the offending SQL
        assert "ORA-00942" in msg  # the Oracle error
        assert "['last']" in msg  # bind keys logged
        assert "Smith" not in msg  # bind values (PII) are NOT logged
