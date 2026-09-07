import asyncio

import httpx
import pytest
from nautionette_backend import background, conversations, main
from nautionette_backend.db import Database

from ..conftest import APP_TOKEN


def chat_client():
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=main.app),
        base_url="http://test",
        headers={"Authorization": f"Bearer {APP_TOKEN}", "Accept": "application/json"},
    )


async def finish_background():
    while background._running:
        await asyncio.wait_for(asyncio.gather(*background._running), 3)


def test_queue_is_durable_ordered_and_does_not_start_overlapping_turns(tmp_path):
    path = str(tmp_path / "chat.db")
    database = Database(path)
    chat_id = database.create_chat("Queue", "default")["id"]
    database.accept_chat_message(chat_id, "First", "first")
    message, created = database.accept_chat_message(chat_id, "Second", "second", queue=True)
    assert created and message["meta"]["queued"] is True
    assert database.accept_chat_message(chat_id, "Second", "second", queue=True)[1] is False
    database.execute("UPDATE chat_turns SET job = '{}' WHERE id = 'second'")
    assert database.next_chat_turn(chat_id) is None
    database.finish_chat_turn("first", "Done", {})
    recovered = Database(path)
    assert recovered.next_chat_turn(chat_id)["id"] == "second"
    assert recovered.next_chat_turn(chat_id) is None
    messages = recovered.list_messages(chat_id)
    assert [message["content"] for message in messages] == ["First", "Done", "Second"]
    assert not messages[-1]["meta"].get("queued")


def test_steering_cannot_cross_chats_or_run_twice(tmp_path):
    database = Database(str(tmp_path / "chat.db"))
    first = database.create_chat("First", "default")["id"]
    second = database.create_chat("Second", "default")["id"]
    database.accept_chat_message(first, "Run", "active")
    database.accept_chat_message(first, "Steer", "input", queue=True)
    database.accept_chat_message(second, "Other", "other")
    assert not database.consume_chat_input("other", "input")
    assert database.consume_chat_input("active", "input")
    assert not database.consume_chat_input("active", "input")
    database.finish_chat_turn("active", "Done", {})
    assert database.next_chat_turn(first) is None


@pytest.mark.parametrize("text", ["Prior answer", ""])
def test_consumed_segments_survive_reload_and_restart_with_tools(tmp_path, monkeypatch, text):
    path = str(tmp_path / "chat.db")
    database = Database(path)
    chat_id = database.create_chat("Queue", "default")["id"]
    database.accept_chat_message(chat_id, "First", "first")
    database.accept_chat_message(chat_id, "Second", "second", queue=True)
    database.accept_chat_message(chat_id, "Third", "third", queue=True)
    steps = [
        *([{"kind": "text", "text": text}] if text else []),
        {"kind": "tool", "id": "tool", "name": "bash", "args": {}, "ok": True, "result": "Retained"},
    ]
    context = {"tokens": 100}
    database.record_chat_progress("first", {"type": "usage", "context": context}, steps, "Working")
    meta = {"tools": ["bash"], "steps": steps}
    assert database.consume_chat_input("first", "second", text, meta)
    assert not database.consume_chat_input("first", "second", text, meta)
    assert not database.consume_chat_input("missing", "third", text, meta)
    recovered = Database(path)
    snapshot = recovered.chat_snapshot(chat_id)
    assert snapshot["active_turn"]["steps"] == []
    assert snapshot["active_turn"]["status"] == ""
    visible = [m for m in snapshot["messages"] if not m["meta"].get("queued")]
    assert [m["content"] for m in visible] == ["First", text, "Second"]
    assert visible[1]["meta"] == {**meta, "context": context}
    remaining = [{"kind": "text", "text": "New partial answer"}]
    recovered.record_chat_progress("first", {"type": "delta", "text": "New partial answer"}, remaining, "")
    monkeypatch.setattr(conversations, "db", recovered)
    conversations.recover_interrupted()
    snapshot = recovered.chat_snapshot(chat_id)
    visible = [m for m in snapshot["messages"] if not m["meta"].get("queued")]
    assert [m["content"] for m in visible] == ["First", text, "Second", "New partial answer"]
    assert visible[1]["meta"] == {**meta, "context": context}
    assert visible[-1]["meta"]["steps"] == remaining
    assert visible[-1]["meta"]["error"]
    assert snapshot["active_turn"] is None
    assert snapshot["chat"]["queue_paused"] == 1
    assert next(m for m in snapshot["messages"] if m["id"] == "third")["meta"]["queued"]


async def test_result_does_not_replay_text_saved_before_steering(backend, monkeypatch):
    chat_id = backend.db.create_chat("Queue", "default")["id"]
    backend.db.accept_chat_message(chat_id, "Run", "first")
    backend.db.accept_chat_message(chat_id, "Next", "second", queue=True)

    async def agent(job):
        yield {"type": "delta", "text": "Already saved"}
        yield {"type": "input_consumed", "id": "second"}
        yield {"type": "interrupted"}
        yield {"type": "result", "ok": True, "text": "Already saved"}

    monkeypatch.setattr(conversations, "stream_agent", agent)
    await conversations.run_turn("first", chat_id, {"prompt": "Run"})
    messages = backend.db.list_messages(chat_id)
    assert [m["content"] for m in messages[:3]] == ["Run", "Already saved", "Next"]
    assert "Already saved" not in messages[-1]["content"]
    assert messages[-1]["meta"]["interrupted"] is True


def test_stop_targets_exact_turn_and_pauses_queue(tmp_path):
    database = Database(str(tmp_path / "chat.db"))
    chat_id = database.create_chat("Queue", "default")["id"]
    database.accept_chat_message(chat_id, "Run", "active")
    database.accept_chat_message(chat_id, "Later", "queued", queue=True)
    database.execute("UPDATE chat_turns SET job = '{}' WHERE id = 'queued'")
    assert not database.stop_chat_turn(chat_id, "old-turn")
    assert database.stop_chat_turn(chat_id, "active")
    database.finish_chat_turn("active", "Stopped", {})
    assert database.next_chat_turn(chat_id) is None
    assert database.chat_snapshot(chat_id)["chat"]["queue_paused"] == 1
    database.execute("UPDATE chats SET queue_paused = 0 WHERE id = ?", (chat_id,))
    assert database.next_chat_turn(chat_id)["id"] == "queued"


async def test_messages_steer_the_running_turn_once_in_order(backend, monkeypatch):
    started = asyncio.Event()
    inbox = asyncio.Queue()
    jobs = []
    commands = []

    async def agent(job):
        jobs.append(job.copy())
        if len(jobs) > 1:
            yield {"type": "delta", "text": "Follow-up answer"}
            yield {"type": "result", "ok": True}
            return
        started.set()
        yield {"type": "delta", "text": "Before the queue"}
        yield {"type": "tool", "id": "command", "name": "bash", "args": {"command": "pwd"}}
        yield {"type": "tool_done", "id": "command", "result": "/workspace", "error": False}
        for index in range(2):
            message = await inbox.get()
            yield {"type": "input_consumed", "id": message["id"]}
            snapshot = backend.db.chat_snapshot(job["chat_id"])
            visible = [m for m in snapshot["messages"] if not m["meta"].get("queued")]
            assert [m["content"] for m in visible] == [
                "Earlier question",
                "Earlier answer",
                "Run",
                "Before the queue",
                "second",
                *(["Between queued messages", "third"] if index else []),
            ]
            assert snapshot["active_turn"]["steps"] == []
            yield {
                "type": "delta",
                "text": "Used both queued instructions" if index else "Between queued messages",
            }
            # A duplicate acknowledgement must neither split nor clear this answer.
            yield {"type": "input_consumed", "id": message["id"]}
        yield {"type": "result", "ok": True}

    async def control(chat_id, turn_id, command):
        assert chat_id == jobs[0]["chat_id"] and turn_id == "first"
        commands.append(command)
        await inbox.put(command)
        return True

    monkeypatch.setattr(conversations, "stream_agent", agent)
    monkeypatch.setattr(conversations.broker, "control_agent", control)
    try:
        async with chat_client() as client:
            chat_id = (await client.post("/api/chats", json={})).json()["id"]
            backend.db.add_message(chat_id, "user", "Earlier question")
            backend.db.add_message(chat_id, "assistant", "Earlier answer")
            await client.post(f"/api/chats/{chat_id}/messages", json={"text": "Run", "message_id": "first"})
            await asyncio.wait_for(started.wait(), 2)
            for message_id in ("second", "third"):
                payload = {"text": message_id, "message_id": message_id, "queue": True}
                queued = await client.post(f"/api/chats/{chat_id}/messages", json=payload)
                assert queued.status_code == 202
                assert queued.json()["message"]["meta"]["queued"] is True
                assert (await client.post(f"/api/chats/{chat_id}/messages", json=payload)).status_code == 202
            await finish_background()
            assert len(jobs) == 1
            assert [command["id"] for command in commands] == ["second", "third"]
            snapshot = (await client.get(f"/api/chats/{chat_id}")).json()
            assert snapshot["active_turn"] is None
            assert not any(message["meta"].get("queued") for message in snapshot["messages"])
            assert [m["content"] for m in snapshot["messages"]] == [
                "Earlier question",
                "Earlier answer",
                "Run",
                "Before the queue",
                "second",
                "Between queued messages",
                "third",
                "Used both queued instructions",
            ]
            assert [m["role"] for m in snapshot["messages"]] == ["user", "assistant"] * 4
            before = snapshot["messages"][3]
            assert before["meta"]["tools"] == ["bash"]
            assert before["meta"]["steps"][1]["result"] == "/workspace"
            assert before["meta"]["steps"][1]["ok"] is True
            assert snapshot["messages"][-1]["meta"]["tools"] == []
            await client.post(
                f"/api/chats/{chat_id}/messages", json={"text": "Follow up", "message_id": "follow-up"}
            )
            await finish_background()
            assert jobs[1]["history"] == [
                {"role": m["role"], "content": m["content"]} for m in snapshot["messages"]
            ]
    finally:
        await background.drain()


async def test_unconsumed_queue_runs_next_with_fresh_history_and_settings(backend, monkeypatch):
    started = asyncio.Event()
    finish = asyncio.Event()
    jobs = []

    async def agent(job):
        jobs.append(job.copy())
        started.set()
        if len(jobs) == 1:
            await finish.wait()
        yield {"type": "delta", "text": f"Answer to {job['prompt']}"}
        yield {"type": "result", "ok": True}

    async def control(*args):
        raise AssertionError("Changed model must not steer the old container")

    monkeypatch.setattr(conversations, "stream_agent", agent)
    monkeypatch.setattr(conversations.broker, "control_agent", control)
    try:
        async with chat_client() as client:
            chat_id = (await client.post("/api/chats", json={})).json()["id"]
            await client.post(f"/api/chats/{chat_id}/messages", json={"text": "First", "message_id": "first"})
            await asyncio.wait_for(started.wait(), 2)
            await client.patch(f"/api/chats/{chat_id}", json={"model": "other/model"})
            await client.post(
                f"/api/chats/{chat_id}/messages",
                json={"text": "Second", "message_id": "second", "queue": True},
            )
            finish.set()
            await finish_background()
            assert len(jobs) == 2
            assert jobs[1]["model"] == "other/model"
            assert [message["content"] for message in jobs[1]["history"]] == ["First", "Answer to First"]
            assert jobs[1]["turn_id"] == "second"
            snapshot = (await client.get(f"/api/chats/{chat_id}")).json()
            assert [message["content"] for message in snapshot["messages"]] == [
                "First",
                "Answer to First",
                "Second",
                "Answer to Second",
            ]
    finally:
        finish.set()
        await background.drain()


async def test_stop_preserves_partial_output_pauses_queue_and_rejects_stale_turn(backend, monkeypatch):
    started = asyncio.Event()
    stopped = asyncio.Event()
    jobs = []

    async def agent(job):
        jobs.append(job["turn_id"])
        yield {"type": "delta", "text": "Partial output"}
        started.set()
        await stopped.wait()

    async def control(chat_id, turn_id, command):
        if command["type"] == "stop":
            assert turn_id == "active"
            stopped.set()
        return True

    monkeypatch.setattr(conversations, "stream_agent", agent)
    monkeypatch.setattr(conversations.broker, "control_agent", control)
    try:
        async with chat_client() as client:
            chat_id = (await client.post("/api/chats", json={})).json()["id"]
            await client.post(f"/api/chats/{chat_id}/messages", json={"text": "Run", "message_id": "active"})
            await asyncio.wait_for(started.wait(), 2)
            await client.post(
                f"/api/chats/{chat_id}/messages", json={"text": "Later", "message_id": "later", "queue": True}
            )
            stale = await client.post(f"/api/chats/{chat_id}/stop", json={"turn_id": "old"})
            assert stale.status_code == 409
            assert (
                await client.post(f"/api/chats/{chat_id}/stop", json={"turn_id": "active"})
            ).status_code == 200
            await finish_background()
            snapshot = (await client.get(f"/api/chats/{chat_id}")).json()
            assert snapshot["active_turn"] is None
            assert snapshot["chat"]["queue_paused"] == 1
            assert snapshot["messages"][-1]["content"] == "Partial output"
            assert snapshot["messages"][-1]["meta"]["interrupted"] is True
            assert jobs == ["active"]
            assert (await client.post(f"/api/chats/{chat_id}/queue/resume")).status_code == 200
            await finish_background()
            assert jobs == ["active", "later"]
    finally:
        stopped.set()
        await background.drain()


def test_restart_preserves_queue_but_requires_explicit_resume(backend):
    database = backend.db
    chat_id = database.create_chat("Restart", "default")["id"]
    database.accept_chat_message(chat_id, "Running", "active")
    database.accept_chat_message(chat_id, "Pending", "pending", queue=True)
    database.execute("UPDATE chat_turns SET job = '{}' WHERE id = 'pending'")
    conversations.recover_interrupted()
    snapshot = database.chat_snapshot(chat_id)
    assert snapshot["active_turn"] is None
    assert snapshot["chat"]["queue_paused"] == 1
    assert database.next_chat_turn(chat_id) is None
    assert next(message for message in snapshot["messages"] if message["id"] == "pending")["meta"]["queued"]


def test_queued_message_can_only_be_removed_when_its_chat_is_idle(client, db):
    chat_id = db.create_chat("Queue", "default")["id"]
    other_chat = db.create_chat("Other", "default")["id"]
    db.accept_chat_message(chat_id, "Running", "active")
    db.accept_chat_message(chat_id, "Pending", "pending", queue=True)
    assert client.delete(f"/api/chats/{chat_id}/queue/pending").status_code == 409
    db.stop_chat_turn(chat_id, "active")
    db.finish_chat_turn("active", "Stopped", {})
    assert client.delete(f"/api/chats/{other_chat}/queue/pending").status_code == 409
    assert client.delete(f"/api/chats/{chat_id}/queue/pending").status_code == 200
    assert not any(message["id"] == "pending" for message in db.list_messages(chat_id))
