import asyncio
from unittest.mock import AsyncMock, Mock

from nautionette_backend import background, conversations


def start_turn(backend, turn="old"):
    chat = backend.db.create_chat("Recovery", "default")["id"]
    backend.db.accept_chat_message(chat, "Run", turn)
    backend.broker.agents.append({"chat_id": chat, "turn_id": turn})
    return chat


async def test_restart_cleans_running_and_previously_finished_survivors(backend):
    interrupted = start_turn(backend)
    finished = start_turn(backend, "finished")
    backend.db.finish_chat_turn("finished", "Already saved", {})
    conversations.recover_interrupted()
    # A legitimate new turn after recovery must not be killed by reconciliation.
    active = start_turn(backend, "new")
    await conversations.recover_chat_agents()
    assert backend.broker.cleanups == [(interrupted, "old"), (finished, "finished")]
    assert backend.broker.agents == [{"chat_id": active, "turn_id": "new"}]
    assert backend.db.chat_snapshot(interrupted)["active_turn"] is None
    assert backend.db.list_messages(finished)[-1]["content"] == "Already saved"


async def test_new_turn_retries_cleanup_before_touching_project_worktrees(backend, monkeypatch, tmp_path):
    chat = start_turn(backend)
    conversations.recover_interrupted()
    saved = tmp_path / "unfinished.txt"
    saved.write_text("keep my edits")
    prepare = Mock(return_value={})
    monkeypatch.setattr(conversations.projects, "prepare_worktrees", prepare)
    monkeypatch.setattr(conversations.projects, "agent_credentials", AsyncMock(return_value=[]))
    stream = Mock()
    monkeypatch.setattr(conversations, "stream_agent", stream)
    backend.broker.cleanup_error = RuntimeError("Docker unavailable")
    backend.db.accept_chat_message(chat, "Retry", "retry")
    await conversations.run_turn("retry", chat, {"project_ids": ["project"]})
    prepare.assert_not_called()
    stream.assert_not_called()
    snapshot = backend.db.chat_snapshot(chat)
    assert snapshot["chat"]["queue_paused"] == 1
    assert conversations.CLEANUP_FAILURE in snapshot["messages"][-1]["content"]

    backend.broker.cleanup_error = None
    backend.db.accept_chat_message(chat, "Try again", "retry2")

    async def agent(job):
        assert not backend.broker.agents  # Old container removed before execution.
        assert saved.read_text() == "keep my edits"
        yield {"type": "delta", "text": "Recovered"}
        yield {"type": "result", "ok": True}

    monkeypatch.setattr(conversations, "stream_agent", agent)
    await conversations.run_turn("retry2", chat, {"project_ids": ["project"]})
    prepare.assert_called_once_with(chat, ["project"])
    assert backend.db.list_messages(chat)[-1]["content"] == "Recovered"
    assert saved.read_text() == "keep my edits"


async def test_shutdown_waits_for_exact_agent_cleanup_before_finishing(backend, monkeypatch):
    chat = start_turn(backend)
    started = asyncio.Event()
    cleaning = asyncio.Event()
    release = asyncio.Event()

    async def agent(job):
        yield {"type": "delta", "text": "Saved partial answer"}
        started.set()
        await asyncio.Event().wait()

    async def cleanup(chat_id, turn_id):
        assert (chat_id, turn_id) == (chat, "old")
        assert backend.db.chat_snapshot(chat)["active_turn"] is not None
        cleaning.set()
        await release.wait()

    monkeypatch.setattr(conversations, "stream_agent", agent)
    monkeypatch.setattr(conversations.broker, "cleanup_chat_agent", cleanup)
    task = background.spawn(conversations.run_turn("old", chat, {}), name="chat-old")
    await asyncio.wait_for(started.wait(), 2)
    draining = asyncio.create_task(background.drain())
    try:
        await asyncio.wait_for(cleaning.wait(), 2)
        assert not draining.done()
    finally:
        release.set()
        await asyncio.wait_for(draining, 2)
    assert task.cancelled()
    snapshot = backend.db.chat_snapshot(chat)
    assert snapshot["active_turn"] is None
    assert snapshot["chat"]["queue_paused"] == 1
    assert snapshot["messages"][-1]["content"] == "Saved partial answer"
    assert "shutdown" in snapshot["messages"][-1]["meta"]["error"]


async def test_cleanup_failure_preserves_answer_and_prevents_queue_advancing(backend, monkeypatch):
    chat = start_turn(backend)
    backend.db.accept_chat_message(chat, "Next", "queued", queue=True)
    backend.db.execute("UPDATE chat_turns SET job = '{}' WHERE id = 'queued'")
    backend.broker.cleanup_error = RuntimeError("Cannot stop agent")
    next_turn = Mock()
    monkeypatch.setattr(conversations, "launch_next", next_turn)

    async def agent(job):
        yield {"type": "delta", "text": "Partial output"}
        raise RuntimeError("stream disconnected")

    monkeypatch.setattr(conversations, "stream_agent", agent)
    await conversations.run_turn("old", chat, {})
    next_turn.assert_not_called()
    snapshot = backend.db.chat_snapshot(chat)
    assert snapshot["messages"][-1]["content"] == "Partial output"
    assert snapshot["messages"][-1]["meta"]["error"] == conversations.CLEANUP_FAILURE
    assert snapshot["chat"]["queue_paused"] == 1
    assert backend.broker.agents == [{"chat_id": chat, "turn_id": "old"}]
    backend.broker.cleanup_error = None
    await conversations.reconcile_chat_agents(chat)
    assert backend.broker.agents == []


async def test_broker_inventory_outage_blocks_new_work(backend, monkeypatch):
    chat = start_turn(backend)
    monkeypatch.setattr(conversations.broker, "chat_agents", AsyncMock(side_effect=RuntimeError("offline")))
    prepare = Mock()
    monkeypatch.setattr(conversations.projects, "prepare_worktrees", prepare)
    await conversations.run_turn("old", chat, {})
    prepare.assert_not_called()
    snapshot = backend.db.chat_snapshot(chat)
    assert snapshot["messages"][-1]["meta"]["error"] == conversations.CLEANUP_FAILURE
    assert snapshot["chat"]["queue_paused"] == 1


async def test_recovery_retries_broker_startup_failure(backend, monkeypatch):
    chat = start_turn(backend)
    conversations.recover_interrupted()
    inventory = AsyncMock(side_effect=[RuntimeError("starting"), backend.broker.agents])
    monkeypatch.setattr(conversations.broker, "chat_agents", inventory)
    monkeypatch.setattr(conversations.asyncio, "sleep", AsyncMock())
    await conversations.recover_chat_agents()
    assert inventory.await_count == 2
    assert backend.broker.cleanups == [(chat, "old")]
