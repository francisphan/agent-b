"""Shared fixtures for Pardot tests."""

from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture(autouse=True)
def _clean_pardot_session():
    """Reset the Pardot session singleton before each test."""
    from src.pardot_client import _pardot_session

    _pardot_session[0] = None
    yield
    _pardot_session[0] = None


@pytest.fixture
def mock_env(monkeypatch):
    """Set required environment variables for Pardot."""
    monkeypatch.setenv("PARDOT_BUSINESS_UNIT_ID", "0Uv000000000001")
    monkeypatch.setenv("SF_CLIENT_ID", "fake_client_id")
    monkeypatch.setenv("SF_CLIENT_SECRET", "fake_secret")


@pytest.fixture
def mock_sf():
    """Patch the Salesforce client used by pardot_client."""
    sf = MagicMock()
    sf.session_id = "fake_access_token_123"
    with patch("src.pardot_client.get_sf_client", return_value=sf) as m:
        m.sf_instance = sf
        yield sf
