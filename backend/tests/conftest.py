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


@pytest.fixture(autouse=True)
def no_real_downloads(monkeypatch, tmp_path):
    """Tests never download street data: trip areas go to a temporary folder, and the download step
    is replaced by a stub unless a test installs its own (see test_anywhere.py)."""
    from safeher import areas, prepare
    monkeypatch.setattr(areas, "TRIP_DATA_DIR", tmp_path / "trip-areas")
    monkeypatch.setattr(areas, "_trip_areas", {})
    monkeypatch.setattr(prepare, "_download", lambda area: None)
