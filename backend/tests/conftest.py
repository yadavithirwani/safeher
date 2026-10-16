"""Shared test setup."""

import os

import pytest

from safeher import google


@pytest.fixture(autouse=True)
def no_real_google_by_default(monkeypatch):
    """Tests never spend money or depend on the network: the real Google key in backend/.env is
    ignored unless SAFEHER_LIVE_GOOGLE=1. Google behaviour is tested with mocks (test_google.py);
    `python -m safeher.google_check` checks the real APIs."""
    if os.environ.get("SAFEHER_LIVE_GOOGLE") != "1":
        monkeypatch.setattr(google, "api_key", lambda: "")
        # Every real key in backend/.env is hidden (an empty env var wins over .env in load_dotenv).
        for name in ("GOOGLE_MAPS_API_KEY", "GEMINI_API_KEY", "EARTHDATA_TOKEN"):
            monkeypatch.setenv(name, "")


@pytest.fixture(autouse=True)
def private_user_database(monkeypatch, tmp_path):
    """Each test gets its own empty user database (reports, live trips) instead of backend/var."""
    from safeher import userdb
    monkeypatch.setattr(userdb, "DB_FILE", tmp_path / "safeher.sqlite3")
