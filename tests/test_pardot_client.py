"""Tests for src/pardot_client.py."""

from unittest.mock import MagicMock, Mock, patch

import pytest
import requests

from src.pardot_client import (
    BASE_URL,
    _build_session,
    _get,
    _get_access_token,
    _get_business_unit_id,
    _pardot_session,
    _patch,
    _post,
    _refresh_session,
    _with_retry,
    create_prospect,
    get_campaign,
    get_email_template,
    get_form,
    get_list,
    get_prospect,
    get_session,
    pardot_enabled,
    pardot_full_surface,
    query_campaigns,
    query_email_templates,
    query_forms,
    query_list_memberships,
    query_lists,
    query_prospects,
    query_visitor_activities,
    update_prospect,
)


# --- _get_business_unit_id ---


class TestGetBusinessUnitId:
    def test_returns_env_value(self, monkeypatch):
        monkeypatch.setenv("PARDOT_BUSINESS_UNIT_ID", "0Uv000000000001")
        assert _get_business_unit_id() == "0Uv000000000001"

    def test_raises_when_missing(self, monkeypatch):
        monkeypatch.delenv("PARDOT_BUSINESS_UNIT_ID", raising=False)
        with pytest.raises(RuntimeError, match="PARDOT_BUSINESS_UNIT_ID"):
            _get_business_unit_id()


# --- _get_access_token ---


class TestGetAccessToken:
    def test_returns_sf_session_id(self, mock_sf):
        assert _get_access_token() == "fake_access_token_123"


# --- _build_session / get_session ---


class TestBuildSession:
    def test_session_has_correct_headers(self, mock_env, mock_sf):
        session = _build_session()
        assert session.headers["Authorization"] == "Bearer fake_access_token_123"
        assert session.headers["Pardot-Business-Unit-Id"] == "0Uv000000000001"
        assert session.headers["Content-Type"] == "application/json"

    def test_get_session_singleton(self, mock_env, mock_sf):
        s1 = get_session()
        s2 = get_session()
        assert s1 is s2

    def test_get_session_rebuilds_after_clear(self, mock_env, mock_sf):
        s1 = get_session()
        _pardot_session[0] = None
        s2 = get_session()
        assert s1 is not s2


# --- _refresh_session ---


class TestRefreshSession:
    @patch("src.pardot_client.sf_reconnect")
    def test_refresh_rebuilds_session(self, mock_reconnect, mock_env, mock_sf):
        new_sf = MagicMock()
        new_sf.session_id = "new_token_456"
        mock_reconnect.return_value = new_sf

        old_session = get_session()
        _refresh_session()
        new_session = get_session()

        assert old_session is not new_session
        mock_reconnect.assert_called_once()


# --- pardot_enabled() gate ---


class TestPardotEnabled:
    """The PARDOT_TOOLS_ENABLED gate — default ON, off only when explicitly false-y."""

    def test_enabled_when_unset(self, monkeypatch):
        monkeypatch.delenv("PARDOT_TOOLS_ENABLED", raising=False)
        assert pardot_enabled() is True

    def test_enabled_when_empty(self, monkeypatch):
        monkeypatch.setenv("PARDOT_TOOLS_ENABLED", "")
        assert pardot_enabled() is True

    @pytest.mark.parametrize("val", ["true", "1", "yes", "TRUE", " Yes ", "on", "garbage"])
    def test_enabled_for_truthy_or_unrecognised(self, monkeypatch, val):
        # Only explicit off values disable; anything else (incl. unknown) stays on.
        monkeypatch.setenv("PARDOT_TOOLS_ENABLED", val)
        assert pardot_enabled() is True

    @pytest.mark.parametrize("val", ["false", "0", "no", "off", "FALSE", " No "])
    def test_disabled_for_falsey(self, monkeypatch, val):
        monkeypatch.setenv("PARDOT_TOOLS_ENABLED", val)
        assert pardot_enabled() is False


class TestPardotEnabledUnrecognised:
    """A non-empty but unrecognized flag value fails OPEN (stays enabled) and warns
    once, instead of silently flipping the gate on a typo."""

    @pytest.fixture(autouse=True)
    def _reset_warn_cache(self):
        from src.pardot_client import _warned_flag_values

        _warned_flag_values.clear()
        yield
        _warned_flag_values.clear()

    def test_unrecognised_stays_enabled_and_warns(self, monkeypatch, caplog):
        monkeypatch.setenv("PARDOT_TOOLS_ENABLED", "fasle")  # typo of "false"
        with caplog.at_level("WARNING", logger="src.pardot_client"):
            assert pardot_enabled() is True
        assert any(
            r.levelname == "WARNING" and "fasle" in r.getMessage() and "ENABLED" in r.getMessage()
            for r in caplog.records
        )

    def test_unrecognised_warns_only_once(self, monkeypatch, caplog):
        monkeypatch.setenv("PARDOT_TOOLS_ENABLED", "maybe")
        with caplog.at_level("WARNING", logger="src.pardot_client"):
            assert pardot_enabled() is True
            assert pardot_enabled() is True
            assert pardot_enabled() is True
        warnings = [r for r in caplog.records if r.levelname == "WARNING"]
        assert len(warnings) == 1  # deduped — not one per per-call gate check

    def test_recognised_values_never_warn(self, monkeypatch, caplog):
        with caplog.at_level("WARNING", logger="src.pardot_client"):
            for val in ("on", "true", "1", "yes", "off", "no", "0", "false"):
                monkeypatch.setenv("PARDOT_TOOLS_ENABLED", val)
                pardot_enabled()
            monkeypatch.delenv("PARDOT_TOOLS_ENABLED", raising=False)
            pardot_enabled()  # unset
        assert not [r for r in caplog.records if r.levelname == "WARNING"]


# --- pardot_full_surface() (full reads+writes vs curated read subset) ---


class TestPardotFullSurface:
    """Distinct axis from the on/off gate, deliberately stricter: only an explicit
    truthy value opts into the full surface, preserving pre-flag behaviour (unset =
    curated, and notably "on" = enabled-but-curated, never full)."""

    @pytest.mark.parametrize("val", ["true", "1", "yes", "TRUE", " Yes "])
    def test_truthy_enables_full_surface(self, monkeypatch, val):
        monkeypatch.setenv("PARDOT_TOOLS_ENABLED", val)
        assert pardot_full_surface() is True

    @pytest.mark.parametrize("val", [None, "", "on", "garbage", "false", "0"])
    def test_non_truthy_stays_curated(self, monkeypatch, val):
        if val is None:
            monkeypatch.delenv("PARDOT_TOOLS_ENABLED", raising=False)
        else:
            monkeypatch.setenv("PARDOT_TOOLS_ENABLED", val)
        assert pardot_full_surface() is False


# --- Disabled short-circuit (PARDOT_TOOLS_ENABLED off) ---


class TestWithRetryDisabled:
    """When Pardot is off, _with_retry raises before any auth/HTTP work — the one
    guard that covers every read, write, and composite Pardot leg."""

    def test_disabled_raises_without_running_func(self, monkeypatch):
        monkeypatch.setenv("PARDOT_TOOLS_ENABLED", "false")
        ran = []
        with pytest.raises(RuntimeError, match="disabled"):
            _with_retry(lambda session: ran.append("ran"))
        assert ran == []  # func never executed — no HTTP attempted

    @patch("src.pardot_client.get_session")
    def test_disabled_does_not_build_session(self, mock_get_session, monkeypatch):
        # get_session() is where the SF token is fetched and the HTTP session is
        # built; short-circuiting before it means no auth work happens.
        monkeypatch.setenv("PARDOT_TOOLS_ENABLED", "0")
        with pytest.raises(RuntimeError, match="PARDOT_TOOLS_ENABLED"):
            _with_retry(lambda session: "unreachable")
        mock_get_session.assert_not_called()


# --- _with_retry ---


class TestWithRetry:
    def test_success_on_first_try(self, mock_env, mock_sf):
        result = _with_retry(lambda session: "ok")
        assert result == "ok"

    @patch("src.pardot_client.time.sleep")
    def test_retries_on_generic_exception(self, mock_sleep, mock_env, mock_sf):
        call_count = 0

        def flaky(session):
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise ConnectionError("transient")
            return "recovered"

        result = _with_retry(flaky)
        assert result == "recovered"
        assert call_count == 3
        assert mock_sleep.call_count == 2

    @patch("src.pardot_client._refresh_session")
    def test_reauths_on_401(self, mock_refresh, mock_env, mock_sf):
        response_401 = Mock()
        response_401.status_code = 401
        http_err = requests.exceptions.HTTPError(response=response_401)

        call_count = 0

        def fail_then_succeed(session):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise http_err
            return "after_reauth"

        result = _with_retry(fail_then_succeed)
        assert result == "after_reauth"
        mock_refresh.assert_called_once()

    @patch("src.pardot_client.time.sleep")
    def test_raises_after_max_retries(self, mock_sleep, mock_env, mock_sf):
        def always_fail(session):
            raise ConnectionError("permanent failure")

        with pytest.raises(ConnectionError, match="permanent failure"):
            _with_retry(always_fail)

    @patch("src.pardot_client.time.sleep")
    def test_retries_non_401_http_error(self, mock_sleep, mock_env, mock_sf):
        response_500 = Mock()
        response_500.status_code = 500
        http_err = requests.exceptions.HTTPError(response=response_500)

        def always_500(session):
            raise http_err

        with pytest.raises(requests.exceptions.HTTPError):
            _with_retry(always_500)

        assert mock_sleep.call_count == 2  # retried twice before giving up


# --- _get helper ---


class TestGetHelper:
    @patch("src.pardot_client._with_retry")
    def test_get_calls_correct_url(self, mock_retry, mock_env, mock_sf):
        mock_retry.side_effect = lambda func: func(
            MagicMock(
                get=MagicMock(
                    return_value=MagicMock(
                        raise_for_status=MagicMock(),
                        json=MagicMock(return_value={"values": []}),
                    )
                )
            )
        )
        result = _get("prospects", params={"limit": 10})
        assert result == {"values": []}


class TestGetErrorMessage:
    """_get must surface the response body in the raised error's message.

    Tools return {"error": str(e)}, so a bare requests HTTPError line (no body)
    hid Pardot's real complaint (e.g. code-184 "Invalid scope") from callers.
    """

    @patch("src.pardot_client._with_retry")
    def test_get_error_body_in_message(self, mock_retry, mock_env, mock_sf):
        resp = MagicMock()
        resp.ok = False
        resp.status_code = 400
        resp.reason = "Bad Request"
        resp.text = '{"code": 184, "message": "Invalid scope"}'
        session = MagicMock()
        session.get.return_value = resp
        mock_retry.side_effect = lambda func: func(session)

        with pytest.raises(requests.exceptions.HTTPError) as exc_info:
            _get("prospects")
        msg = str(exc_info.value)
        assert "400 Bad Request" in msg
        assert "Invalid scope" in msg  # Pardot's actual error body
        # response stays attached so _with_retry can still branch on status.
        assert exc_info.value.response is resp

    @patch("src.pardot_client._with_retry")
    def test_get_error_empty_body(self, mock_retry, mock_env, mock_sf):
        resp = MagicMock()
        resp.ok = False
        resp.status_code = 500
        resp.reason = "Server Error"
        resp.text = ""
        session = MagicMock()
        session.get.return_value = resp
        mock_retry.side_effect = lambda func: func(session)

        with pytest.raises(requests.exceptions.HTTPError, match="500 Server Error"):
            _get("prospects")

    @patch("src.pardot_client._refresh_session")
    @patch("src.pardot_client.time.sleep")
    def test_get_still_reauths_on_401(self, mock_sleep, mock_refresh, mock_env, mock_sf):
        # Body enrichment must not swallow response=, or the read path loses its
        # 401 re-auth. First GET 401, second GET succeeds after re-auth.
        resp_401 = MagicMock()
        resp_401.ok = False
        resp_401.status_code = 401
        resp_401.reason = "Unauthorized"
        resp_401.text = "session expired"
        resp_ok = MagicMock()
        resp_ok.ok = True
        resp_ok.json.return_value = {"values": []}

        session = get_session()
        session.get = MagicMock(side_effect=[resp_401, resp_ok])

        assert _get("prospects") == {"values": []}
        mock_refresh.assert_called_once()


# --- Query / Get functions ---


class TestQueryFunctions:
    @patch("src.pardot_client._get")
    def test_query_prospects(self, mock_get):
        mock_get.return_value = {"values": [{"id": "1"}]}
        result = query_prospects({"limit": 10})
        mock_get.assert_called_once_with("prospects", params={"limit": 10})
        assert result == {"values": [{"id": "1"}]}

    @patch("src.pardot_client._get")
    def test_get_prospect(self, mock_get):
        mock_get.return_value = {"id": "42"}
        result = get_prospect("42")
        mock_get.assert_called_once_with("prospects/42", params=None)
        assert result == {"id": "42"}

    @patch("src.pardot_client._get")
    def test_query_lists(self, mock_get):
        mock_get.return_value = {"values": []}
        query_lists({"fields": "name"})
        mock_get.assert_called_once_with("lists", params={"fields": "name"})

    @patch("src.pardot_client._get")
    def test_get_list(self, mock_get):
        mock_get.return_value = {"id": "5"}
        get_list("5")
        mock_get.assert_called_once_with("lists/5", params=None)

    @patch("src.pardot_client._get")
    def test_query_list_memberships(self, mock_get):
        mock_get.return_value = {"values": []}
        query_list_memberships({"list_id": "10"})
        mock_get.assert_called_once_with("list-memberships", params={"list_id": "10"})

    @patch("src.pardot_client._get")
    def test_query_campaigns(self, mock_get):
        mock_get.return_value = {"values": []}
        query_campaigns()
        mock_get.assert_called_once_with("campaigns", params=None)

    @patch("src.pardot_client._get")
    def test_get_campaign(self, mock_get):
        mock_get.return_value = {"id": "7"}
        get_campaign("7")
        mock_get.assert_called_once_with("campaigns/7", params=None)

    @patch("src.pardot_client._get")
    def test_query_visitor_activities(self, mock_get):
        mock_get.return_value = {"values": []}
        query_visitor_activities({"type": "Visit"})
        mock_get.assert_called_once_with("visitor-activities", params={"type": "Visit"})

    @patch("src.pardot_client._get")
    def test_query_forms(self, mock_get):
        mock_get.return_value = {"values": []}
        query_forms()
        mock_get.assert_called_once_with("forms", params=None)

    @patch("src.pardot_client._get")
    def test_get_form(self, mock_get):
        mock_get.return_value = {"id": "9"}
        get_form("9")
        mock_get.assert_called_once_with("forms/9", params=None)

    @patch("src.pardot_client._get")
    def test_query_email_templates(self, mock_get):
        mock_get.return_value = {"values": []}
        query_email_templates()
        mock_get.assert_called_once_with("email-templates", params=None)

    @patch("src.pardot_client._get")
    def test_get_email_template(self, mock_get):
        mock_get.return_value = {"id": "11"}
        get_email_template("11")
        mock_get.assert_called_once_with("email-templates/11", params=None)


# --- _post / _patch helpers ---


class TestPostHelper:
    @patch("src.pardot_client._with_retry")
    def test_post_calls_correct_url_with_json_body(self, mock_retry, mock_env, mock_sf):
        mock_session = MagicMock()
        mock_session.post.return_value = MagicMock(
            raise_for_status=MagicMock(),
            json=MagicMock(return_value={"id": "new"}),
        )
        mock_retry.side_effect = lambda func: func(mock_session)

        result = _post("prospects", {"email": "a@b.com"})
        mock_session.post.assert_called_once_with(
            f"{BASE_URL}/prospects", json={"email": "a@b.com"}
        )
        assert result == {"id": "new"}


class TestPatchHelper:
    @patch("src.pardot_client._with_retry")
    def test_patch_calls_correct_url_with_json_body(self, mock_retry, mock_env, mock_sf):
        mock_session = MagicMock()
        mock_session.patch.return_value = MagicMock(
            raise_for_status=MagicMock(),
            json=MagicMock(return_value={"id": "42", "firstName": "Updated"}),
        )
        mock_retry.side_effect = lambda func: func(mock_session)

        result = _patch("prospects/42", {"firstName": "Updated"})
        mock_session.patch.assert_called_once_with(
            f"{BASE_URL}/prospects/42", json={"firstName": "Updated"}
        )
        assert result == {"id": "42", "firstName": "Updated"}


# --- Write operation functions ---


class TestWriteFunctions:
    @patch("src.pardot_client._post")
    def test_create_prospect(self, mock_post):
        mock_post.return_value = {"id": "100", "email": "new@test.com"}
        result = create_prospect({"email": "new@test.com", "firstName": "Jane"})
        mock_post.assert_called_once_with(
            "prospects", {"email": "new@test.com", "firstName": "Jane"}
        )
        assert result["id"] == "100"

    @patch("src.pardot_client._patch")
    def test_update_prospect(self, mock_patch):
        mock_patch.return_value = {"id": "100", "firstName": "Updated"}
        result = update_prospect("100", {"firstName": "Updated"})
        mock_patch.assert_called_once_with("prospects/100", {"firstName": "Updated"})
        assert result["firstName"] == "Updated"


# --- 4xx no-retry behavior ---


class TestNoRetryOn4xx:
    @patch("src.pardot_client.time.sleep")
    def test_400_raises_immediately_without_retry(self, mock_sleep, mock_env, mock_sf):
        response_400 = Mock()
        response_400.status_code = 400
        http_err = requests.exceptions.HTTPError(response=response_400)

        def always_400(session):
            raise http_err

        with pytest.raises(requests.exceptions.HTTPError):
            _with_retry(always_400)

        # Should NOT have slept (no retries for 4xx)
        mock_sleep.assert_not_called()

    @patch("src.pardot_client.time.sleep")
    def test_403_raises_immediately_without_retry(self, mock_sleep, mock_env, mock_sf):
        response_403 = Mock()
        response_403.status_code = 403
        http_err = requests.exceptions.HTTPError(response=response_403)

        def always_403(session):
            raise http_err

        with pytest.raises(requests.exceptions.HTTPError):
            _with_retry(always_403)

        mock_sleep.assert_not_called()


class TestErrorLogging:
    @patch("src.pardot_client.time.sleep")
    def test_400_logs_url_and_body_at_error(self, mock_sleep, mock_env, mock_sf, caplog):
        response_400 = Mock()
        response_400.status_code = 400
        response_400.url = "https://pi.pardot.com/api/v5/objects/prospects/999"
        response_400.text = '{"code": 4, "message": "Invalid prospect ID"}'
        http_err = requests.exceptions.HTTPError(response=response_400)

        def always_400(session):
            raise http_err

        with caplog.at_level("WARNING", logger="src.pardot_client"):
            with pytest.raises(requests.exceptions.HTTPError):
                _with_retry(always_400)

        rec = next(r for r in caplog.records if r.levelname == "ERROR")
        msg = rec.getMessage()
        assert "status=400" in msg
        assert "prospects/999" in msg  # the request URL
        assert "Invalid prospect ID" in msg  # Pardot's error body

    @patch("src.pardot_client.time.sleep")
    def test_500_retry_logs_at_warning(self, mock_sleep, mock_env, mock_sf, caplog):
        response_500 = Mock()
        response_500.status_code = 500
        response_500.url = "https://pi.pardot.com/api/v5/objects/prospects"
        response_500.text = "Internal Server Error"
        http_err = requests.exceptions.HTTPError(response=response_500)

        def always_500(session):
            raise http_err

        with caplog.at_level("WARNING", logger="src.pardot_client"):
            with pytest.raises(requests.exceptions.HTTPError):
                _with_retry(always_500)

        assert any(r.levelname == "WARNING" for r in caplog.records)
        assert "will retry" in caplog.text


class TestGetErrorClipAndPersistent401:
    """Review fixes: 500-char marked clip; persistent 401 raises the HTTPError."""

    @patch("src.pardot_client._with_retry")
    def test_get_error_body_clipped_with_marker(self, mock_retry, mock_env, mock_sf):
        resp = MagicMock()
        resp.ok = False
        resp.status_code = 400
        resp.reason = "Bad Request"
        resp.text = "x" * 600
        session = MagicMock()
        session.get.return_value = resp
        mock_retry.side_effect = lambda func: func(session)

        with pytest.raises(requests.exceptions.HTTPError) as exc_info:
            _get("prospects")
        assert "(+100 chars)" in str(exc_info.value)  # 600 - 500, marked

    @patch("src.pardot_client._refresh_session")
    def test_persistent_401_raises_http_error_not_typeerror(self, mock_refresh, mock_env, mock_sf):
        # If re-auth never fixes the 401, the retry loop must raise the real
        # HTTPError (carrying "Invalid scope" etc.), not TypeError(None).
        resp = MagicMock()
        resp.ok = False
        resp.status_code = 401
        resp.reason = "Unauthorized"
        resp.text = '{"code": 184, "message": "Invalid scope"}'
        session = MagicMock()
        session.get.return_value = resp

        with patch("src.pardot_client.get_session", return_value=session):
            with pytest.raises(requests.exceptions.HTTPError) as exc_info:
                _get("prospects")
        assert "Invalid scope" in str(exc_info.value)
