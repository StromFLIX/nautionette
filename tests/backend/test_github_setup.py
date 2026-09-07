import hashlib
import hmac
import html
import json
import re
import time
from unittest.mock import AsyncMock
from urllib.parse import parse_qs, urlsplit

import pytest
from fastapi import HTTPException
from nautionette_backend import github_setup, projects


@pytest.fixture(autouse=True)
def setup_storage(db, monkeypatch):
    monkeypatch.delenv("GITHUB_APP_PUBLIC_URL", raising=False)
    projects._tokens.clear()


def begin():
    result = github_setup.begin("https://nautionette.example.com", "example-org")
    state = parse_qs(urlsplit(result["start_url"]).query)["state"][0]
    return state, github_setup.start(state)


def test_manifest_registers_permissions_callbacks_and_webhooks_without_secrets(client, anonymous):
    assert anonymous.post(github_setup.BASE_PATH + "/connect", json={}).status_code == 401
    response = client.post(
        github_setup.BASE_PATH + "/connect",
        json={"public_url": "https://nautionette.example.com", "organization": "example-org"},
    )
    target = response.json()["start_url"]
    response = anonymous.get(target)
    assert response.status_code == 200
    assert "https://github.com/organizations/example-org/settings/apps/new?" in response.text
    manifest = json.loads(html.unescape(re.search(r'name="manifest" value="([^"]+)"', response.text)[1]))
    assert manifest["hook_attributes"] == {
        "url": "https://nautionette.example.com/api/projects/github-app/webhook",
        "active": True,
    }
    assert manifest["default_permissions"]["contents"] == "write"
    assert manifest["public"] is False
    assert manifest["setup_on_update"] is True
    assert "private_key" not in json.dumps(manifest)
    assert "HttpOnly" in response.headers["set-cookie"] and "Secure" in response.headers["set-cookie"]
    assert response.headers["referrer-policy"] == "no-referrer"
    assert anonymous.get(target).status_code == 400


@pytest.mark.parametrize(
    "url",
    [
        "http://localhost:8080",
        "https://127.0.0.1",
        "https://192.168.1.1",
        "https://app.local",
        "https://user:secret@example.com",
        "https://example.com/path",
        "https://example.com?redirect=x",
    ],
)
def test_unusable_or_unsafe_webhook_origins_are_rejected(url):
    with pytest.raises(HTTPException):
        github_setup.begin(url)


@pytest.mark.asyncio
async def test_registration_and_installation_exchange_is_browser_bound_and_single_use(db, monkeypatch):
    state, flow = begin()
    remote = AsyncMock(
        return_value={
            "id": 12,
            "slug": "nautionette-app",
            "pem": "server-private-key",
            "webhook_secret": "webhook-secret",
        }
    )
    monkeypatch.setattr(projects, "github", remote)
    monkeypatch.setattr(projects, "app_jwt", lambda config: "app-jwt")
    with pytest.raises(HTTPException):
        await github_setup.convert(state, "other-browser", "code")
    assert remote.await_count == 0
    install_url = await github_setup.convert(state, flow["browser"], "code")
    assert install_url.startswith("https://github.com/apps/nautionette-app/installations/new?")
    remote.assert_awaited_once_with("POST", "/app-manifests/code/conversions", "")
    assert "server-private-key" not in json.dumps(github_setup.status())
    assert not github_setup.status()["configured"]
    with pytest.raises(HTTPException):
        await github_setup.convert(state, flow["browser"], "code")
    remote.return_value = {
        "app_id": 12,
        "permissions": {"contents": "write", "workflows": "write"},
        "account": {"login": "example-org"},
    }
    assert (
        await github_setup.installed(state, flow["browser"], "34")
        == "https://nautionette.example.com/settings/projects?github=connected"
    )
    assert projects.app_config()["installation_id"] == "34"
    assert projects.app_config()["private_key"] == "server-private-key"
    with pytest.raises(HTTPException):
        await github_setup.installed(state, flow["browser"], "34")


@pytest.mark.asyncio
async def test_foreign_installation_and_expired_callbacks_do_not_replace_connection(db, monkeypatch):
    config = {
        "app_id": "12",
        "slug": "app",
        "private_key": "key",
        "webhook_secret": "secret",
        "public_url": "https://nautionette.example.com",
    }
    db.set_setting(github_setup.REGISTERED_SETTING, config)
    db.set_setting(projects.APP_SETTING, {"app_id": "old", "private_key": "old-key"})
    state, flow = begin()
    assert "manifest" not in flow
    monkeypatch.setattr(projects, "app_jwt", lambda config: "jwt")
    monkeypatch.setattr(
        projects, "github", AsyncMock(return_value={"app_id": 999, "permissions": {"contents": "write"}})
    )
    with pytest.raises(HTTPException):
        await github_setup.installed(state, flow["browser"], "34")
    assert projects.app_config()["app_id"] == "old"
    db.execute("UPDATE github_app_setups SET expires_at = ?", (time.time() - 1,))
    with pytest.raises(HTTPException):
        await github_setup.installed(state, flow["browser"], "34")


@pytest.mark.asyncio
async def test_installation_request_stays_pending_until_org_approval(db):
    config = {
        "app_id": "12",
        "slug": "app",
        "private_key": "key",
        "webhook_secret": "secret",
        "public_url": "https://nautionette.example.com",
    }
    db.set_setting(github_setup.REGISTERED_SETTING, config)
    state, flow = begin()
    assert (
        await github_setup.installed(state, flow["browser"], "", "request")
        == "https://nautionette.example.com/settings/projects?github=pending"
    )
    assert github_setup.status()["registered"]
    assert not github_setup.status()["configured"]


def send_webhook(client, payload, event="installation", delivery="delivery", secret=b"secret"):
    body = json.dumps(payload).encode()
    return client.post(
        github_setup.BASE_PATH + "/webhook",
        content=body,
        headers={
            "X-Hub-Signature-256": "sha256=" + hmac.new(secret, body, hashlib.sha256).hexdigest(),
            "X-GitHub-Event": event,
            "X-GitHub-Delivery": delivery,
        },
    )


def test_signed_webhooks_are_public_but_deduplicated_and_revoke_suspended_installations(db, anonymous):
    db.set_setting(
        projects.APP_SETTING,
        {"app_id": "12", "installation_id": "34", "webhook_secret": "secret", "private_key": "key"},
    )
    projects._tokens[123] = ("cached-token", time.time() + 3000)
    payload = {"action": "suspend", "installation": {"id": 34}}
    assert send_webhook(anonymous, payload, secret=b"wrong").status_code == 401
    assert projects.app_status()["configured"]
    assert send_webhook(anonymous, payload).json() == {"ok": True}
    assert projects.app_config()["installation_status"] == "suspended"
    assert not projects.app_status()["configured"]
    assert projects._tokens == {}
    assert send_webhook(anonymous, payload).json()["duplicate"] is True
    payload["action"] = "unsuspend"
    payload["installation"]["permissions"] = {"contents": "write"}
    assert send_webhook(anonymous, payload, delivery="resume").status_code == 200
    assert projects.app_status()["configured"]


def test_foreign_installations_and_malformed_webhooks_cannot_change_settings(db, anonymous):
    db.set_setting(
        projects.APP_SETTING,
        {"app_id": "12", "installation_id": "34", "webhook_secret": "secret", "private_key": "key"},
    )
    assert send_webhook(anonymous, {"action": "deleted", "installation": {"id": 999}}).json()["ignored"]
    assert projects.app_status()["configured"]
    assert send_webhook(anonymous, []).status_code == 400
    assert (
        anonymous.post(
            github_setup.BASE_PATH + "/webhook", content=b"x" * (github_setup.MAX_WEBHOOK_BYTES + 1)
        ).status_code
        == 413
    )
    assert anonymous.post(github_setup.BASE_PATH + "/webhook", content=b"{}").status_code == 401


def test_push_webhooks_update_metadata_without_touching_chat_worktrees(db, anonymous, tmp_path, monkeypatch):
    db.set_setting(
        projects.APP_SETTING, {"app_id": "12", "installation_id": "34", "webhook_secret": "secret"}
    )
    db.execute(
        "INSERT INTO projects VALUES (?,?,?,?,?,?)", ("a" * 32, 99, "owner/old", "master", "ready", "")
    )
    monkeypatch.setattr(projects, "PROJECTS_DIR", tmp_path)
    local = tmp_path / "unfinished.txt"
    local.write_text("keep my changes")
    payload = {
        "installation": {"id": 34},
        "repository": {"id": 99, "full_name": "owner/new", "default_branch": "main"},
    }
    assert send_webhook(anonymous, payload, event="push").status_code == 200
    project = db.one("SELECT * FROM projects WHERE repository_id = 99")
    assert project["full_name"] == "owner/new" and project["default_branch"] == "main"
    assert local.read_text() == "keep my changes"


def test_repository_access_changes_invalidate_installation_token_cache(db, anonymous):
    db.set_setting(
        projects.APP_SETTING, {"app_id": "12", "installation_id": "34", "webhook_secret": "secret"}
    )
    projects._tokens[123] = ("cached-token", time.time() + 3000)
    assert (
        send_webhook(
            anonymous, {"action": "removed", "installation": {"id": 34}}, event="installation_repositories"
        ).status_code
        == 200
    )
    assert not projects._tokens


def test_callback_routes_keep_secrets_server_side_and_support_secure_cookies(client, monkeypatch):
    origin = "https://nautionette.example.com"
    target = client.post(github_setup.BASE_PATH + "/connect", json={"public_url": origin}).json()["start_url"]
    client.get(target)
    state = parse_qs(urlsplit(target).query)["state"][0]
    remote = AsyncMock(
        return_value={"id": 12, "slug": "app", "pem": "private-secret", "webhook_secret": "hook-secret"}
    )
    monkeypatch.setattr(projects, "github", remote)
    monkeypatch.setattr(projects, "app_jwt", lambda config: "jwt")
    response = client.get(
        f"{origin}{github_setup.BASE_PATH}/callback",
        params={"state": state, "code": "code"},
        follow_redirects=False,
    )
    assert response.status_code == 303 and response.headers["location"].startswith(
        "https://github.com/apps/app/"
    )
    assert "private-secret" not in response.text and "hook-secret" not in response.text
    remote.return_value = {"app_id": 12, "permissions": {"contents": "write"}}
    response = client.get(
        f"{origin}{github_setup.BASE_PATH}/installed",
        params={"state": state, "installation_id": "34"},
        follow_redirects=False,
    )
    assert response.headers["location"] == f"{origin}/settings/projects?github=connected"
    assert "Max-Age=0" in response.headers["set-cookie"]
    assert client.get(github_setup.BASE_PATH + "/callback?state=wrong&code=code").status_code == 400
