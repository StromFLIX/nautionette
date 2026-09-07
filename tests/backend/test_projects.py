from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException
from nautionette_backend import projects
from nautionette_backend.db import Database


@pytest.fixture(autouse=True)
def project_storage(db, tmp_path, monkeypatch):
    db.execute("DELETE FROM project_leases")
    db.execute("DELETE FROM projects")
    projects._tokens.clear()
    monkeypatch.setattr(projects, "PROJECTS_DIR", tmp_path)


def ready_project(db, full_name="owner/repository", repository_id=123):
    project_id = f"{repository_id:032x}"
    projects.checkout(project_id).mkdir()
    db.execute(
        "INSERT INTO projects VALUES (?,?,?,?,?,?)",
        (project_id, repository_id, full_name, "main", "ready", ""),
    )
    return project_id


def test_project_settings_are_user_only_and_never_return_private_key(client, anonymous, db):
    db.set_setting(
        projects.APP_SETTING, {"app_id": "1", "installation_id": "2", "private_key": "secret", "slug": "app"}
    )
    result = client.get("/api/projects/github-app")
    assert result.json()["configured"] is True
    assert "secret" not in result.text and "private_key" not in result.text
    assert anonymous.get("/api/projects").status_code == 401
    assert anonymous.put("/api/projects/github-app", json={}).status_code == 401


def test_selection_requires_ready_checkouts(db):
    project_id = ready_project(db)
    assert projects.selection([project_id, project_id]) == [project_id]
    for selection in (None, "all", ["../escape"], ["missing"], [[]]):
        with pytest.raises(HTTPException):
            projects.selection(selection)
    db.execute("UPDATE projects SET status = 'cloning'")
    with pytest.raises(HTTPException):
        projects.selection([project_id])


@pytest.mark.asyncio
async def test_agents_receive_fresh_repository_scoped_tokens_then_revoke_them(db, monkeypatch):
    selected = ready_project(db)
    ready_project(db, "owner/other", 456)
    db.set_setting(projects.APP_SETTING, {"app_id": "1", "installation_id": "2"})
    monkeypatch.setattr(projects, "app_jwt", lambda config: "jwt")
    remote = AsyncMock(return_value={"token": "installation-secret", "expires_at": "2099-01-01T00:00:00Z"})
    monkeypatch.setattr(projects, "github", remote)
    credentials = await projects.agent_credentials([selected])
    assert credentials == [
        {
            "full_name": "owner/repository",
            "token": "installation-secret",
            "expires_at": "2099-01-01T00:00:00Z",
        }
    ]
    assert remote.call_args.kwargs["json"]["repository_ids"] == [123]
    await projects.agent_credentials([selected])
    assert remote.await_count == 2
    await projects.revoke_credentials(credentials)
    remote.assert_awaited_with("DELETE", "/installation/token", "installation-secret")


def test_message_selection_is_idempotent_and_allows_concurrent_chats(db):
    project_id = ready_project(db)
    first = db.create_chat("First", "default")
    second = db.create_chat("Second", "default")
    message, created = db.accept_chat_message(first["id"], "edit", "turn", [project_id])
    assert created and message["meta"]["project_ids"] == [project_id]
    assert db.accept_chat_message(first["id"], "edit", "turn", [project_id])[1] is False
    with pytest.raises(ValueError):
        db.accept_chat_message(first["id"], "edit", "turn", [])
    assert db.accept_chat_message(second["id"], "edit", "other", [project_id])[1] is True
    assert len(db.query("SELECT * FROM project_leases")) == 2
    db.finish_chat_turn("turn", "done", {})
    assert db.accept_chat_message(second["id"], "edit", "other", [project_id])[1] is False
    assert len(db.query("SELECT * FROM project_leases")) == 1


def test_worktree_paths_are_per_chat_and_retained_between_turns(db):
    project_id = ready_project(db)
    first = db.create_chat("First", "default")
    second = db.create_chat("Second", "default")
    for chat in (first, second):
        assert projects.prepare_worktrees(chat["id"], [project_id]) == {
            "project_baselines": {project_id: "main"},
            "project_remotes": {project_id: "owner/repository"},
        }
    first_path = projects.PROJECTS_DIR / ".sessions" / project_id / first["id"]
    second_path = projects.PROJECTS_DIR / ".sessions" / project_id / second["id"]
    (first_path / "change.txt").write_text("unfinished")
    projects.prepare_worktrees(first["id"], [project_id])
    assert (first_path / "change.txt").read_text() == "unfinished"
    assert not (second_path / "change.txt").exists()


def test_chat_job_contains_only_selected_projects_and_retry_is_stable(client, db, broker, monkeypatch):
    monkeypatch.setattr(projects, "agent_credentials", AsyncMock(return_value=[]))
    project_id = ready_project(db)
    created = client.post("/api/chats", json={"project_ids": [project_id]}).json()
    assert created["project_ids"] == [project_id]
    endpoint = f"/api/chats/{created['id']}/messages"
    response = client.post(endpoint, json={"text": "edit", "message_id": "turn"})
    assert response.status_code == 200
    job = broker.jobs[-1]
    assert job["project_ids"] == [project_id]
    assert job["internet_allowed"] is False
    assert "not automatically for gh, curl, or MCP GitHub tools" in job["system_prompt"]
    assert (
        "A GitHub API or MCP 403 does not establish that Git push lacks write access" in job["system_prompt"]
    )
    assert "request_internet_access and wait; after approval retry" in job["system_prompt"]
    message = db.list_messages(created["id"])[0]
    assert message["meta"]["project_ids"] == [project_id]
    assert client.get(f"/api/chats/{created['id']}").json()["chat"]["project_ids"] == [project_id]
    assert client.post(endpoint, json={"text": "continue", "message_id": "follow-up"}).status_code == 200
    assert broker.jobs[-1]["project_ids"] == [project_id]
    assert client.patch(f"/api/chats/{created['id']}", json={"project_ids": []}).status_code == 200
    assert client.post(endpoint, json={"text": "edit", "message_id": "turn"}).status_code == 200
    assert client.get(f"/api/chats/{created['id']}").json()["chat"]["project_ids"] == []
    assert (
        client.post(endpoint, json={"text": "without projects", "message_id": "cleared"}).status_code == 200
    )
    assert broker.jobs[-1]["project_ids"] == []
    assert (
        client.post(endpoint, json={"text": "edit", "message_id": "turn", "project_ids": []}).status_code
        == 422
    )


def test_archiving_keeps_working_tree_and_refuses_busy_projects(client, db):
    project_id = ready_project(db)
    chat = db.create_chat("Projects", "default")
    db.accept_chat_message(chat["id"], "edit", "turn")
    db.execute("INSERT INTO project_leases VALUES (?,?)", (project_id, "turn"))
    assert client.delete(f"/api/projects/{project_id}").status_code == 409
    db.execute("DELETE FROM project_leases")
    assert client.delete(f"/api/projects/{project_id}").status_code == 200
    assert client.get("/api/projects").json() == {"projects": []}
    assert projects.checkout(project_id).is_dir()


@pytest.mark.asyncio
async def test_installation_tokens_are_repository_scoped_and_refreshed(db, monkeypatch):
    db.set_setting(projects.APP_SETTING, {"app_id": "1", "installation_id": "2"})
    monkeypatch.setattr(projects, "app_jwt", lambda config: "jwt")
    remote = AsyncMock(return_value={"token": "installation-secret"})
    monkeypatch.setattr(projects, "github", remote)
    assert await projects.installation_token(123) == "installation-secret"
    assert remote.call_args.kwargs["json"] == {"permissions": {"contents": "write"}, "repository_ids": [123]}
    await projects.installation_token(123)
    assert remote.await_count == 1
    projects._tokens[123] = ("old", 0)
    await projects.installation_token(123)
    assert remote.await_count == 2


def test_old_exclusive_reservations_migrate_without_losing_data(tmp_path):
    path = str(tmp_path / "migration.db")
    database = Database(path)
    database.execute("DROP TABLE project_leases")
    database.execute(
        "CREATE TABLE project_leases (project_id TEXT PRIMARY KEY REFERENCES projects(id), "
        "turn_id TEXT NOT NULL REFERENCES chat_turns(id) ON DELETE CASCADE)"
    )
    project_id = "a" * 32
    database.execute(
        "INSERT INTO projects VALUES (?,?,?,?,?,?)", (project_id, 1, "owner/repo", "main", "ready", "")
    )
    first = database.create_chat("First", "default")
    second = database.create_chat("Second", "default")
    database.accept_chat_message(first["id"], "edit", "turn", [project_id])
    database._conn.close()
    migrated = Database(path)
    try:
        assert migrated.query("SELECT * FROM project_leases") == [
            {"project_id": project_id, "turn_id": "turn"}
        ]
        assert migrated.accept_chat_message(second["id"], "edit", "other", [project_id])[1] is True
    finally:
        migrated._conn.close()
