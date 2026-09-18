"""Multiple GitHub accounts must never replace each other's credentials or projects."""

import json
import time
from unittest.mock import AsyncMock, Mock
from urllib.parse import parse_qs, urlsplit

import pytest
from fastapi import HTTPException
from nautionette_backend import github_setup, projects
from nautionette_backend.db import Database

from .test_github_setup import send_webhook
from .test_projects import ready_project


@pytest.fixture(autouse=True)
def storage(db, monkeypatch, tmp_path):
    monkeypatch.setattr(projects, "PROJECTS_DIR", tmp_path)
    monkeypatch.delenv("GITHUB_APP_PUBLIC_URL", raising=False)
    projects._tokens.clear()


def connect(app_id="1", installation_id="10", account="personal", secret="personal-secret"):  # noqa: S107
    return projects.save_connection(
        {
            "app_id": app_id,
            "installation_id": installation_id,
            "account": account,
            "private_key": f"private-key-{app_id}",
            "webhook_secret": secret,
            "slug": f"app-{app_id}",
            "public_url": "https://nautionette.example.com",
            "installation_status": "connected",
            "workflows": True,
        }
    )


def start(**kwargs):
    target = github_setup.begin("https://nautionette.example.com", **kwargs)["start_url"]
    state = parse_qs(urlsplit(target).query)["state"][0]
    return state, github_setup.start(state)


def test_legacy_installation_and_every_project_migrate_once_without_touching_work(db, tmp_path):
    path = str(tmp_path / "legacy.db")
    database = Database(path)
    database.execute("ALTER TABLE projects DROP COLUMN connection_id")
    database.execute("DROP TABLE github_project_connections")
    config = {
        "app_id": "1",
        "installation_id": "10",
        "private_key": "legacy-key",
        "webhook_secret": "legacy-secret",
        "account": "personal",
        "workflows": True,
        "installation_status": "suspended",
        "slug": "legacy-app",
        "public_url": "https://nautionette.example.com",
    }
    database.set_setting("github_projects_app", config)
    webhook = {"last_received_at": 123, "event": "installation"}
    database.set_setting("github_projects_webhook", webhook)
    database.set_setting(github_setup.REGISTERED_SETTING, {"app_id": "2", "private_key": "pending"})
    for number, status in enumerate(["ready", "archived", "failed", "cloning"], 1):
        database.execute(
            "INSERT INTO projects VALUES (?,?,?,?,?,?)",
            (f"{number:032x}", number, f"owner/repo-{number}", "main", status, "kept-error"),
        )
    chat = database.create_chat("Existing work", "default")
    database.accept_chat_message(chat["id"], "keep working", "turn", [f"{1:032x}"])
    before = database.query("SELECT * FROM projects")
    leases = database.query("SELECT * FROM project_leases")
    database._conn.close()

    migrated = Database(path)
    try:
        assert migrated.query("SELECT * FROM projects") == [row | {"connection_id": "1-10"} for row in before]
        assert migrated.query("SELECT * FROM project_leases") == leases
        assert migrated.get_chat(chat["id"])["project_ids"] == [f"{1:032x}"]
        assert json.loads(migrated.one("SELECT config FROM github_project_connections")["config"]) == (
            config | {"webhook": webhook}
        )
        assert not migrated.get_setting("github_projects_app")
        assert not migrated.get_setting("github_projects_webhook")
        assert migrated.get_setting(github_setup.REGISTERED_SETTING)["private_key"] == "pending"
        # A restart must not undo subsequent edits or recreate the singleton.
        migrated.execute("UPDATE github_project_connections SET config = ?", (json.dumps(config),))
        migrated.execute("UPDATE projects SET connection_id = '2-20' WHERE repository_id = 2")
    finally:
        migrated._conn.close()
    restarted = Database(path)
    try:
        assert (
            restarted.one("SELECT connection_id FROM projects WHERE repository_id = 2")["connection_id"]
            == "2-20"
        )
        assert len(restarted.query("SELECT * FROM github_project_connections")) == 1
        assert json.loads(restarted.one("SELECT config FROM github_project_connections")["config"]) == config
    finally:
        restarted._conn.close()


def test_status_lists_all_connections_without_secrets_and_requires_explicit_choice(client, anonymous):
    personal = connect()
    org = connect("2", "20", "team", "org-secret")
    response = client.get("/api/projects/github-app")
    result = response.json()
    assert result["configured"] and not result["registered"]
    assert {item["id"] for item in result["connections"]} == {personal, org}
    assert {item["account"] for item in result["connections"]} == {"personal", "team"}
    for forbidden in ("private_key", "webhook_secret", "private-key", "personal-secret", "org-secret"):
        assert forbidden not in response.text
    for endpoint in ("/api/projects/github-app", "/api/projects/repositories?connection_id=1-10"):
        assert anonymous.get(endpoint).status_code == 401
    assert (
        anonymous.post("/api/projects", json={"full_name": "owner/repo", "connection_id": org}).status_code
        == 401
    )
    assert client.get("/api/projects/repositories").status_code == 422
    assert client.post("/api/projects", json={"full_name": "owner/repo"}).status_code == 422
    assert client.get("/api/projects/repositories?connection_id=missing").status_code == 404
    assert (
        client.post("/api/projects", json={"full_name": "owner/repo", "connection_id": "missing"}).status_code
        == 404
    )


@pytest.mark.asyncio
async def test_manual_configuration_adds_and_updates_only_matching_connection(monkeypatch):
    personal = connect()
    original = projects.app_config(personal)
    real_signer = projects.app_jwt
    monkeypatch.setattr(projects, "app_jwt", lambda config: f"jwt-{config['app_id']}")
    remote = AsyncMock(
        side_effect=[
            {"slug": "org-app"},
            {"app_id": 2, "account": {"login": "team"}, "permissions": {"contents": "write"}},
        ]
    )
    monkeypatch.setattr(projects, "github", remote)
    result = await projects.configure("2", "20", "org-private-key")
    assert result["id"] == "2-20" and result["account"] == "team"
    assert projects.app_config(personal) == original
    # Empty keys may only reuse credentials from the same App + installation.
    signer = Mock(side_effect=projects.app_jwt)
    monkeypatch.setattr(projects, "app_jwt", signer)
    remote.side_effect = [{"slug": "org-app"}, {"app_id": 2, "permissions": {"contents": "write"}}]
    await projects.configure("2", "20", "")
    assert signer.call_args.args[0]["private_key"] == "org-private-key"
    monkeypatch.setattr(projects, "app_jwt", real_signer)
    with pytest.raises(HTTPException) as error:
        await projects.configure("2", "21", "")
    assert error.value.status_code == 422
    assert len(projects.app_configs()) == 2
    assert projects.app_config(personal) == original


@pytest.mark.asyncio
async def test_new_registration_and_reconnect_keep_existing_connections(monkeypatch):
    personal = connect()
    original = projects.app_config(personal)
    state, flow = start(organization="team")
    assert "https://github.com/organizations/team/" in flow["url"]
    assert "manifest" in flow
    monkeypatch.setattr(projects, "app_jwt", lambda config: "jwt")
    remote = AsyncMock(
        return_value={
            "id": 2,
            "slug": "org-app",
            "pem": "org-private-key",
            "webhook_secret": "org-secret",
        }
    )
    monkeypatch.setattr(projects, "github", remote)
    await github_setup.convert(state, flow["browser"], "code")
    remote.return_value = {"app_id": 2, "permissions": {"contents": "write"}, "account": {"login": "team"}}
    await github_setup.installed(state, flow["browser"], "20")
    assert projects.app_config(personal) == original
    assert len(projects.app_configs()) == 2
    state, flow = start(connection_id="2-20")
    assert "manifest" not in flow
    await github_setup.installed(state, flow["browser"], "20")
    assert len(projects.app_configs()) == 2
    assert projects.app_config(personal) == original
    assert not github_setup.status()["registered"]


@pytest.mark.asyncio
async def test_additional_installation_of_same_app_does_not_copy_status_or_webhook(monkeypatch):
    personal = connect()
    config = projects.app_config(personal) | {
        "webhook": {"event": "installation"},
        "installation_status": "suspended",
    }
    projects.save_connection(config)
    state, flow = start(connection_id=personal)
    monkeypatch.setattr(projects, "app_jwt", lambda config: "jwt")
    monkeypatch.setattr(
        projects,
        "github",
        AsyncMock(
            return_value={
                "app_id": 1,
                "account": {"login": "team"},
                "permissions": {"contents": "write"},
            }
        ),
    )
    await github_setup.installed(state, flow["browser"], "11")
    assert projects.app_config(personal) == config
    assert projects.app_status("1-11")["configured"]
    assert projects.app_status("1-11")["webhook"] == {}


@pytest.mark.asyncio
async def test_pending_registration_can_be_bypassed_and_callbacks_finish_out_of_order(db, monkeypatch):
    monkeypatch.setattr(projects, "app_jwt", lambda config: "jwt")
    remote = AsyncMock(
        return_value={"id": 1, "slug": "personal", "pem": "key-1", "webhook_secret": "secret-1"}
    )
    monkeypatch.setattr(projects, "github", remote)
    first, first_flow = start()
    await github_setup.convert(first, first_flow["browser"], "code-1")
    second, second_flow = start(new_app=True, organization="team")
    assert "manifest" in second_flow
    remote.return_value = {"id": 2, "slug": "org", "pem": "key-2", "webhook_secret": "secret-2"}
    await github_setup.convert(second, second_flow["browser"], "code-2")
    remote.return_value = {"app_id": 1, "permissions": {"contents": "write"}}
    await github_setup.installed(first, first_flow["browser"], "10")
    assert github_setup.registered_app()["app_id"] == "2"
    remote.return_value = {"app_id": 2, "permissions": {"contents": "write"}}
    await github_setup.installed(second, second_flow["browser"], "20")
    assert {item["id"] for item in projects.app_configs()} == {"1-10", "2-20"}


@pytest.mark.asyncio
async def test_tokens_are_connection_and_repository_scoped_and_agents_use_saved_bindings(db, monkeypatch):
    personal = connect()
    org = connect("2", "20", "team", "org-secret")
    first = ready_project(db, "personal/private", 123, personal)
    second = ready_project(db, "team/private", 456, org)
    monkeypatch.setattr(projects, "app_jwt", lambda config: f"jwt-{config['app_id']}")

    async def github(method, path, token, **kwargs):
        assert method == "POST"
        installation = "10" if token == "jwt-1" else "20"
        assert path == f"/app/installations/{installation}/access_tokens"
        assert kwargs["json"]["permissions"] == {"contents": "write", "workflows": "write"}
        repository = kwargs["json"].get("repository_ids", [None])[0]
        return {"token": f"{installation}-{repository}", "expires_at": "2099-01-01T00:00:00Z"}

    remote = AsyncMock(side_effect=github)
    monkeypatch.setattr(projects, "github", remote)
    for repo in (None, 123):
        assert await projects.installation_token(repo, personal) == f"10-{repo}"
        assert await projects.installation_token(repo, org) == f"20-{repo}"
        assert await projects.installation_token(repo, personal) == f"10-{repo}"
    assert remote.await_count == 4
    credentials = await projects.agent_credentials([first, second])
    assert [item["token"] for item in credentials] == ["10-123", "20-456"]
    await projects.agent_credentials([first, second])
    assert remote.await_count == 8  # each agent turn gets fresh, individually revocable tokens
    projects.save_connection(projects.app_config(personal) | {"installation_status": "suspended"})
    with pytest.raises(HTTPException) as error:
        await projects.installation_token(123, personal)
    assert error.value.status_code == 409
    assert await projects.installation_token(123, org) == "20-123"
    assert remote.await_count == 8


@pytest.mark.asyncio
async def test_repository_listing_add_clone_and_retry_use_selected_connection(client, db, monkeypatch):
    connect()
    org = connect("2", "20", "team", "org-secret")
    repository = {"id": 123, "full_name": "team/private", "default_branch": "main", "private": True}
    token = AsyncMock(return_value="org-token")
    monkeypatch.setattr(projects, "installation_token", token)
    remote = AsyncMock(return_value={"total_count": 1, "repositories": [repository]})
    monkeypatch.setattr(projects, "github", remote)
    response = client.get("/api/projects/repositories", params={"page": 2, "connection_id": org})
    assert response.json() == {"connection_id": org, "total_count": 1, "repositories": [repository]}
    token.assert_awaited_with(connection_id=org)
    remote.assert_awaited_with("GET", "/installation/repositories?per_page=50&page=2", "org-token")
    remote.return_value = repository
    # Close the scheduled coroutine; exercise cloning deterministically below.
    monkeypatch.setattr(projects, "spawn", lambda coroutine, **kwargs: coroutine.close())
    response = client.post("/api/projects", json={"full_name": "team/private", "connection_id": org})
    assert response.status_code == 202
    project = response.json()
    assert project["connection_id"] == org
    token.assert_awaited_with(123, org)
    assert client.get("/api/projects").json()["projects"][0]["connection_id"] == org

    async def clone_git(*args, **kwargs):
        (projects.PROJECTS_DIR / (project["id"] + ".clone")).mkdir()

    monkeypatch.setattr(projects, "git", AsyncMock(side_effect=clone_git))
    await projects.clone(project)
    token.assert_awaited_with(123, org)
    assert projects.checkout(project["id"]).is_dir()
    # A second connection must not silently take over an existing repository/worktree.
    with pytest.raises(HTTPException) as error:
        await projects.add_repository("team/private", "1-10")
    assert error.value.status_code == 409
    projects.archive(project["id"])
    restored = await projects.add_repository("team/private", org)
    assert restored["id"] == project["id"] and restored["status"] == "ready"
    db.execute("UPDATE projects SET status = 'failed' WHERE id = ?", (project["id"],))
    retried = await projects.add_repository("team/private", org)
    assert retried["connection_id"] == org and retried["status"] == "cloning"


@pytest.mark.parametrize("same_app", [False, True])
def test_webhooks_target_only_matching_app_and_installation(db, anonymous, same_app):
    personal = connect(secret="secret")
    org = connect("1" if same_app else "2", "20", "team", "secret" if same_app else "org-secret")
    project = ready_project(db, "team/private", 123, org)
    projects._tokens[(personal, 123)] = ("personal-token", time.time() + 3000)
    projects._tokens[(org, 123)] = ("org-token", time.time() + 3000)
    payload = {"action": "suspend", "installation": {"id": 10}}
    assert send_webhook(anonymous, payload).json() == {"ok": True}
    assert projects.app_config(personal)["installation_status"] == "suspended"
    assert projects.app_status(org)["configured"]
    assert set(projects._tokens) == {(org, 123)}
    payload = {"installation": {"id": 10}, "repository": {"id": 123, "full_name": "wrong/name"}}
    assert send_webhook(anonymous, payload, event="push", delivery="rename").status_code == 200
    assert db.one("SELECT full_name FROM projects WHERE id = ?", (project,))["full_name"] == "team/private"
    if not same_app:
        # A valid signature for a different App cannot select this installation.
        payload["installation"]["id"] = 20
        assert send_webhook(anonymous, payload, event="push", delivery="foreign").json()["ignored"]
    assert projects.app_status(org)["webhook"] == {}
    assert projects.app_status(personal)["webhook"]["event"] == "push"


def test_ping_accepts_secrets_from_pending_setups_without_changing_connections(db, anonymous):
    personal = connect()
    original = projects.app_config(personal)
    db.execute(
        "INSERT INTO github_app_setups (state_hash, phase, expires_at, public_url, organization, config) "
        "VALUES ('pending', 'installation', ?, '', '', ?)",
        (time.time() + 100, json.dumps({"app_id": "2", "webhook_secret": "pending-secret"})),
    )
    assert send_webhook(anonymous, {}, event="ping", secret=b"pending-secret").json() == {"ok": True}
    assert projects.app_config(personal) == original
    assert send_webhook(anonymous, {}, event="ping", secret=b"pending-secret").json()["duplicate"]
