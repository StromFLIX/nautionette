from __future__ import annotations

import pytest
from nautionette_backend.config import Settings, settings


def test_staging_is_runtime_metadata_available_before_login(anonymous, client, monkeypatch):
    monkeypatch.setattr(settings, "environment", "staging")
    assert anonymous.get("/healthz").json()["environment"] == "staging"
    assert anonymous.get("/api/system").status_code == 401
    assert client.get("/api/system").json()["environment"] == "staging"


def test_environment_typo_fails_closed(monkeypatch):
    monkeypatch.setenv("APP_ENVIRONMENT", "stagign")
    with pytest.raises(ValueError, match="APP_ENVIRONMENT"):
        Settings()
