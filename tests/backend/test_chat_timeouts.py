"""Per-chat exemptions persist without altering accepted jobs or other chats."""

import asyncio
import json

import httpx
import pytest
from nautionette_backend import agent, conversations
from nautionette_backend.clients.docker_broker import BrokerClient
from nautionette_backend.db import Database


@pytest.mark.parametrize("exempt", [False, True])
def test_create_read_list_and_reopen_preserve_exemption(client, db, exempt):
    chat = client.post("/api/chats", json={"timeout_exempt": exempt}).json()
    assert chat["timeout_exempt"] is exempt
    assert client.get(f"/api/chats/{chat['id']}").json()["chat"]["timeout_exempt"] is exempt
    assert client.get("/api/chats").json()["chats"][0]["timeout_exempt"] is exempt
    path = db.one("PRAGMA database_list")["file"]
    reopened = Database(path)
    assert reopened.get_chat(chat["id"])["timeout_exempt"] is exempt
    reopened._conn.close()


def test_legacy_chat_migration_is_safe_and_idempotent(tmp_path):
    path = str(tmp_path / "legacy.db")
    old = Database(path)
    chat = old.create_chat("Legacy", "default")
    old.add_message(chat["id"], "user", "Retain this")
    old.execute("ALTER TABLE chats DROP COLUMN timeout_exempt")
    old._conn.close()
    migrated = Database(path)
    assert migrated.get_chat(chat["id"])["timeout_exempt"] is False
    migrated.update_chat(chat["id"], {"timeout_exempt": True})
    migrated._conn.close()
    reopened = Database(path)
    assert reopened.get_chat(chat["id"])["timeout_exempt"] is True
    assert reopened.list_messages(chat["id"])[0]["content"] == "Retain this"
    reopened._conn.close()


@pytest.mark.parametrize("invalid", [None, 0, 1, "true", "false", [], {}])
def test_exemptions_require_strict_booleans(client, invalid):
    assert client.post("/api/chats", json={"timeout_exempt": invalid}).status_code == 422
    chat = client.post("/api/chats", json={}).json()
    response = client.patch(f"/api/chats/{chat['id']}", json={"timeout_exempt": invalid, "title": "Bad"})
    assert response.status_code == 422
    saved = client.get(f"/api/chats/{chat['id']}").json()["chat"]
    assert saved["timeout_exempt"] is False
    assert saved["title"] == "New chat"


def test_exemption_is_reversible_per_chat_and_cannot_be_set_by_message(client, broker, db):
    first = client.post("/api/chats", json={"title": "Long task"}).json()["id"]
    assert client.patch(f"/api/chats/{first}", json={"timeout_exempt": True}).json()["timeout_exempt"] is True
    # Model/profile changes do not reset the session's exemption.
    assert client.patch(f"/api/chats/{first}", json={"agent_id": None}).json()["timeout_exempt"] is True
    other = client.post("/api/chats", json={}).json()
    assert other["timeout_exempt"] is False
    client.post(f"/api/chats/{first}/messages", json={"text": "Long task", "message_id": "long"})
    client.post(f"/api/chats/{other['id']}/messages", json={"text": "Regular", "timeout_exempt": True})
    updated = client.patch(f"/api/chats/{first}", json={"timeout_exempt": False}).json()
    assert updated["timeout_exempt"] is False
    client.post(f"/api/chats/{first}/messages", json={"text": "Regular again"})
    assert [job["timeout_exempt"] for job in broker.jobs] == [True, False, False]
    assert json.loads(db.one("SELECT job FROM chat_turns WHERE id = 'long'")["job"])["timeout_exempt"] is True


@pytest.mark.parametrize("exempt", [False, True])
async def test_stream_runner_passes_the_correct_timeout_to_broker(live, monkeypatch, exempt):
    calls = []

    async def run(job, timeout):
        calls.append((job, timeout))
        yield {"type": "result", "ok": True}

    monkeypatch.setattr(live.broker, "run_agent", run)
    job = agent.agent_job(prompt="Long task", timeout_seconds=3600)
    job.update(chat_id="chat", turn_id="turn", timeout_exempt=exempt)
    events = [event async for event in agent.stream_agent(job)]
    assert events == [{"type": "result", "ok": True}]
    assert calls == [(job, None if exempt else 3630)]
    # Workflow budgets are not affected even if the flag is accidentally copied.
    job["mode"] = "workflow"
    _ = [event async for event in agent.stream_agent(job)]
    assert calls[-1][1] == 3630


@pytest.mark.parametrize("timeout", [None, 3630])
async def test_only_stream_read_timeout_is_removed(http, timeout):
    def respond(request):
        limits = request.extensions["timeout"]
        assert limits["read"] == timeout
        assert limits["connect"] == 10
        assert limits["write"] == limits["pool"] == (900 if timeout is None else timeout)
        return httpx.Response(200, text='{"type":"result","ok":true}\n')

    http["http://broker.test/agent/run"] = respond
    events = [event async for event in BrokerClient("http://broker.test").run_agent({}, timeout=timeout)]
    assert events == [{"type": "result", "ok": True}]


@pytest.mark.parametrize("first_exempt", [False, True])
async def test_queue_with_different_exemption_cannot_steer_running_container(
    backend, monkeypatch, first_exempt
):
    db = backend.db
    chat = db.create_chat("Queue", "default", timeout_exempt=first_exempt)
    db.accept_chat_message(chat["id"], "Running", "first")
    db.accept_chat_message(chat["id"], "Queued", "second", queue=True)
    current = {"timeout_exempt": first_exempt}
    candidate = {"timeout_exempt": not first_exempt, "prompt": "Queued"}
    db.execute("UPDATE chat_turns SET job = ? WHERE id = 'second'", (json.dumps(candidate),))
    commands = []

    async def control(*args):
        commands.append(args)
        return True

    monkeypatch.setattr(conversations.broker, "control_agent", control)
    finished = asyncio.Event()
    task = asyncio.create_task(conversations.control_turn("first", chat["id"], current, finished))
    try:
        await asyncio.sleep(0.3)
        assert commands == []
    finally:
        finished.set()
        await task
    # Later chat changes cannot rewrite the queued turn's saved policy.
    db.update_chat(chat["id"], {"timeout_exempt": first_exempt})
    db.finish_chat_turn("first", "Done", {})
    next_turn = db.next_chat_turn(chat["id"])
    assert json.loads(next_turn["job"])["timeout_exempt"] is not first_exempt
