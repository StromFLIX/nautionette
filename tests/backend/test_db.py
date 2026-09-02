"""SQLite storage: what survives a restart."""

from __future__ import annotations

from nautionette_backend.db import Database


def test_a_chat_summary_carries_its_last_message(db):
    chat = db.create_chat("Research", "default")
    db.add_message(chat["id"], "user", "what is the plan?")
    db.add_message(chat["id"], "assistant", "the plan is\nto keep going")
    listed = db.list_chats()[0]
    assert listed["message_count"] == 2
    assert listed["last_message"] == {"role": "assistant", "preview": "the plan is to keep going"}


def test_a_chat_with_nothing_in_it_has_no_last_message(db):
    db.create_chat("Empty", "default")
    assert db.list_chats()[0]["last_message"] is None


def test_the_tool_selection_round_trips_and_is_deduplicated(db):
    chat = db.create_chat("t", "default", tools=["b", "a", "b"])
    assert db.get_chat(chat["id"])["tools"] == ["a", "b"]
    assert db.update_chat(chat["id"], {"tools": None})["tools"] is None


def test_only_the_editable_columns_can_be_written(db):
    chat = db.create_chat("t", "default")
    db.update_chat(chat["id"], {"id": "hijacked", "created_at": 0, "title": "fine"})
    assert db.get_chat(chat["id"])["title"] == "fine"
    assert db.get_chat("hijacked") is None


def test_a_message_touches_its_chat(db):
    chat = db.create_chat("t", "default")
    db.execute("UPDATE chats SET updated_at = 0 WHERE id = ?", (chat["id"],))
    db.add_message(chat["id"], "user", "hello")
    assert db.get_chat(chat["id"])["updated_at"] > 0


def test_workflow_settings_default_without_a_row(db):
    assert db.workflow_settings("never_seen") == {
        "name": "never_seen",
        "disabled": False,
        "chat_mode": "same",
        "chat_id": None,
    }


def test_workflow_settings_are_merged_not_replaced(db):
    db.set_workflow_settings("wf", {"chat_id": "abc"})
    settings = db.set_workflow_settings("wf", {"disabled": True})
    assert settings["chat_id"] == "abc"
    assert settings["disabled"] is True

    db.forget_workflow("wf")
    assert db.workflow_settings("wf")["chat_id"] is None


def test_a_run_records_its_input_and_later_its_result(db):
    db.record_run("wf", "wf-1", "run-1", "manual", {"url": "https://a"})
    db.update_run("wf-1", "completed", {"summary": "done"})
    run = db.list_runs("wf")[0]
    assert run["status"] == "completed"
    assert run["input"] == {"url": "https://a"}
    assert run["result"] == {"summary": "done"}


def test_a_setting_keeps_its_type(db):
    db.set_setting("a_number", 42)
    db.set_setting("a_mapping", {"key": "value"})
    assert db.get_setting("a_number") == 42
    assert db.get_setting("a_mapping") == {"key": "value"}
    assert db.get_setting("absent", "fallback") == "fallback"


def test_the_event_log_does_not_grow_without_end(db):
    for index in range(20):
        db.add_event("worker", "tick", {"index": index})
    assert len(db.recent_events(limit=5)) == 5
    assert db.recent_events()[-1]["payload"] == {"index": 19}


def test_a_fresh_database_creates_its_own_directory(tmp_path):
    Database(str(tmp_path / "nested" / "deeper" / "test.db"))
    assert (tmp_path / "nested" / "deeper" / "test.db").is_file()
