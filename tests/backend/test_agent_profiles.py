"""Defaults and saved agents are presets, never live pointers into a running chat."""

import json
import sqlite3
from unittest.mock import AsyncMock

import pytest
from nautionette_backend import agent_profiles, catalog, projects, runtime
from nautionette_backend.db import Database


@pytest.fixture(autouse=True)
def configured(backend, monkeypatch, tmp_path):
    models = [
        {"id": "test/reasoner", "gateway": "test", "reasoning_efforts": ["low", "high", "max"]},
        {"id": "test/other", "gateway": "test", "reasoning_efforts": ["low", "high"]},
        {"id": "test/plain", "gateway": "test", "reasoning_efforts": []},
    ]
    monkeypatch.setattr(catalog, "model_catalog", AsyncMock(return_value=models))
    monkeypatch.setattr(projects, "PROJECTS_DIR", tmp_path)
    monkeypatch.setattr(projects, "agent_credentials", AsyncMock(return_value=[]))
    project_id = "a" * 32
    projects.checkout(project_id).mkdir()
    backend.db.execute(
        "INSERT INTO projects VALUES (?,?,?,?,?,?)", (project_id, 1, "owner/repo", "main", "ready", "")
    )
    return project_id


def create(client, config=None, name="Research"):
    response = client.post("/api/agents", json={"name": name, "config": config or {}})
    assert response.status_code == 201, response.text
    return response.json()


def save_defaults(client, **kwargs):
    response = client.put("/api/settings", json=kwargs)
    assert response.status_code == 200, response.text
    return response.json()["settings"]


def new_chat(client, **kwargs):
    response = client.post("/api/chats", json=kwargs)
    assert response.status_code == 200, response.text
    return response.json()


def test_global_defaults_round_trip_as_native_types_and_reach_every_new_chat(client, configured):
    values = {
        "default_model": "test/reasoner",
        "default_agent_set": "research",
        "default_reasoning_effort": "high",
        "default_tools": ["search"],
        "default_project_ids": [configured],
    }
    saved = save_defaults(client, **values)
    assert all(saved[key] == value for key, value in values.items())
    loaded = client.get("/api/settings").json()["settings"]
    assert loaded == saved
    expected = {key: values[setting] for key, setting in agent_profiles.SETTING_KEYS.items()}
    available = client.get("/api/catalog").json()
    assert available["global_chat_defaults"] == expected
    assert available["chat_defaults"] == {**expected, "agent_id": None, "agent_name": None}
    chat = new_chat(client)
    assert {key: chat[key] for key in agent_profiles.CONFIG_KEYS} == expected
    response = client.post(f"/api/chats/{chat['id']}/messages", json={"text": "Hello", "message_id": "turn"})
    assert response.status_code == 200
    assert client.get(f"/api/chats/{chat['id']}").json()["chat"]["project_ids"] == [configured]


@pytest.mark.parametrize("tools", [None, [], ["search"], ["offline_tool"]])
def test_tool_selections_never_collapse_to_all_and_reach_the_durable_job(client, backend, tools):
    save_defaults(client, default_tools=tools)
    agent = create(client, {"tools": tools})
    chat = new_chat(client, agent_id=agent["id"])
    assert chat["tools"] == tools
    assert client.get("/api/chats").json()["chats"][0]["tools"] == tools
    assert client.post(
        f"/api/chats/{chat['id']}/messages", json={"text": "Hi", "message_id": "tools"}
    ).is_success
    job = json.loads(backend.db.one("SELECT job FROM chat_turns WHERE id = 'tools'")["job"])
    assert job["tools"] == tools
    assert backend.broker.jobs[-1]["tools"] == tools


def test_agents_inherit_by_omission_and_explicit_choices_override_each_field(client, configured):
    save_defaults(
        client,
        default_model="test/reasoner",
        default_reasoning_effort="high",
        default_tools=["search"],
        default_project_ids=[configured],
    )
    agent = create(client, {"tools": [], "project_ids": [], "reasoning_effort": None})
    assert agent["config"] == {"tools": [], "project_ids": [], "reasoning_effort": None}
    assert agent["resolved"] == {
        "model": "test/reasoner",
        "agent_set": "default",
        "tools": [],
        "project_ids": [],
        "reasoning_effort": None,
    }
    save_defaults(client, default_agent_id=agent["id"])
    first = new_chat(client)
    assert first["agent_id"] == agent["id"]
    assert first["agent_name"] == "Research"
    assert first["tools"] == [] and first["project_ids"] == [] and first["reasoning_effort"] is None
    save_defaults(client, default_model="test/other", default_tools=None)
    second = new_chat(client)
    assert second["model"] == "test/other" and second["tools"] == []
    assert client.get(f"/api/chats/{first['id']}").json()["chat"]["model"] == "test/reasoner"
    assert client.get(f"/api/agents/{agent['id']}").json()["config"] == agent["config"]


def test_direct_api_chat_overrides_and_explicit_global_selection_beat_the_default_agent(client, configured):
    save_defaults(
        client, default_model="test/reasoner", default_reasoning_effort="max", default_tools=["search"]
    )
    agent = create(client, {"model": "test/other", "tools": [], "project_ids": [configured]})
    save_defaults(client, default_agent_id=agent["id"])
    chat = new_chat(client, tools=None, project_ids=[], reasoning_effort="low")
    assert chat["agent_id"] == agent["id"] and chat["tools"] is None
    assert chat["project_ids"] == [] and chat["reasoning_effort"] == "low"
    baseline = new_chat(client, agent_id=None)
    assert baseline["agent_id"] is None and baseline["model"] == "test/reasoner"
    assert baseline["tools"] == ["search"] and baseline["project_ids"] == []
    assert baseline["reasoning_effort"] == "max"


def test_profile_edits_and_deletion_do_not_change_chats_or_accepted_turns(client, backend):
    agent = create(client, {"model": "test/reasoner", "reasoning_effort": "max", "tools": []})
    save_defaults(client, default_agent_id=agent["id"])
    chat = new_chat(client)
    backend.db.execute("UPDATE chats SET queue_paused = 1 WHERE id = ?", (chat["id"],))
    url = f"/api/chats/{chat['id']}"
    response = client.post(url + "/messages", json={"text": "Wait", "message_id": "queued", "queue": True})
    assert response.status_code == 202
    job = json.loads(backend.db.one("SELECT job FROM chat_turns WHERE id = 'queued'")["job"])
    assert job["model"] == "test/reasoner" and job["reasoning_effort"] == "max" and job["tools"] == []
    changed = client.patch(f"/api/agents/{agent['id']}", json={"name": "Writer", "config": {"tools": None}})
    assert changed.status_code == 200
    assert client.get(url).json()["chat"]["agent_name"] == "Research"
    assert client.get(url).json()["chat"]["tools"] == []
    assert new_chat(client)["tools"] is None
    assert client.patch(url, json={"agent_id": agent["id"]}).json()["agent_name"] == "Writer"
    assert json.loads(backend.db.one("SELECT job FROM chat_turns WHERE id = 'queued'")["job"]) == job
    snapshot = client.get(url).json()["chat"]
    assert client.delete(f"/api/agents/{agent['id']}").status_code == 200
    assert client.get(url).json()["chat"] == snapshot
    assert new_chat(client)["agent_id"] is None
    assert client.get("/api/settings").json()["settings"]["default_agent_id"] is None
    assert json.loads(backend.db.one("SELECT job FROM chat_turns WHERE id = 'queued'")["job"]) == job


def test_selecting_an_agent_replaces_the_configuration_atomically_and_can_be_reapplied(client, configured):
    agent = create(
        client,
        {"model": "test/reasoner", "reasoning_effort": "high", "tools": [], "project_ids": [configured]},
    )
    chat = new_chat(client, model="test/plain", tools=["search"])
    url = f"/api/chats/{chat['id']}"
    selected = client.patch(url, json={"agent_id": agent["id"]}).json()
    assert selected["tools"] == [] and selected["project_ids"] == [configured]
    assert selected["model"] == "test/reasoner" and selected["reasoning_effort"] == "high"
    customized = client.patch(url, json={"tools": None, "project_ids": []}).json()
    assert customized["tools"] is None and customized["agent_id"] == agent["id"]
    assert client.patch(url, json={"agent_id": agent["id"]}).json()["tools"] == []
    explicit = client.patch(
        url, json={"agent_id": agent["id"], "model": "test/plain", "project_ids": []}
    ).json()
    assert explicit["reasoning_effort"] is None and explicit["project_ids"] == []
    restored = client.patch(url, json={"agent_id": None}).json()
    assert restored["agent_id"] is None and restored["agent_name"] is None
    assert restored["tools"] is None and restored["project_ids"] == []


def test_reasoning_is_bound_to_its_model_in_every_inheritance_layer(client):
    save_defaults(client, default_model="test/reasoner", default_reasoning_effort="max")
    inherited = create(client)
    assert inherited["resolved"]["reasoning_effort"] == "max"
    other = create(client, {"model": "test/other"}, name="Other")
    assert other["resolved"]["reasoning_effort"] is None
    assert new_chat(client, agent_id=inherited["id"], model="test/plain")["reasoning_effort"] is None
    assert (
        client.post(
            "/api/agents",
            json={"name": "Invalid", "config": {"model": "test/other", "reasoning_effort": "max"}},
        ).status_code
        == 422
    )
    save_defaults(client, default_model="test/other")
    assert client.get("/api/settings").json()["settings"]["default_reasoning_effort"] is None
    assert client.get(f"/api/agents/{inherited['id']}").json()["resolved"]["reasoning_effort"] is None


def test_clearing_override_keys_restores_inheritance_without_losing_explicit_null(client):
    agent = create(client, {"tools": [], "model": "test/other"})
    save_defaults(
        client, default_tools=["read"], default_model="test/reasoner", default_reasoning_effort="high"
    )
    assert (
        client.patch(f"/api/agents/{agent['id']}", json={"description": "Notes"}).json()["config"]
        == agent["config"]
    )
    inherited = client.patch(f"/api/agents/{agent['id']}", json={"config": {}}).json()
    assert inherited["resolved"]["tools"] == ["read"] and inherited["resolved"]["reasoning_effort"] == "high"
    assert (
        client.patch(f"/api/agents/{agent['id']}", json={"config": {"tools": None}}).json()["resolved"][
            "tools"
        ]
        is None
    )


def test_stale_project_defaults_fail_closed_and_can_be_overridden_or_cleared(client, backend, configured):
    agent = create(client, {"project_ids": [configured], "tools": []})
    save_defaults(client, default_agent_id=agent["id"])
    backend.db.execute("UPDATE projects SET status = 'archived'")
    assert client.post("/api/chats", json={}).status_code == 422
    assert client.get("/api/chats").json()["chats"] == []
    assert new_chat(client, project_ids=[])["project_ids"] == []
    assert client.patch(f"/api/agents/{agent['id']}", json={"name": "Still saved"}).status_code == 200
    assert client.patch(f"/api/agents/{agent['id']}", json={"config": {"project_ids": []}}).status_code == 200
    assert new_chat(client)["project_ids"] == []


@pytest.mark.parametrize(
    "invalid",
    [
        {"tools": "all"},
        {"tools": [42]},
        {"tools": [""]},
        {"tools": False},
        {"tools": {}},
        {"project_ids": None},
        {"project_ids": ["missing"]},
        {"project_ids": [False]},
        {"model": None},
        {"model": False},
        {"model": " "},
        {"agent_set": "../oops"},
        {"reasoning_effort": False},
        {"reasoning_effort": "invalid"},
        {"reasoning_effort": {}},
        {"unexpected": True},
    ],
)
def test_invalid_profile_configs_are_rejected_before_writing_any_fields(client, invalid):
    agent = create(client)
    response = client.patch(f"/api/agents/{agent['id']}", json={"name": "Changed", "config": invalid})
    assert response.status_code == 422, response.text
    assert client.get(f"/api/agents/{agent['id']}").json() == agent
    assert client.post("/api/agents", json={"name": "New", "config": invalid}).status_code == 422
    assert len(client.get("/api/agents").json()["agents"]) == 1


@pytest.mark.parametrize(
    "invalid",
    [
        {"default_tools": "all"},
        {"default_tools": [False]},
        {"default_project_ids": "all"},
        {"default_project_ids": ["missing"]},
        {"default_reasoning_effort": "invalid"},
        {"default_agent_id": False},
        {"history_chars": {}},
        {"default_model": False},
    ],
)
def test_invalid_global_edits_are_atomic(client, invalid):
    before = client.get("/api/settings").json()
    response = client.put("/api/settings", json={"default_agent_set": "research", **invalid})
    assert response.status_code == 422, response.text
    assert client.get("/api/settings").json() == before


def test_missing_agents_never_silently_fall_back_on_explicit_selection(client):
    chat = new_chat(client)
    url = f"/api/chats/{chat['id']}"
    assert client.post("/api/chats", json={"agent_id": "missing"}).status_code == 404
    assert client.patch(url, json={"agent_id": "missing", "title": "Changed"}).status_code == 404
    assert client.get(url).json()["chat"]["title"] == "New chat"
    assert client.put("/api/settings", json={"default_agent_id": "missing"}).status_code == 404
    assert client.get("/api/settings").json()["settings"]["default_agent_id"] is None


def test_duplicate_names_validation_and_authentication(client, anonymous):
    agent = create(client, name=" Research ")
    assert agent["name"] == "Research"
    assert client.post("/api/agents", json={"name": "research"}).status_code == 409
    for name in [None, "", " ", "x" * 81, "Global defaults"]:
        assert client.post("/api/agents", json={"name": name}).status_code == 422
    for method, url, body in [
        ("get", "/api/agents", None),
        ("post", "/api/agents", {"name": "No"}),
        ("get", f"/api/agents/{agent['id']}", None),
        ("patch", f"/api/agents/{agent['id']}", {"name": "No"}),
        ("delete", f"/api/agents/{agent['id']}", None),
    ]:
        assert anonymous.request(method, url, json=body).status_code == 401


def test_agent_mutations_invalidate_the_catalog_and_expose_resolved_defaults(client):
    client.get("/api/catalog")
    assert runtime.cached_catalog()
    agent = create(client, {"tools": []})
    assert runtime.cached_catalog() is None
    save_defaults(client, default_agent_id=agent["id"])
    result = client.get("/api/catalog").json()
    assert result["chat_defaults"]["tools"] == []
    assert result["agents"][0]["config"] == {"tools": []}
    assert client.delete(f"/api/agents/{agent['id']}").is_success
    assert runtime.cached_catalog() is None
    assert client.get("/api/catalog").json()["chat_defaults"]["tools"] is None


def test_resetting_global_settings_leaves_agents_and_explicit_empty_selections_intact(client, configured):
    agent = create(client, {"tools": []})
    save_defaults(client, default_agent_id=agent["id"], default_tools=[], default_project_ids=[configured])
    cleared = save_defaults(
        client, **{key: None for key in ["default_agent_id", *agent_profiles.SETTING_KEYS.values()]}
    )
    assert cleared["default_tools"] is None and cleared["default_project_ids"] == []
    assert cleared["default_agent_id"] is None
    assert client.get(f"/api/agents/{agent['id']}").json()["config"]["tools"] == []


def test_legacy_chats_migrate_without_adopting_new_defaults(tmp_path):
    path = str(tmp_path / "legacy.db")
    with sqlite3.connect(path) as connection:
        connection.execute(
            "CREATE TABLE chats (id TEXT PRIMARY KEY, title TEXT, agent_set TEXT,"
            " created_at REAL, updated_at REAL, promoted_to TEXT)"
        )
        connection.execute("INSERT INTO chats VALUES ('legacy', 'Legacy', 'default', 1, 1, NULL)")
    database = Database(path)
    database.set_setting("default_tools", ["search"])
    chat = database.get_chat("legacy")
    assert chat["agent_id"] is None and chat["agent_name"] is None
    assert chat["tools"] is None and chat["project_ids"] == []
    database._conn.close()
    reopened = Database(path)
    assert reopened.get_chat("legacy") == chat
    reopened._conn.close()
