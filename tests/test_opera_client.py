"""Tests for the OPERA read-only SQL guard and the HTTP query client."""

from unittest.mock import MagicMock, patch

import pytest
import requests

from src.opera_client import assert_read_only, query


class TestAssertReadOnly:
    def test_simple_select_passes(self):
        assert_read_only("SELECT 1 FROM DUAL")

    def test_select_with_named_binds_passes(self):
        assert_read_only("SELECT NAME_ID FROM OPERA.NAME WHERE LAST = :last AND NAME_TYPE = 'D'")

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


def _resp(status_code, *, ok=None, json_body=None, text=""):
    r = MagicMock(spec=requests.Response)
    r.status_code = status_code
    r.ok = (status_code < 400) if ok is None else ok
    r.json.return_value = json_body if json_body is not None else {}
    r.text = text
    return r


class TestQueryHTTP:
    @pytest.fixture(autouse=True)
    def _enable_opera(self, monkeypatch):
        # The HTTP path assumes OPERA is switched on; query() now gates on this.
        monkeypatch.setenv("OPERA_TOOLS_ENABLED", "true")

    @patch("src.opera_client._api_config", return_value=("http://opera-api.test", "tok"))
    @patch("src.opera_client._session.post")
    def test_disabled_short_circuits_without_http(self, mock_post, _cfg, monkeypatch):
        monkeypatch.setenv("OPERA_TOOLS_ENABLED", "false")
        with pytest.raises(RuntimeError, match="disabled"):
            query("SELECT 1 FROM DUAL")
        mock_post.assert_not_called()

    @patch("src.opera_client._api_config", return_value=("http://opera-api.test", "tok"))
    @patch("src.opera_client._session.post")
    def test_success_returns_rows(self, mock_post, _cfg):
        mock_post.return_value = _resp(200, json_body={"rows": [{"name_id": "1", "first": "A"}]})
        assert query("SELECT 1 FROM DUAL") == [{"name_id": "1", "first": "A"}]

    @patch("src.opera_client._api_config", return_value=("http://opera-api.test", "tok"))
    @patch("src.opera_client._session.post")
    def test_empty_rows_default(self, mock_post, _cfg):
        mock_post.return_value = _resp(200, json_body={})  # no "rows" key
        assert query("SELECT 1 FROM DUAL") == []

    @patch("src.opera_client._api_config", return_value=("http://opera-api.test", "tok"))
    @patch("src.opera_client._session.post")
    def test_http_400_guard_rejection_raises_valueerror(self, mock_post, _cfg):
        mock_post.return_value = _resp(
            400, json_body={"error": "only SELECT/WITH queries are allowed (got: UPDATE)"}
        )
        # SQL passes the client-side guard but the server rejects it → ValueError.
        with pytest.raises(ValueError, match="rejected query"):
            query("SELECT 1 FROM DUAL")
        assert mock_post.call_count == 1  # not retried

    @patch("src.opera_client._api_config", return_value=("http://opera-api.test", "tok"))
    @patch("src.opera_client._session.post")
    def test_http_401_raises_runtimeerror(self, mock_post, _cfg):
        mock_post.return_value = _resp(401, text="unauthorized")
        with pytest.raises(RuntimeError, match="auth failed"):
            query("SELECT 1 FROM DUAL")
        assert mock_post.call_count == 1  # not retried

    @patch("src.opera_client.time.sleep")
    @patch("src.opera_client._api_config", return_value=("http://opera-api.test", "tok"))
    @patch("src.opera_client._session.post")
    def test_network_error_retries_and_logs_without_pii(self, mock_post, _cfg, _sleep, caplog):
        mock_post.side_effect = requests.ConnectionError("connection refused")
        sql = "SELECT NAME_ID FROM OPERA.NONEXISTENT WHERE LAST = :last"

        with caplog.at_level("WARNING", logger="src.opera_client"):
            with pytest.raises(requests.RequestException):
                query(sql, binds={"last": "Smith"})

        assert mock_post.call_count == 3  # MAX_RETRIES
        # Retried attempts log WARNING; the final attempt logs ERROR.
        assert sum(r.levelname == "WARNING" for r in caplog.records) == 2
        rec = next(r for r in caplog.records if r.levelname == "ERROR")
        msg = rec.getMessage()
        assert "OPERA.NONEXISTENT" in msg  # the offending SQL is logged
        assert "['last']" in msg  # bind keys logged
        assert "Smith" not in msg  # bind values (PII) are NOT logged

    @patch("src.opera_client.time.sleep")
    @patch("src.opera_client._api_config", return_value=("http://opera-api.test", "tok"))
    @patch("src.opera_client._session.post")
    def test_5xx_retries_then_raises(self, mock_post, _cfg, _sleep):
        mock_post.return_value = _resp(503, text="upstream down")
        with pytest.raises(RuntimeError):
            query("SELECT 1 FROM DUAL")
        assert mock_post.call_count == 3

    @patch("src.opera_client._api_config", return_value=("http://opera-api.test", "tok"))
    @patch("src.opera_client._session.post")
    def test_proxy_passed_when_set(self, mock_post, _cfg, monkeypatch):
        monkeypatch.setenv("OPERA_API_PROXY", "socks5h://localhost:1055")
        mock_post.return_value = _resp(200, json_body={"rows": []})
        query("SELECT 1 FROM DUAL")
        _, kwargs = mock_post.call_args
        assert kwargs.get("proxies") == {
            "http": "socks5h://localhost:1055",
            "https": "socks5h://localhost:1055",
        }

    @patch("src.opera_client._api_config", return_value=("http://opera-api.test", "tok"))
    @patch("src.opera_client._session.post")
    def test_no_proxy_when_unset(self, mock_post, _cfg, monkeypatch):
        monkeypatch.delenv("OPERA_API_PROXY", raising=False)
        mock_post.return_value = _resp(200, json_body={"rows": []})
        query("SELECT 1 FROM DUAL")
        _, kwargs = mock_post.call_args
        assert kwargs.get("proxies") is None
