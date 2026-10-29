"""Agent membership is independent of historical selections or later preset edits."""

import base64
import io
import json

import pytest
from PIL import Image

from .test_agent_profiles import configured, create, new_chat  # noqa: F401 - shared fixture


def patch(client, chat, **fields):
    response = client.patch(f"/api/chats/{chat['id']}", json=fields)
    assert response.is_success, response.text
    return response.json()


def test_only_latest_selection_counts_and_undoing_customization_does_not_restore_membership(client):
    writer = create(client, {"tools": []}, "Writer")
    researcher = create(client, {"tools": ["search"]}, "Researcher")
    chat = new_chat(client, agent_id=writer["id"])
    assert not chat["agent_customized"]
    assert not patch(client, chat, title="Renamed")["agent_customized"]
    assert not patch(client, chat, tools=[])["agent_customized"]  # a no-op is not customization
    assert patch(client, chat, tools=None)["agent_customized"]
    assert patch(client, chat, tools=[])["agent_customized"]
    restored = patch(client, chat, agent_id=writer["id"])
    assert not restored["agent_customized"]
    selected = patch(client, chat, agent_id=researcher["id"])
    assert selected["agent_id"] == researcher["id"] and not selected["agent_customized"]
    assert selected["tools"] == ["search"]
    custom = patch(client, chat, agent_id=writer["id"], tools=["different"])
    assert custom["agent_id"] == writer["id"] and custom["agent_customized"]
    assert client.get("/api/chats").json()["chats"][0]["agent_customized"]


def test_creation_overrides_and_remembered_customization_cannot_join_agent_group(client):
    agent = create(client, {"tools": []})
    assert not new_chat(client, agent_id=agent["id"], tools=[])["agent_customized"]
    assert new_chat(client, agent_id=agent["id"], tools=None)["agent_customized"]
    assert new_chat(client, agent_id=agent["id"], tools=[], agent_customized=True)["agent_customized"]
    assert client.post("/api/chats", json={"agent_customized": "yes"}).status_code == 422


def test_profile_edits_leave_membership_and_persona_snapshots_unchanged(client, backend):
    agent = create(client, {"tools": [], "system_prompt": "You are Ada. Explain clearly."})
    chat = new_chat(client, agent_id=agent["id"])
    backend.db.execute("UPDATE chats SET queue_paused = 1 WHERE id = ?", (chat["id"],))
    response = client.post(
        f"/api/chats/{chat['id']}/messages",
        json={"text": "Hello", "message_id": "persona-turn", "queue": True},
    )
    assert response.status_code == 202
    job = json.loads(backend.db.one("SELECT job FROM chat_turns WHERE id = 'persona-turn'")["job"])
    assert "Agent instructions:\nYou are Ada. Explain clearly." in job["system_prompt"]
    assert len(job["system_prompt"]) > len(chat["system_prompt"])  # retains core operating rules
    assert client.patch(
        f"/api/agents/{agent['id']}", json={"name": "Ada", "config": {"system_prompt": "New persona"}}
    ).is_success
    saved = client.get(f"/api/chats/{chat['id']}").json()["chat"]
    assert saved["agent_id"] == agent["id"] and not saved["agent_customized"]
    assert saved["system_prompt"] == "You are Ada. Explain clearly."
    assert new_chat(client, agent_id=agent["id"])["system_prompt"] == "New persona"
    assert patch(client, chat, system_prompt="Custom persona")["agent_customized"]
    assert patch(client, chat, agent_id=agent["id"])["system_prompt"] == "New persona"
    assert json.loads(backend.db.one("SELECT job FROM chat_turns WHERE id = 'persona-turn'")["job"]) == job


def image_data():
    output = io.BytesIO()
    Image.new("RGB", (640, 480), "purple").save(output, format="PNG")
    return "data:image/png;base64," + base64.b64encode(output.getvalue()).decode()


def test_portraits_are_bounded_raster_images_roundtrip_and_clear_without_changing_chats(client):
    response = client.post("/api/agents", json={"name": "Ada", "image": image_data()})
    assert response.status_code == 201, response.text
    agent = response.json()
    assert agent["image"].startswith("data:image/webp;base64,")
    with Image.open(io.BytesIO(base64.b64decode(agent["image"].split(",")[1]))) as image:
        assert image.width <= 256 and image.height <= 256
        assert not image.getexif()
    assert client.get("/api/catalog").json()["agents"][0]["image"] == agent["image"]
    chat = new_chat(client, agent_id=agent["id"])
    assert "image" not in chat
    changed = client.patch(f"/api/agents/{agent['id']}", json={"description": "A person"}).json()
    assert changed["image"] == agent["image"]
    assert client.patch(f"/api/agents/{agent['id']}", json={"image": ""}).json()["image"] == ""
    assert not client.get(f"/api/chats/{chat['id']}").json()["chat"]["agent_customized"]


@pytest.mark.parametrize(
    "value",
    [
        None,
        False,
        {},
        "https://example.com/avatar.png",
        "data:image/svg+xml;base64,abc",
        "data:image/png;base64,bm90LWFuLWltYWdl",
        "data:image/png;base64,a",
    ],
)
def test_invalid_portrait_edits_are_atomic(client, value):
    agent = create(client)
    response = client.patch(f"/api/agents/{agent['id']}", json={"name": "Changed", "image": value})
    assert response.status_code == 422
    assert client.get(f"/api/agents/{agent['id']}").json() == agent


def test_legacy_grouping_is_classified_once_then_survives_profile_edits_and_restarts(tmp_path):
    from nautionette_backend.db import Database

    path = str(tmp_path / "agents.db")
    database = Database(path)
    database.execute(
        "INSERT INTO agent_profiles (id, name, config, created_at, updated_at) VALUES (?,?,?,?,?)",
        ("writer", "Writer", json.dumps({"tools": []}), 1, 1),
    )
    clean = database.create_chat("Clean", "default", model="test/model", tools=[], agent_id="writer")
    custom = database.create_chat("Custom", "default", model="test/model", tools=None, agent_id="writer")
    database.set_setting("default_model", "test/model")
    database.execute("ALTER TABLE chats DROP COLUMN agent_customized")
    database._conn.close()
    upgraded = Database(path)
    assert not upgraded.get_chat(clean["id"])["agent_customized"]
    assert upgraded.get_chat(custom["id"])["agent_customized"]
    upgraded.execute("UPDATE agent_profiles SET config = '{}' ")
    upgraded._conn.close()
    restarted = Database(path)
    assert not restarted.get_chat(clean["id"])["agent_customized"]
    assert restarted.get_chat(custom["id"])["agent_customized"]
    restarted._conn.close()
