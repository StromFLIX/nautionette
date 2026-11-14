import time
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException
from nautionette_backend import github_setup, projects

OLD = "https://app.nautionette.example.com"
NEW = "https://app.denkzeug.example.com"


@pytest.fixture
def origin_connections(db, monkeypatch):
    monkeypatch.delenv("GITHUB_APP_PUBLIC_URL", raising=False)
    config = {
        "app_id": "12",
        "installation_id": "34",
        "public_url": OLD,
        "slug": "existing-app",
        "private_key": "private-migration-key",
        "webhook_secret": "hook-secret",
    }
    first = projects.save_connection(config)
    second = projects.save_connection(config | {"installation_id": "35"})
    unrelated = projects.save_connection(config | {"app_id": "13", "installation_id": "36"})
    db.set_setting(github_setup.REGISTERED_SETTING, config)
    monkeypatch.setattr(projects, "app_jwt", lambda _: "app-jwt")
    remote = AsyncMock(return_value={"url": NEW + github_setup.BASE_PATH + "/webhook"})
    monkeypatch.setattr(projects, "github", remote)
    return first, second, unrelated, remote


async def test_origin_migration_verifies_provider_and_preserves_every_credential(db, origin_connections):
    first, second, unrelated, remote = origin_connections
    result = await github_setup.migrate_public_url(first, NEW, True)
    assert result["public_url"] == NEW
    assert set(result["connection_ids"]) == {first, second}
    for identifier in (first, second):
        config = projects.app_config(identifier)
        assert config["public_url"] == NEW
        assert config["private_key"] == "private-migration-key"
        assert config["webhook_secret"] == "hook-secret"
    assert github_setup.registered_app()["public_url"] == NEW
    assert projects.app_config(unrelated)["public_url"] == OLD
    remote.assert_awaited_once_with("GET", "/app/hook/config", "app-jwt")
    assert await github_setup.migrate_public_url(first, NEW, True) == result


@pytest.mark.parametrize(
    "case", ["confirmation", "wrong-webhook", "environment", "active-setup", "provider-error"]
)
async def test_migration_refuses_partial_or_unsafe_cutovers(db, monkeypatch, origin_connections, case):
    first, _, _, remote = origin_connections
    if case == "wrong-webhook":
        remote.return_value = {"url": "https://other.example.com/api/projects/github-app/webhook"}
    elif case == "environment":
        monkeypatch.setenv("GITHUB_APP_PUBLIC_URL", OLD)
    elif case == "active-setup":
        github_setup.begin(OLD)
    elif case == "provider-error":
        remote.side_effect = HTTPException(502, "GitHub unavailable")
    with pytest.raises(HTTPException):
        await github_setup.migrate_public_url(first, NEW, case != "confirmation")
    assert projects.app_config(first)["public_url"] == OLD
    assert github_setup.registered_app()["public_url"] == OLD
    if case in {"confirmation", "environment"}:
        remote.assert_not_awaited()


async def test_expired_setup_does_not_block_migration(db, origin_connections):
    first, _, _, _ = origin_connections
    github_setup.begin(OLD)
    db.execute("UPDATE github_app_setups SET expires_at = ?", (time.time() - 1,))
    assert (await github_setup.migrate_public_url(first, NEW, True))["public_url"] == NEW


def test_origin_migration_requires_user_auth_and_never_returns_secrets(client, anonymous, origin_connections):
    first, _, _, _ = origin_connections
    path = github_setup.BASE_PATH + f"/{first}/public-url"
    body = {"public_url": NEW, "callbacks_updated": True}
    assert anonymous.put(path, json=body).status_code == 401
    response = client.put(path, json=body)
    assert response.status_code == 200
    assert "private-migration-key" not in response.text and "hook-secret" not in response.text


async def test_foreign_connection_change_during_verification_fails_closed(db, origin_connections):
    first, _, _, remote = origin_connections

    async def replaced(*args):
        db.execute("DELETE FROM github_project_connections WHERE id = ?", (first,))
        return {"url": NEW + github_setup.BASE_PATH + "/webhook"}

    remote.side_effect = replaced
    with pytest.raises(HTTPException, match="connection changed"):
        await github_setup.migrate_public_url(first, NEW, True)
    assert github_setup.registered_app()["public_url"] == OLD
