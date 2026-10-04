import json
from unittest.mock import Mock

import pytest
from nautionette_backend import conversations
from nautionette_backend.events import EventBus

RECOVERY = {
    "type": "recovery",
    "attempt": 1,
    "max_attempts": 2,
    "message": "Automatic recovery 1/2: interrupted model connection",
}


def start(backend, monkeypatch):
    chat = backend.db.create_chat("Recovery", "default")["id"]
    backend.db.accept_chat_message(chat, "Do the work", "turn")
    backend.db.accept_chat_message(chat, "Then do more", "queued", queue=True)
    launch = Mock()
    monkeypatch.setattr(conversations, "launch_next", launch)
    events = EventBus()
    monkeypatch.setattr(conversations, "bus", events)
    return chat, launch, events


async def test_recovery_retains_one_turn_timeline_and_status_through_idle_events(backend, monkeypatch):
    chat, launch, events = start(backend, monkeypatch)
    calls = []

    async def agent(job):
        calls.append(job)
        yield {"type": "delta", "text": "Work saved. "}
        yield {"type": "tool", "id": "edit", "name": "write", "args": {"path": "kept.txt"}}
        yield {"type": "tool_done", "id": "edit", "result": "Saved", "error": False}
        yield RECOVERY
        yield {"type": "status", "state": "recovering", "message": "Recovering automatically in 2s"}
        for event in ({"type": "phase", "phase": "other"}, {"type": "agent_end"}):
            yield event
            snapshot = backend.db.chat_snapshot(chat)
            assert snapshot["active_turn"]["id"] == "turn"
            assert snapshot["active_turn"]["status"] == "Recovering automatically in 2s"
            assert snapshot["active_turn"]["timing"]["active"] == "other"
            assert snapshot["active_turn"]["steps"][-1]["kind"] == "recovery"
            assert [m["role"] for m in snapshot["messages"]] == ["user", "user"]
            assert backend.db.list_chats()[0]["answering"] is True
            launch.assert_not_called()
        yield {"type": "phase", "phase": "model"}
        assert backend.db.chat_snapshot(chat)["active_turn"]["status"] == ""
        yield {"type": "delta", "text": "Continued without repeating the edit."}
        yield {"type": "result", "ok": True}

    monkeypatch.setattr(conversations, "stream_agent", agent)
    await conversations.run_turn("turn", chat, {"prompt": "Do the work"})
    assert len(calls) == 1  # The backend never replays a cold container job.
    assert backend.db.list_messages(chat)[-1]["meta"]["error"] is None
    launch.assert_called_once_with(chat)
    snapshot = backend.db.chat_snapshot(chat)
    answer = snapshot["messages"][-1]
    assert snapshot["active_turn"] is None
    assert snapshot["chat"]["queue_paused"] == 0
    assert answer["content"] == "Work saved. Continued without repeating the edit."
    assert answer["meta"]["error"] is None
    assert answer["meta"]["tools"] == ["write"]
    assert [step["kind"] for step in answer["meta"]["steps"]] == ["text", "tool", "recovery", "text"]
    assert answer["meta"]["steps"][1]["result"] == "Saved"
    assert answer["meta"]["timing"]["active"] is None
    assert [e["ok"] for e in events.history() if e["kind"] == "chat.answered"] == [True]
    history = [
        json.loads(row["payload"])
        for row in backend.db.query(
            "SELECT payload FROM chat_turn_events WHERE turn_id = 'turn' ORDER BY seq"
        )
    ]
    assert sum(e["type"] == "recovery" for e in history) == 1
    assert sum(e["type"] == "done" for e in history) == 1
    assert not any(e["type"] == "error" for e in history)


async def test_exhausted_recovery_preserves_error_and_pauses_dependent_queue(backend, monkeypatch):
    chat, launch, events = start(backend, monkeypatch)

    async def agent(job):
        yield {"type": "delta", "text": "Partial answer"}
        yield RECOVERY
        yield {**RECOVERY, "attempt": 2, "message": "Automatic recovery 2/2"}
        yield {"type": "result", "ok": False, "error": "503 unavailable; automatic recovery exhausted"}

    monkeypatch.setattr(conversations, "stream_agent", agent)
    await conversations.run_turn("turn", chat, {})
    launch.assert_not_called()
    snapshot = backend.db.chat_snapshot(chat)
    assert snapshot["chat"]["queue_paused"] == 1
    assert snapshot["messages"][-1]["content"] == "Partial answer"
    assert "recovery exhausted" in snapshot["messages"][-1]["meta"]["error"]
    assert snapshot["messages"][1]["meta"]["queued"] is True
    assert [e["ok"] for e in events.history() if e["kind"] == "chat.answered"] == [False]


@pytest.mark.parametrize("completed_tool", [False, True])
async def test_missing_result_is_failure_not_success_and_never_replays_tools(
    backend, monkeypatch, completed_tool
):
    chat, launch, events = start(backend, monkeypatch)
    calls = []

    async def agent(job):
        calls.append(job)
        yield {"type": "delta", "text": "Partial work"}
        yield {"type": "tool", "id": "publish", "name": "deploy", "args": {}}
        if completed_tool:
            yield {"type": "tool_done", "id": "publish", "result": "Published", "error": False}
        yield {"type": "closed"}

    monkeypatch.setattr(conversations, "stream_agent", agent)
    await conversations.run_turn("turn", chat, {})
    assert len(calls) == 1
    assert backend.broker.cleanups == [(chat, "turn")]
    launch.assert_not_called()
    snapshot = backend.db.chat_snapshot(chat)
    answer = snapshot["messages"][-1]
    assert snapshot["chat"]["queue_paused"] == 1
    assert answer["content"] == "Partial work"
    assert "before a final result" in answer["meta"]["error"]
    assert answer["meta"]["tools"] == ["deploy"]
    assert bool(answer["meta"]["steps"][1].get("interrupted")) is not completed_tool
    assert [e["ok"] for e in events.history() if e["kind"] == "chat.answered"] == [False]


async def test_empty_exception_is_still_a_failure(backend, monkeypatch):
    chat, launch, _ = start(backend, monkeypatch)

    async def agent(job):
        yield {"type": "delta", "text": "Kept"}
        raise TimeoutError()

    monkeypatch.setattr(conversations, "stream_agent", agent)
    await conversations.run_turn("turn", chat, {})
    launch.assert_not_called()
    assert backend.db.list_messages(chat)[-1]["meta"]["error"] == "TimeoutError"


async def test_success_result_does_not_hide_a_fatal_runtime_error(backend, monkeypatch):
    chat, launch, _ = start(backend, monkeypatch)

    async def agent(job):
        yield {"type": "error", "message": "Permission denied"}
        yield {"type": "result", "ok": True, "text": "Not a real success"}

    monkeypatch.setattr(conversations, "stream_agent", agent)
    await conversations.run_turn("turn", chat, {})
    launch.assert_not_called()
    assert backend.db.chat_snapshot(chat)["chat"]["queue_paused"] == 1
    assert backend.db.list_messages(chat)[-1]["meta"]["error"] == "Permission denied"


async def test_stop_during_recovery_wins_over_a_late_success(backend, monkeypatch):
    chat, launch, events = start(backend, monkeypatch)

    async def agent(job):
        yield {"type": "delta", "text": "Kept"}
        yield RECOVERY
        yield {"type": "status", "state": "recovering", "message": "Recovering automatically in 2s"}
        assert backend.db.stop_chat_turn(chat, "turn")
        yield {"type": "result", "ok": True}

    monkeypatch.setattr(conversations, "stream_agent", agent)
    await conversations.run_turn("turn", chat, {})
    launch.assert_not_called()
    snapshot = backend.db.chat_snapshot(chat)
    assert snapshot["chat"]["queue_paused"] == 1
    assert snapshot["messages"][-1]["meta"]["interrupted"] is True
    assert snapshot["messages"][-1]["meta"]["error"] == "Stopped by you."
    assert snapshot["messages"][-1]["meta"]["steps"][-1]["kind"] == "recovery"
    assert [e["ok"] for e in events.history() if e["kind"] == "chat.answered"] == [False]


def test_restart_retains_recovery_notice_but_never_replays_an_uncertain_job(backend):
    chat = backend.db.create_chat("Restart", "default")["id"]
    backend.db.accept_chat_message(chat, "Do the work", "turn")
    steps = [{"kind": "text", "text": "Kept"}, {"kind": "recovery", "message": RECOVERY["message"]}]
    backend.db.record_chat_progress("turn", RECOVERY, steps, "Recovering")
    conversations.recover_interrupted()
    conversations.recover_interrupted()
    snapshot = backend.db.chat_snapshot(chat)
    assert snapshot["active_turn"] is None
    assert snapshot["chat"]["queue_paused"] == 1
    assert len(snapshot["messages"]) == 2
    assert snapshot["messages"][-1]["meta"]["steps"] == steps
    assert "backend restart" in snapshot["messages"][-1]["meta"]["error"]
