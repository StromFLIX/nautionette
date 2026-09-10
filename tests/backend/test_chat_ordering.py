"""Chat recency counts live agent work without losing message-only clocks."""

from __future__ import annotations

import pytest
from nautionette_backend import conversations
from nautionette_backend.db import Database
from nautionette_backend.events import EventBus


def clock(monkeypatch, value):
    monkeypatch.setattr("nautionette_backend.db.time.time", lambda: value)


@pytest.mark.parametrize(
    "event",
    [
        {"type": "delta", "text": "Checking now"},
        {"type": "thinking", "text": "Consider the options"},
        {"type": "tool", "id": "tool", "name": "bash"},
        {"type": "tool_done", "id": "tool", "result": "Done"},
        {"type": "status", "message": "Preparing worktrees"},
        {"type": "phase", "phase": "reply"},
        {"type": "usage", "context": {"tokens": 100}},
        {"type": "error", "message": "Retry needed"},
        {"type": "result", "ok": True},
    ],
)
def test_live_activity_reorders_chats_before_the_reply_finishes(client, db, monkeypatch, event):
    clock(monkeypatch, 10)
    running = db.create_chat("Still working", "default")["id"]
    db.accept_chat_message(running, "First request", "turn")
    clock(monkeypatch, 20)
    completed = db.create_chat("Already done", "default")["id"]
    db.add_message(completed, "assistant", "Done")
    assert [chat["id"] for chat in db.list_chats()] == [completed, running]

    clock(monkeypatch, 30)
    assert db.record_chat_progress("turn", event, [], "")
    listed = client.get("/api/chats").json()["chats"]
    assert [chat["id"] for chat in listed] == [running, completed]
    assert listed[0]["answering"] is True
    assert listed[0]["updated_at"] == 30
    assert listed[0]["last_message_at"] == (30 if event["type"] == "delta" else 10)
    assert listed[0]["last_user_message_at"] == 10
    assert len(db.list_messages(running)) == 1  # Progress does not create transcript messages.
    assert listed[0]["unread"] is False


def test_message_clocks_include_saved_replies_queued_users_and_streamed_prose(db, monkeypatch):
    clock(monkeypatch, 10)
    chat_id = db.create_chat("Work", "default")["id"]
    clock(monkeypatch, 20)
    db.accept_chat_message(chat_id, "Start", "turn")
    clock(monkeypatch, 30)
    db.record_chat_progress("turn", {"type": "delta", "text": "Working"}, [], "")
    clock(monkeypatch, 40)
    db.record_chat_progress("turn", {"type": "tool", "name": "read"}, [], "")
    chat = db.get_chat(chat_id)
    assert (chat["updated_at"], chat["last_message_at"], chat["last_user_message_at"]) == (40, 30, 20)

    clock(monkeypatch, 50)
    db.accept_chat_message(chat_id, "Next", "queued", queue=True)
    clock(monkeypatch, 60)
    assert not db.accept_chat_message(chat_id, "Next", "queued", queue=True)[1]
    chat = db.get_chat(chat_id)
    assert (chat["updated_at"], chat["last_message_at"], chat["last_user_message_at"]) == (50, 50, 50)
    assert db.consume_chat_input("turn", "queued", "Working", {})
    assert db.get_chat(chat_id)["last_message_at"] == 60

    clock(monkeypatch, 70)
    db.finish_chat_turn("turn", "Finished", {})
    chat = db.get_chat(chat_id)
    assert (chat["updated_at"], chat["last_message_at"], chat["last_user_message_at"]) == (70, 70, 50)
    clock(monkeypatch, 80)
    assert not db.record_chat_progress("turn", {"type": "delta", "text": "Late event"}, [], "")
    assert db.get_chat(chat_id)["updated_at"] == 70

    db.add_message(chat_id, "assistant", "Workflow notification")
    assert db.get_chat(chat_id)["last_message_at"] == 80
    assert db.get_chat(chat_id)["last_user_message_at"] == 50
    clock(monkeypatch, 90)
    db.add_message(chat_id, "user", "Thanks")
    assert db.get_chat(chat_id)["last_user_message_at"] == 90


def test_legacy_clocks_are_backfilled_and_live_clocks_survive_restart(tmp_path, monkeypatch):
    path = str(tmp_path / "chats.db")
    clock(monkeypatch, 10)
    db = Database(path)
    empty = db.create_chat("Empty", "default")["id"]
    chat_id = db.create_chat("Legacy", "default")["id"]
    clock(monkeypatch, 20)
    db.accept_chat_message(chat_id, "Request", "turn")
    clock(monkeypatch, 30)
    db.add_message(chat_id, "assistant", "Answer")
    db.execute("ALTER TABLE chats DROP COLUMN last_message_at")
    db.execute("ALTER TABLE chats DROP COLUMN last_user_message_at")
    db._conn.close()

    restored = Database(path)
    assert restored.get_chat(empty)["last_message_at"] == 10
    assert restored.get_chat(empty)["last_user_message_at"] == 10
    assert restored.get_chat(chat_id)["last_message_at"] == 30
    assert restored.get_chat(chat_id)["last_user_message_at"] == 20
    clock(monkeypatch, 40)
    restored.record_chat_progress("turn", {"type": "delta", "text": "More"}, [], "")
    clock(monkeypatch, 50)
    restored.record_chat_progress("turn", {"type": "tool", "name": "read"}, [], "")
    restored._conn.close()
    restarted = Database(path)
    chat = restarted.get_chat(chat_id)
    assert (chat["updated_at"], chat["last_message_at"], chat["last_user_message_at"]) == (50, 40, 20)
    restarted._conn.close()


async def test_sidebar_progress_notices_are_throttled_but_completion_is_immediate(backend, monkeypatch):
    chat_id = backend.db.create_chat("Work", "default")["id"]
    backend.db.accept_chat_message(chat_id, "Start", "turn")
    bus = EventBus()
    monkeypatch.setattr(conversations, "bus", bus)
    ticks = [0, 0.1, 0.9, 1, 1.1, 2]
    current = 0
    monkeypatch.setattr(conversations, "monotonic", lambda: current)

    async def agent(job):
        nonlocal current
        for index, tick in enumerate(ticks):
            current = tick
            clock(monkeypatch, 100 + tick)
            yield {"type": "delta", "text": "."}
            assert backend.db.get_chat(chat_id)["last_message_at"] == 100 + tick
            assert len(bus.history()) == [1, 1, 1, 2, 2, 3][index]
            assert backend.db.list_chats()[0]["answering"] is True
        yield {"type": "result", "ok": True}

    monkeypatch.setattr(conversations, "stream_agent", agent)
    await conversations.run_turn("turn", chat_id, {"prompt": "Start"})
    assert backend.db.list_messages(chat_id)[-1]["meta"]["error"] is None
    assert [event["kind"] for event in bus.history()] == ["chat.progress"] * 3 + ["chat.answered"]
    assert all(event["chat_id"] == chat_id for event in bus.history())
    assert backend.db.list_chats()[0]["answering"] is False
