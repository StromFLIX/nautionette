"""Attribution is backend-owned and independent of workflow/model prompt text."""

from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException
from nautionette_backend import provenance

SOURCE = {"workflow": "maintenance", "workflow_id": "wf-run", "run_id": "uuid", "activity_id": "3"}


@pytest.mark.parametrize("trigger", ["schedule", "manual", "agent", "webhook"])
async def test_recorded_trigger_is_used_not_a_caller_supplied_claim(db, trigger):
    db.record_run("maintenance", "wf-run", "uuid", trigger, {})
    result = await provenance.workflow_call({**SOURCE, "trigger": "fake-human"})
    assert result == {"kind": "workflow", "relation": "call", **SOURCE, "trigger": trigger}


async def test_scheduled_discovery_delay_is_verified_against_temporal(db, monkeypatch):
    describe = AsyncMock(
        return_value={"workflow_type": "maintenance", "run_id": "uuid", "trigger": "schedule"}
    )
    monkeypatch.setattr(provenance.temporal, "describe", describe)
    assert (await provenance.workflow_call(SOURCE))["trigger"] == "schedule"
    describe.assert_awaited_once_with("wf-run")
    describe.return_value["run_id"] = "different"
    with pytest.raises(HTTPException, match="differs from Temporal"):
        await provenance.workflow_call(SOURCE)


async def test_unknown_trigger_is_not_mislabeled_as_scheduled(db, monkeypatch):
    monkeypatch.setattr(
        provenance.temporal,
        "describe",
        AsyncMock(return_value={"workflow_type": "maintenance", "run_id": "uuid"}),
    )
    assert (await provenance.workflow_call(SOURCE))["trigger"] == "unknown"


def test_nested_delegation_retains_one_bounded_workflow_root():
    root = provenance.workflow_origin("maintenance", "wf-run", relation="call", trigger="schedule")
    sender = {"id": "lead", "name": "Lead", "chat_id": "chat", "call_id": "call"}
    child = provenance.agent_call(sender, root)
    grandchild = provenance.agent_call({**sender, "id": "child"}, child)
    assert grandchild["root"] == root and "root" not in grandchild["root"]
    assert "not a new message from the human user" in provenance.context(grandchild)


def test_public_chat_payload_cannot_forge_origin(client, db):
    chat = client.post("/api/chats", json={"title": "Human chat"}).json()
    response = client.post(
        f"/api/chats/{chat['id']}/messages",
        headers={"accept": "application/json"},
        json={
            "text": "hi",
            "origin": {"kind": "workflow", "workflow": "forged"},
            "sender_agent": {"id": "fake", "name": "Fake"},
        },
    )
    assert response.status_code == 202
    message = response.json()["message"]
    assert "origin" not in message["meta"] and "sender_agent" not in message["meta"]


def test_live_snapshot_and_saved_answer_share_caller_after_restart(db):
    import json

    origin = provenance.workflow_origin("maintenance", "wf-run", relation="call", trigger="agent")
    chat = db.create_chat(title="Task", agent_set="default")
    message, _ = db.accept_chat_message(chat["id"], "work", "turn", origin=origin)
    db.execute(
        "UPDATE chat_turns SET job = ? WHERE id = ?",
        (json.dumps({"agent_id": "worker", "origin": origin}), "turn"),
    )
    assert message["meta"]["origin"] == db.chat_snapshot(chat["id"])["active_turn"]["origin"]
    db.finish_chat_turn("turn", "done", {})
    assert db.list_messages(chat["id"])[-1]["meta"]["origin"] == origin
