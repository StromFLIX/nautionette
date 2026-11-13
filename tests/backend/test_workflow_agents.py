"""Workflow profile tasks retain chat isolation and idempotent admission."""

import json
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException
from nautionette_backend import workflow_agents

KEY = "a" * 64


@pytest.fixture
def profile(client):
    return client.post(
        "/api/agents",
        json={
            "name": "Marvin test",
            "config": {"model": "test/saved", "tools": [], "system_prompt": "Persona"},
        },
    ).json()


def test_profile_validation_is_internal_and_does_not_create_chat(client, internal_headers, profile, db):
    url = f"/internal/agent/tasks/{KEY}"
    payload = {"agent_id": profile["id"], "prompt": "Check", "validate_only": True}
    assert client.post(url, json=payload).status_code == 401
    result = client.post(url, headers=internal_headers, json=payload).json()
    assert result["validated"] and result["model"] == "test/saved"
    assert result["agent_id"] == profile["id"]
    assert db.list_chats() == []
    assert client.get(url).status_code == 401


async def test_profile_task_reconnects_without_restarting_completed_turn(client, profile, db, monkeypatch):
    async def accept(chat_id, payload):
        message, created = db.accept_chat_message(chat_id, payload["text"], payload["message_id"], [])
        if created:
            db.execute(
                "UPDATE chat_turns SET job = ? WHERE id = ?",
                (json.dumps({"agent_id": profile["id"]}), message["id"]),
            )
            db.finish_chat_turn(message["id"], '{"summary":"done"}', {})

    monkeypatch.setattr(workflow_agents, "submit_message", accept)
    first = await workflow_agents.start(KEY, profile["id"], "Update")
    again = await workflow_agents.start(KEY, profile["id"], "Update")
    assert first == again and first["ok"]
    assert len(db.list_messages(first["chat_id"])) == 2
    chat = db.get_chat(first["chat_id"])
    assert chat["agent_id"] == profile["id"]
    assert chat["model"] == "test/saved" and chat["system_prompt"] == "Persona"
    assert chat["tools"] == [] and chat["timeout_exempt"] is True
    with pytest.raises(HTTPException, match="different agent request"):
        await workflow_agents.start(KEY, profile["id"], "Different")
    # Completed tasks remain readable after a profile is removed; no fresh model
    # discovery/admission is needed merely to reconnect to persisted results.
    client.delete(f"/api/agents/{profile['id']}")
    assert await workflow_agents.start(KEY, profile["id"], "Update") == first
    # Deleting a task chat must never turn a retry into a second mutating task.
    db.delete_chat(first["chat_id"])
    with pytest.raises(HTTPException, match="refusing to replay") as deleted:
        await workflow_agents.start(KEY, profile["id"], "Update")
    assert deleted.value.status_code == 410
    assert db.list_chats() == []
    with pytest.raises(HTTPException, match="refusing to replay"):
        workflow_agents.status(KEY)


async def test_task_uses_normal_submit_and_preserves_profile_projects(client, profile, db, monkeypatch):
    submit = AsyncMock()
    monkeypatch.setattr(workflow_agents, "submit_message", submit)
    monkeypatch.setattr(workflow_agents, "status", lambda key: {"state": "running"})
    await workflow_agents.start(KEY, profile["id"], "Check")
    chat_id, turn_id = workflow_agents.task_ids(KEY)
    submit.assert_awaited_once_with(chat_id, {"text": "Check", "message_id": turn_id})
    assert db.get_chat(chat_id)["agent_id"] == profile["id"]


@pytest.mark.parametrize("error,stopped", [("container failed", False), (None, True)])
async def test_completed_chat_with_error_or_stop_is_failure(client, profile, db, monkeypatch, error, stopped):
    async def accept(chat_id, payload):
        _, created = db.accept_chat_message(chat_id, payload["text"], payload["message_id"], [])
        if created:
            db.finish_chat_turn(payload["message_id"], "not done", {"error": error, "stopped": stopped})

    monkeypatch.setattr(workflow_agents, "submit_message", accept)
    assert (await workflow_agents.start(KEY, profile["id"], "Check"))["ok"] is False


def test_missing_or_invalid_task_fails(client, internal_headers):
    assert client.get(f"/internal/agent/tasks/{KEY}", headers=internal_headers).status_code == 404
    assert client.get("/internal/agent/tasks/invalid", headers=internal_headers).status_code == 422
    assert (
        client.post(
            f"/internal/agent/tasks/{KEY}",
            headers=internal_headers,
            json={"agent_id": "missing", "prompt": "check", "validate_only": True},
        ).status_code
        == 404
    )
