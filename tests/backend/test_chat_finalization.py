"""A dead owner must never leave a durable turn permanently running."""

import asyncio
import json
from unittest.mock import AsyncMock, Mock

import pytest
from nautionette_backend import background, conversations
from nautionette_backend.routers.chats import stop_chat


@pytest.mark.parametrize("stage", ["clock", "revocation", "save"])
async def test_failed_finalizer_preserves_checkpoint_and_settles_turn(backend, monkeypatch, stage):
    chat = backend.db.create_chat("Finalization", "default")["id"]
    backend.db.accept_chat_message(chat, "Run", "old")
    backend.db.accept_chat_message(chat, "Do not replay", "queued", queue=True)
    backend.db.execute("UPDATE chat_turns SET job = '{}' WHERE id = 'queued'")
    advance = Mock()
    monkeypatch.setattr(conversations, "launch_next", advance)

    async def agent(job):
        yield {"type": "delta", "text": "Retained partial work"}
        yield {"type": "result", "ok": True}

    monkeypatch.setattr(conversations, "stream_agent", agent)
    secret = "sensitive-upstream-response"
    if stage == "clock":
        monkeypatch.setattr(conversations.Timeline, "stop_tools", Mock(side_effect=RuntimeError(secret)))
    elif stage == "revocation":
        monkeypatch.setattr(
            conversations.TurnCredentials, "revoke", AsyncMock(side_effect=RuntimeError(secret))
        )
    else:
        save = backend.db.finish_chat_turn
        calls = 0

        def fail_once(*args, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 1:
                raise RuntimeError(secret)
            return save(*args, **kwargs)

        monkeypatch.setattr(backend.db, "finish_chat_turn", fail_once)
    with pytest.raises(RuntimeError, match=secret):
        await conversations.run_turn("old", chat, {})
    snapshot = backend.db.chat_snapshot(chat)
    assert snapshot["active_turn"] is None
    assert snapshot["chat"]["queue_paused"] == 1
    answer = snapshot["messages"][-1]
    assert answer["content"] == "Retained partial work"
    assert answer["meta"]["failure"]["error_type"] == "RuntimeError"
    assert answer["meta"]["failure"]["frames"]
    assert answer["meta"]["timing"]["active"] is None
    assert secret not in json.dumps(answer)
    events = backend.db.query("SELECT payload FROM chat_turn_events WHERE turn_id = 'old'")
    assert any(json.loads(row["payload"]).get("reason") == "turn_finalization_failed" for row in events)
    assert secret not in json.dumps(events)
    assert backend.db.one("SELECT state FROM chat_turns WHERE id = 'queued'")["state"] == "queued"
    assert backend.broker.cleanups[-1] == (chat, "old")
    advance.assert_not_called()


async def test_recovery_cleanup_failure_still_ends_turn_but_keeps_queue_paused(backend, monkeypatch):
    chat = backend.db.create_chat("Finalization", "default")["id"]
    backend.db.accept_chat_message(chat, "Run", "old")
    backend.broker.cleanup_error = RuntimeError("Docker unavailable")
    await conversations.recover_failed_turn("old", chat, RuntimeError("owner failed"))
    snapshot = backend.db.chat_snapshot(chat)
    assert snapshot["active_turn"] is None
    assert snapshot["chat"]["queue_paused"] == 1
    assert snapshot["messages"][-1]["meta"]["error"] == conversations.CLEANUP_FAILURE


async def test_recovery_does_not_change_completed_turn_or_another_chat(backend):
    chat = backend.db.create_chat("Finalization", "default")["id"]
    backend.db.accept_chat_message(chat, "Run", "old")
    await conversations.recover_failed_turn("old", "another-chat", RuntimeError())
    assert backend.db.chat_snapshot(chat)["active_turn"] is not None
    backend.db.finish_chat_turn("old", "Already saved", {})
    await conversations.recover_failed_turn("old", chat, RuntimeError())
    assert backend.db.list_messages(chat)[-1]["content"] == "Already saved"
    assert not backend.broker.cleanups


async def test_stop_recovers_ownerless_turn_without_replaying_it(backend):
    chat = backend.db.create_chat("Abandoned", "default")["id"]
    backend.db.accept_chat_message(chat, "Run", "old")
    snapshot = await stop_chat(chat, {"turn_id": "old"})
    assert snapshot["active_turn"] is None
    assert snapshot["chat"]["queue_paused"] == 1
    assert snapshot["messages"][-1]["meta"]["interrupted"] is True
    assert backend.broker.cleanups == [(chat, "old")]


async def test_stop_does_not_misclassify_a_live_silent_owner(backend, monkeypatch):
    chat = backend.db.create_chat("Live", "default")["id"]
    backend.db.accept_chat_message(chat, "Run", "old")
    started = asyncio.Event()
    release = asyncio.Event()

    async def agent(job):
        started.set()
        await release.wait()
        yield {"type": "result", "ok": True}

    monkeypatch.setattr(conversations, "stream_agent", agent)
    task = asyncio.create_task(conversations.run_turn("old", chat, {}))
    try:
        await asyncio.wait_for(started.wait(), 2)
        snapshot = await stop_chat(chat, {"turn_id": "old"})
        assert snapshot["active_turn"] is not None
        assert not backend.broker.cleanups
    finally:
        release.set()
        await asyncio.wait_for(task, 2)
    assert not conversations.turn_has_owner("old")


async def test_second_cancellation_cannot_cancel_recovery(backend, monkeypatch):
    chat = backend.db.create_chat("Finalization", "default")["id"]
    backend.db.accept_chat_message(chat, "Run", "old")
    recovering = asyncio.Event()
    release = asyncio.Event()

    async def fail(*args):
        raise RuntimeError("finalizer failed")

    async def cleanup(*args):
        recovering.set()
        await release.wait()

    monkeypatch.setattr(conversations, "_run_turn", fail)
    monkeypatch.setattr(conversations.broker, "cleanup_chat_agent", cleanup)
    owner = asyncio.create_task(conversations.run_turn("old", chat, {}))
    await asyncio.wait_for(recovering.wait(), 2)
    recovery = next(task for task in background._running if task.get_name() == "chat-recovery-old")
    owner.cancel()
    with pytest.raises(asyncio.CancelledError):
        await owner
    assert not recovery.cancelled()
    release.set()
    await asyncio.wait_for(recovery, 2)
    assert backend.db.chat_snapshot(chat)["active_turn"] is None
