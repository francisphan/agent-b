"""Tests for src/sf_client.py error logging."""

from unittest.mock import MagicMock, patch

import pytest
from simple_salesforce.exceptions import SalesforceError

from src import sf_client
from src.sf_client import _log_sf_error, _with_retry


def _sf_error():
    return SalesforceError(
        "https://x.my.salesforce.com/services/data/v59.0/query/?q=SELECT+Id+FROM+Bogus",
        400,
        "Account",
        [{"message": "No such column 'Bogus'", "errorCode": "INVALID_FIELD"}],
    )


class TestLogSfError:
    def test_error_level_includes_status_url_and_body(self, caplog):
        with caplog.at_level("WARNING", logger="src.sf_client"):
            _log_sf_error(_sf_error(), will_retry=False)
        rec = next(r for r in caplog.records if r.levelname == "ERROR")
        msg = rec.getMessage()
        assert "status=400" in msg
        assert "SELECT+Id+FROM+Bogus" in msg  # the SOQL, carried in the URL
        assert "No such column" in msg  # Salesforce's error body

    def test_will_retry_logs_at_warning(self, caplog):
        with caplog.at_level("WARNING", logger="src.sf_client"):
            _log_sf_error(_sf_error(), will_retry=True)
        assert any(r.levelname == "WARNING" for r in caplog.records)
        assert "will retry" in caplog.text


class TestWithRetryLogging:
    def test_non_idempotent_logs_error_once_and_raises(self, caplog):
        sf_client._sf_holder[0] = MagicMock()

        def boom(_sf):
            raise _sf_error()

        with caplog.at_level("WARNING", logger="src.sf_client"):
            with pytest.raises(SalesforceError):
                _with_retry(boom, idempotent=False)

        errors = [r for r in caplog.records if r.levelname == "ERROR"]
        assert len(errors) == 1  # raised immediately, logged once

    @patch("src.sf_client.time.sleep")
    def test_idempotent_logs_warning_then_error(self, _sleep, caplog):
        sf_client._sf_holder[0] = MagicMock()

        def boom(_sf):
            raise _sf_error()

        with caplog.at_level("WARNING", logger="src.sf_client"):
            with pytest.raises(SalesforceError):
                _with_retry(boom, idempotent=True)

        # Two retried attempts log WARNING, the final attempt logs ERROR.
        assert sum(r.levelname == "WARNING" for r in caplog.records) == 2
        assert sum(r.levelname == "ERROR" for r in caplog.records) == 1
