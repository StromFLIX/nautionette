"""Delegation uses real admission/SQL, never a caller-provided identity or ancestry."""

import asyncio
import json
from contextlib import contextmanager

import pytest
from fastapi import HTTPException
from nautionette_backend import chat_outputs, subagents
from nautionette_backend.routers import chats


@pytest.fixture
def scope(client, db, monkeypatch):
    monkeypatch.setattr(chats, "spawn", lambda coroutine, **kwargs: coroutine.close())
    child = client.post("/api/agents", json={"name": "Research"}).json()
    root = client.post("/api/agents", json={"name": "Lead", "allowed_subagents": [child["id"]]}).json()
    chat = client.post("/api/chats", json={"agent_id": root["id"], "title": "Delegator"}).json()
    db.accept_chat_message(chat["id"], "Work", "root")
    db.execute(
        "UPDATE chat_turns SET job=? WHERE id='root'",
        (
            json.dumps(
                {
                    "agent_id": root["id"],
                    "agent_name": root["name"],
                    "tools": None,
                }
            ),
        ),
    )
    with chat_outputs.publication_grant(chat["id"], "root") as grant:
        yield {"grant": grant["grant"], "chat": chat, "root": root, "child": child}


def start(client, scope, request="one", **extra):
    return client.post(
        "/api/subagents/call",
        json={
            "grant": scope["grant"],
            "request_id": request,
            "agent_id": scope["child"]["id"],
            "text": "Find facts",
            **extra,
        },
    )


def finish(db, result, text="Facts", error=None):
    db.finish_chat_turn(result["turn_id"], text, {"error": error})


@contextmanager
def child_scope(db, result):
    with chat_outputs.publication_grant(result["chat_id"], result["turn_id"]) as grant:
        yield grant["grant"]


def test_allowlist_defaults_validation_and_deletion_cleanup(client, scope):
    assert scope["child"]["allowed_subagents"] == []
    for value in (None, "all", [123], ["missing"]):
        assert client.patch(
            f"/api/agents/{scope['root']['id']}", json={"allowed_subagents": value}
        ).status_code in (404, 422)
    assert client.patch(f"/api/agents/{scope['root']['id']}", json={"description": "Changed"}).json()[
        "allowed_subagents"
    ] == [scope["child"]["id"]]
    client.delete(f"/api/agents/{scope['child']['id']}")
    assert client.get(f"/api/agents/{scope['root']['id']}").json()["allowed_subagents"] == []


def test_roundtrip_idempotence_sender_wait_and_continuation(client, db, scope):
    listed = client.post("/api/subagents/list", json={"grant": scope["grant"]}).json()
    assert [agent["id"] for agent in listed["agents"]] == [scope["child"]["id"]]
    first = start(client, scope)
    assert first.status_code == 200, first.text
    result = first.json()
    assert result["state"] == "running" and result["depth"] == 1
    assert start(client, scope).json()["id"] == result["id"]
    assert start(client, scope, text="Different").status_code == 409
    assert db.one("SELECT COUNT(*) AS n FROM agent_calls")["n"] == 1
    job = json.loads(db.one("SELECT job FROM chat_turns WHERE id=?", (result["turn_id"],))["job"])
    assert job["delegation"] == result["id"] and job["agent_id"] == scope["child"]["id"]
    messages = db.list_messages(result["chat_id"])
    assert messages[0]["meta"]["sender_agent"]["name"] == "Lead"
    assert messages[0]["meta"]["sender_agent"]["chat_id"] == scope["chat"]["id"]
    assert scope["grant"] not in json.dumps(job)
    poll = {"grant": scope["grant"], "call_id": result["id"], "timeout_seconds": 0}
    assert client.post("/api/subagents/wait", json=poll).json()["state"] == "running"
    assert start(client, scope, "busy", chat_id=result["chat_id"]).status_code == 409
    finish(db, result)
    answer = client.post("/api/subagents/wait", json=poll).json()
    assert answer["ok"] and answer["text"] == "Facts"
    assert db.list_messages(result["chat_id"])[-1]["meta"]["sender_agent"]["name"] == "Research"
    continued = start(client, scope, "continue", chat_id=result["chat_id"])
    assert continued.status_code == 200, continued.text
    assert continued.json()["chat_id"] == result["chat_id"]
    assert continued.json()["id"] != result["id"]


def test_scope_cannot_be_forged_and_group_can_be_disabled(client, db, scope):
    assert client.post("/api/subagents/list", json={"grant": "x" * 40}).status_code == 401
    assert start(client, scope, agent_id=scope["root"]["id"]).status_code == 403
    assert start(client, scope, chat_id=scope["chat"]["id"]).status_code == 403
    assert start(client, scope, root_turn_id="forged").status_code == 422
    db.execute(
        "UPDATE chat_turns SET job=? WHERE id='root'",
        (
            json.dumps(
                {
                    "agent_id": scope["root"]["id"],
                    "tools": [],
                }
            ),
        ),
    )
    assert start(client, scope).status_code == 403
    assert client.post("/api/subagents/list", json={"grant": scope["grant"]}).status_code == 403


def test_width_counts_finished_calls_and_parallel_spans_whole_tree(client, db, scope):
    client.put("/api/settings", json={"subagent_max_parallel": 1, "subagent_max_width": 2})
    first = start(client, scope).json()
    assert "parallel" in start(client, scope, "two").json()["detail"]
    finish(db, first)
    second = start(client, scope, "two").json()
    finish(db, second)
    assert "width" in start(client, scope, "three").json()["detail"]
    assert start(client, scope).json()["id"] == first["id"]  # retries don't consume width


def test_nested_depth_parallel_and_default_empty_allowlist(client, db, scope):
    client.put("/api/settings", json={"subagent_max_depth": 2, "subagent_max_parallel": 2})
    first = start(client, scope).json()
    with child_scope(db, first) as grant:
        payload = {"grant": grant, "request_id": "nested", "agent_id": scope["child"]["id"], "text": "Again"}
        assert client.post("/api/subagents/call", json=payload).status_code == 403
        client.patch(
            f"/api/agents/{scope['child']['id']}", json={"allowed_subagents": [scope["child"]["id"]]}
        )
        second = client.post("/api/subagents/call", json=payload)
        assert second.status_code == 200, second.text
        assert second.json()["depth"] == 2
        assert "parallel" in start(client, scope, "sibling").json()["detail"]
        with child_scope(db, second.json()) as grandchild:
            payload.update(grant=grandchild, request_id="too-deep")
            assert "depth" in client.post("/api/subagents/call", json=payload).json()["detail"]


def test_stop_and_failures_propagate_but_normal_parent_completion_does_not(client, db, scope):
    first = start(client, scope).json()
    db.finish_chat_turn("root", "Launched asynchronously", {})
    assert not subagents.ancestor_stopped(first["turn_id"])
    db.execute("UPDATE chat_turns SET stop_requested=1 WHERE id='root'")
    assert subagents.ancestor_stopped(first["turn_id"])
    with child_scope(db, first) as grant:
        assert client.post("/api/subagents/list", json={"grant": grant}).status_code == 409


@pytest.mark.parametrize(
    "key,ceiling", [("subagent_max_depth", 10), ("subagent_max_width", 100), ("subagent_max_parallel", 100)]
)
def test_limits_are_strict_global_settings(client, key, ceiling):
    for value in (-1, ceiling + 1, True, 1.5, "2"):
        response = client.put("/api/settings", json={key: value, "default_agent_set": "changed"})
        assert response.status_code == 422
        assert client.get("/api/settings").json()["settings"]["default_agent_set"] == "default"
    assert client.put("/api/settings", json={key: 0}).json()["settings"][key] == 0


@pytest.mark.asyncio
async def test_concurrent_admission_is_serialized_and_wait_really_waits(client, db, scope):
    db.set_setting("subagent_max_parallel", 1)
    results = await asyncio.gather(
        subagents.start(scope["grant"], "a", scope["child"]["id"], "First", None),
        subagents.start(scope["grant"], "b", scope["child"]["id"], "Second", None),
        return_exceptions=True,
    )
    accepted = next(result for result in results if isinstance(result, dict))
    rejected = next(result for result in results if isinstance(result, HTTPException))
    assert rejected.status_code == 409
    waiting = asyncio.create_task(subagents.wait(scope["grant"], accepted["id"], 2))
    await asyncio.sleep(0.01)
    assert not waiting.done()
    finish(db, accepted, error="Child failed")
    result = await waiting
    assert not result["ok"] and result["error"] == "Child failed"


def test_lowering_limits_and_revoking_access_take_effect(client, db, scope):
    first = start(client, scope).json()
    finish(db, first)
    client.put("/api/settings", json={"subagent_max_parallel": 0})
    assert start(client, scope, "disabled").status_code == 409
    client.patch(f"/api/agents/{scope['root']['id']}", json={"allowed_subagents": []})
    assert start(client, scope, "revoked").status_code == 403


def test_access_is_rechecked_after_discovery_before_accepting(client, db, scope, monkeypatch):
    original = chats.catalog.model_info

    async def revoke_during_discovery(model):
        info = await original(model)
        db.execute("UPDATE agent_profiles SET allowed_subagents='[]' WHERE id=?", (scope["root"]["id"],))
        return info

    monkeypatch.setattr(chats.catalog, "model_info", revoke_during_discovery)
    response = start(client, scope)
    assert response.status_code == 403
    assert db.one("SELECT COUNT(*) AS n FROM chat_turns")["n"] == 1
    call = db.one("SELECT * FROM agent_calls")
    assert call["error"] == "Subagent access was revoked"
    assert "Find facts" not in call["request_hash"]


@pytest.mark.asyncio
async def test_child_executes_normal_pipeline_and_keeps_snapshot_identity(client, db, scope, backend):
    from nautionette_backend import conversations

    first = start(client, scope).json()
    client.patch(f"/api/agents/{scope['child']['id']}", json={"name": "Renamed"})
    job = json.loads(db.one("SELECT job FROM chat_turns WHERE id=?", (first["turn_id"],))["job"])
    await conversations.run_turn(first["turn_id"], first["chat_id"], job)
    result = await subagents.wait(scope["grant"], first["id"], 0)
    assert result["state"] == "completed" and result["ok"] and result["text"] == "hello"
    answer = db.list_messages(first["chat_id"])[-1]
    assert answer["meta"]["sender_agent"]["name"] == "Research"
    assert (first["chat_id"], first["turn_id"]) in backend.broker.cleanups
    assert "Message from agent Lead" in backend.broker.jobs[-1]["prompt"]


def test_uncertain_admission_is_not_replayed(client, db, scope):
    first = start(client, scope).json()
    db.execute("DELETE FROM chat_turns WHERE id=?", (first["turn_id"],))
    replay = start(client, scope).json()
    assert replay["state"] == "unavailable" and replay["id"] == first["id"]
    assert db.one("SELECT COUNT(*) AS n FROM agent_calls")["n"] == 1
