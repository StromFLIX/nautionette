import asyncio

import pytest
from nautionette_backend import conversations


def pending_chat(db):
    chat = db.create_chat("Research", "default")
    turn_id = "turn-" + chat["id"]
    db.accept_chat_message(chat["id"], "Research this", turn_id)
    db.execute(
        "UPDATE chats SET internet_status = 'pending', internet_turn_id = ? WHERE id = ?",
        (turn_id, chat["id"]),
    )
    return chat["id"], turn_id


@pytest.mark.parametrize("allowed", [False, True])
def test_decision_is_user_only_scoped_and_persistent(client, anonymous, db, live, monkeypatch, allowed):
    chat_id, turn_id = pending_chat(db)
    other = db.create_chat("Other", "default")
    calls = []

    async def decide(*args):
        calls.append(args)

    monkeypatch.setattr(live.broker, "decide_internet", decide)
    path = f"/api/chats/{chat_id}/internet"
    payload = {"allowed": allowed, "turn_id": turn_id}
    assert anonymous.post(path, json=payload).status_code == 401
    assert client.post(path, json={**payload, "turn_id": "wrong"}).status_code == 409
    assert client.post(path, json={**payload, "allowed": "true"}).status_code == 422
    response = client.post(path, json=payload)
    assert response.status_code == 200
    assert response.json()["internet_status"] == ("allowed" if allowed else "denied")
    assert client.post(path, json=payload).status_code == 200
    assert calls == [(chat_id, turn_id, allowed)]
    assert db.get_chat(other["id"])["internet_status"] == "blocked"
    db.finish_chat_turn(turn_id, "Done", {})
    conversations.recover_interrupted()
    assert db.get_chat(chat_id)["internet_status"] == ("allowed" if allowed else "denied")


def test_unrequested_grants_and_patch_bypass_are_refused(client):
    chat = client.post("/api/chats", json={"internet_status": "allowed"}).json()
    path = f"/api/chats/{chat['id']}"
    assert chat["internet_status"] == "blocked"
    assert client.patch(path, json={"internet_status": "allowed"}).json()["internet_status"] == "blocked"
    assert client.post(path + "/internet", json={"turn_id": "fake", "allowed": True}).status_code == 409


def test_failed_delivery_does_not_persist_a_grant(client, db, live, monkeypatch):
    chat_id, turn_id = pending_chat(db)

    async def fail(*args):
        raise RuntimeError("broker unavailable")

    monkeypatch.setattr(live.broker, "decide_internet", fail)
    response = client.post(f"/api/chats/{chat_id}/internet", json={"allowed": True, "turn_id": turn_id})
    assert response.status_code == 502
    assert db.get_chat(chat_id)["internet_status"] == "pending"


def test_future_turns_inherit_only_their_chats_grant(client, db, broker):
    approved = client.post("/api/chats", json={}).json()["id"]
    blocked = client.post("/api/chats", json={}).json()["id"]
    db.execute("UPDATE chats SET internet_status = 'allowed' WHERE id = ?", (approved,))
    for chat_id in (approved, approved, blocked):
        client.post(f"/api/chats/{chat_id}/messages", json={"text": "Hello", "internet_allowed": True})
    assert [job["internet_allowed"] for job in broker.jobs] == [True, True, False]
    assert [job["chat_id"] for job in broker.jobs] == [approved, approved, blocked]


@pytest.mark.parametrize("status", ["blocked", "pending", "denied", "allowed"])
def test_chat_prompt_gates_only_direct_egress_not_gateway_tools(client, db, broker, status):
    chat_id = client.post("/api/chats", json={}).json()["id"]
    db.execute("UPDATE chats SET internet_status = ? WHERE id = ?", (status, chat_id))
    response = client.post(f"/api/chats/{chat_id}/messages", json={"text": "Run the configured tool"})
    assert response.status_code == 200
    job = broker.jobs[-1]
    assert job["internet_allowed"] is (status == "allowed")
    prompt = job["system_prompt"]
    assert f"Direct internet access is {status} for this chat" in prompt
    assert "only direct connections from the agent container" in prompt
    assert "Git clone/fetch/pull/push, direct HTTP/API calls, or package downloads" in prompt
    assert "Before making such a connection, call request_internet_access" in prompt
    assert "tools exposed through agentgateway do not require chat internet approval" in prompt
    assert "regardless of tool name or service" in prompt
    assert "Use them normally even when direct internet access is blocked, pending, or denied" in prompt
    assert "If denied, continue with local work and configured gateway tools" in prompt
    assert "Do not tunnel arbitrary shell commands or direct network requests" in prompt
    assert "Before first accessing the internet" not in prompt
    assert "never bypass the decision via MCP tools or workflows" not in prompt


async def test_tool_request_is_visible_until_turn_ends(db, broker, monkeypatch):
    chat = db.create_chat("Research", "default")
    db.accept_chat_message(chat["id"], "Research this", "turn-a")
    requested = asyncio.Event()
    finish = asyncio.Event()

    async def stream(job):
        yield {"type": "tool", "name": "request_internet_access", "args": {"reason": "Read docs"}}
        requested.set()
        await finish.wait()
        yield {"type": "result", "ok": True, "text": "Done"}

    monkeypatch.setattr(conversations, "stream_agent", stream)
    task = asyncio.create_task(conversations.run_turn("turn-a", chat["id"], {}))
    try:
        await asyncio.wait_for(requested.wait(), 2)
        snapshot = db.chat_snapshot(chat["id"])
        assert snapshot["chat"]["internet_status"] == "pending"
        assert snapshot["chat"]["internet_reason"] == "Read docs"
    finally:
        finish.set()
        await task
    assert db.get_chat(chat["id"])["internet_status"] == "blocked"


def test_restart_clears_unanswered_requests(db):
    chat_id, turn_id = pending_chat(db)
    conversations.recover_interrupted()
    assert db.get_chat(chat_id)["internet_status"] == "blocked"


def test_turn_finishing_during_decision_does_not_lose_the_grant(client, db, live, monkeypatch):
    chat_id, turn_id = pending_chat(db)

    async def stream(job):
        yield {"type": "result", "ok": True, "text": "Done"}

    async def decide(*args):
        await conversations.run_turn(turn_id, chat_id, {})

    monkeypatch.setattr(conversations, "stream_agent", stream)
    monkeypatch.setattr(live.broker, "decide_internet", decide)
    response = client.post(f"/api/chats/{chat_id}/internet", json={"allowed": True, "turn_id": turn_id})
    assert response.status_code == 200
    assert db.get_chat(chat_id)["internet_status"] == "allowed"
    assert db.chat_snapshot(chat_id)["active_turn"] is None
