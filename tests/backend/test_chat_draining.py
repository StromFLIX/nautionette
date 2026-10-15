import asyncio
import json
import signal
from unittest.mock import AsyncMock

import pytest
import uvicorn
from nautionette_backend import background, conversations
from nautionette_backend.server import DrainingServer

from .test_chat_queue import chat_client, finish_background


async def test_drain_finishes_active_turn_and_resumes_untouched_queue_after_restart(backend, monkeypatch):
    started, release = asyncio.Event(), asyncio.Event()
    jobs = []

    async def agent(job):
        jobs.append(job["turn_id"])
        yield {"type": "delta", "text": f"Answer {job['turn_id']}"}
        if job["turn_id"] == "first":
            started.set()
            await release.wait()
        yield {"type": "result", "ok": True}

    monkeypatch.setattr(conversations, "stream_agent", agent)
    async with chat_client() as client:
        chat = (await client.post("/api/chats", json={})).json()["id"]
        await client.post(f"/api/chats/{chat}/messages", json={"text": "Run", "message_id": "first"})
        await asyncio.wait_for(started.wait(), 2)
        # Different job configuration keeps this input out of live steering.
        await client.patch(f"/api/chats/{chat}", json={"timeout_exempt": True})
        await client.post(
            f"/api/chats/{chat}/messages", json={"text": "Next", "message_id": "next", "queue": True}
        )
        background.begin_draining()
        waiting = asyncio.create_task(background.wait_for_chat_turns())
        try:
            await asyncio.sleep(0)
            assert not waiting.done()
            rejected = await client.post(
                f"/api/chats/{chat}/messages", json={"text": "Too late", "message_id": "late"}
            )
            assert rejected.status_code == 503
            assert rejected.headers["Retry-After"] == "5"
            assert (await client.post(f"/api/chats/{chat}/queue/resume")).status_code == 503
            assert not backend.db.one("SELECT id FROM chat_turns WHERE id = 'late'")
        finally:
            release.set()
            await asyncio.wait_for(waiting, 2)
            await background.drain()
        snapshot = backend.db.chat_snapshot(chat)
        assert snapshot["active_turn"] is None
        assert snapshot["chat"]["queue_paused"] == 0
        assert jobs == ["first"]
        # New process startup: only never-started inputs are resumed.
        monkeypatch.setattr(background, "_draining", False)
        conversations.recover_interrupted()
        conversations.resume_chat_queues()
        conversations.resume_chat_queues()  # Idempotent, even before the new task runs.
        await finish_background()
        assert jobs == ["first", "next"]
        assert [m["content"] for m in backend.db.list_messages(chat)] == [
            "Run",
            "Answer first",
            "Next",
            "Answer next",
        ]
        assert backend.db.chat_snapshot(chat)["chat"]["queue_paused"] == 0


async def test_signal_during_model_discovery_does_not_accept_untracked_turn(backend, monkeypatch):
    from nautionette_backend import catalog

    async def discover(model):
        background.begin_draining()
        return {"id": model}

    monkeypatch.setattr(catalog, "model_info", discover)
    async with chat_client() as client:
        chat = (await client.post("/api/chats", json={})).json()["id"]
        response = await client.post(f"/api/chats/{chat}/messages", json={"text": "No"})
        assert response.status_code == 503
        assert backend.db.list_messages(chat) == []
        assert not background._chat_turns


async def test_restart_does_not_resume_uncertain_or_manually_paused_queues(backend, monkeypatch):
    chats = []
    for prefix in ("uncertain", "paused"):
        chat = backend.db.create_chat(prefix, "default")["id"]
        chats.append(chat)
        backend.db.accept_chat_message(chat, "Run", prefix)
        backend.db.accept_chat_message(chat, "Next", prefix + "-next", queue=True)
        backend.db.execute("UPDATE chat_turns SET job = '{}' WHERE id = ?", (prefix + "-next",))
    backend.db.finish_chat_turn("paused", "Stopped", {})
    backend.db.execute("UPDATE chats SET queue_paused = 1 WHERE id = ?", (chats[1],))
    stream = AsyncMock()
    monkeypatch.setattr(conversations, "stream_agent", stream)
    conversations.recover_interrupted()
    conversations.recover_interrupted()
    conversations.resume_chat_queues()
    stream.assert_not_called()
    assert not background._chat_turns
    for chat in chats:
        assert backend.db.chat_snapshot(chat)["chat"]["queue_paused"] == 1
    answer = backend.db.list_messages(chats[0])[-1]
    assert "Verify unfinished tools" in answer["meta"]["error"]
    assert "not automatically replayed" in answer["meta"]["error"]


@pytest.mark.parametrize("expedite", [False, True])
async def test_server_deadline_or_second_signal_cleans_up_before_closing_http(backend, monkeypatch, expedite):
    chat = backend.db.create_chat("Drain", "default")["id"]
    backend.db.accept_chat_message(chat, "Run", "active")
    backend.db.accept_chat_message(chat, "Next", "next", queue=True)
    backend.db.execute("UPDATE chat_turns SET job = '{}' WHERE id = 'next'")
    started = asyncio.Event()

    async def agent(job):
        yield {"type": "delta", "text": "Partial"}
        started.set()
        await asyncio.Event().wait()

    async def close_http(server, sockets=None):
        snapshot = backend.db.chat_snapshot(chat)
        assert snapshot["active_turn"] is None
        assert snapshot["chat"]["queue_paused"] == 1
        assert backend.broker.cleanups == [(chat, "active")]
        assert "shutdown" in snapshot["messages"][-1]["meta"]["error"]

    monkeypatch.setattr(conversations, "stream_agent", agent)
    monkeypatch.setattr(uvicorn.Server, "shutdown", close_http)
    monkeypatch.setattr(
        "nautionette_backend.server.settings.backend_chat_drain_seconds", 100 if expedite else 0
    )
    background.spawn(conversations.run_turn("active", chat, {}), name="chat-active", chat_turn=True)
    await asyncio.wait_for(started.wait(), 2)
    server = DrainingServer(uvicorn.Config("nautionette_backend.main:app"))
    server.handle_exit(signal.SIGTERM, None)
    assert background.is_draining()
    assert server.should_exit
    if expedite:
        server.handle_exit(signal.SIGINT, None)
        assert not server.force_exit  # Do not skip the lifespan cleanup.
    await asyncio.wait_for(server.shutdown(), 2)
    assert not backend.db.one("SELECT id FROM chat_turns WHERE id = 'next' AND state = 'running'")


async def test_steering_delivery_rechecks_drain_after_inflight_command(backend, monkeypatch):
    chat = backend.db.create_chat("Drain controls", "default")["id"]
    backend.db.accept_chat_message(chat, "Run", "active")
    for turn in ("next", "last"):
        backend.db.accept_chat_message(chat, "Next", turn, queue=True)
        backend.db.execute("UPDATE chat_turns SET job = ? WHERE id = ?", (json.dumps({"prompt": turn}), turn))
    commands = []
    finished = asyncio.Event()

    async def control(chat_id, turn_id, command):
        commands.append(command)
        # Simulate SIGTERM while the first delivery was awaiting the broker.
        background.begin_draining()
        finished.set()
        return True

    monkeypatch.setattr(conversations.broker, "control_agent", control)
    await asyncio.wait_for(conversations.control_turn("active", chat, {}, finished), 2)
    assert [c["id"] for c in commands] == ["next"]

    # Stop remains usable during draining; it takes precedence over steering.
    finished.clear()
    assert backend.db.stop_chat_turn(chat, "active")
    await asyncio.wait_for(conversations.control_turn("active", chat, {}, finished), 2)
    assert [c["type"] for c in commands] == ["steer", "stop"]


async def test_cancelling_drain_waiter_does_not_cancel_the_turn(backend):
    release = asyncio.Event()
    turn = background.spawn(release.wait(), name="chat-test", chat_turn=True)
    waiting = asyncio.create_task(background.wait_for_chat_turns())
    await asyncio.sleep(0)
    waiting.cancel()
    await asyncio.gather(waiting, return_exceptions=True)
    assert not turn.done()
    release.set()
    await turn
