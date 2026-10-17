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
        for name in ("GOOGLE_MAPS_API_KEY", "GEMINI_API_KEY", "EARTHDATA_TOKEN", "GOOGLE_CLOUD_PROJECT",
                     "GOOGLE_API_KEY", "CHATBOT_PROVIDER", "VERTEX_API_KEY"):
            monkeypatch.setenv(name, "")
        monkeypatch.delenv("GOOGLE_GENAI_USE_VERTEXAI", raising=False)


@pytest.fixture(autouse=True)
def scope_check_offline(monkeypatch):
    """HerSide's scope check calls a model; tests treat every message as in scope unless a test
    exercises the check itself (scope._check_impl, test_chatbot_scope.py)."""
    from safeher.chatbot import scope

    async def allow(message, recent=None):
        return scope.Verdict(True, "walking_route", "test")
    monkeypatch.setattr(scope, "check", allow)


@pytest.fixture(autouse=True)
def private_user_database(monkeypatch, tmp_path):
    """Each test gets its own empty user database (reports, live trips) instead of backend/var."""
    from safeher import userdb
    monkeypatch.setattr(userdb, "DB_FILE", tmp_path / "safeher.sqlite3")
