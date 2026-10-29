import json
from unittest.mock import Mock

import pytest
from nautionette.chat_sessions import chat_directory
from nautionette_backend import chat_attachments, chat_sessions, conversations


def native(chat_id, session_id="stable-session"):
    directory = chat_directory(chat_id)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "session.jsonl"
    path.write_text(json.dumps({"type": "session", "id": session_id, "version": 3}) + "\n")
    return path


def test_legacy_import_preserves_all_text_tools_and_original_attachments(backend):
    chat = backend.db.create_chat("Legacy", "default")["id"]
    attachments = []
    for i in range(7):
        attachment = chat_attachments.store_attachment(
            chat, f"original {i}".encode(), "text/plain", f"{i}.txt"
        )
        backend.db.accept_chat_message(
            chat, f"question {i}", f"legacy-{i}", attachment_ids=[attachment["id"]]
        )
        backend.db.finish_chat_turn(
            f"legacy-{i}",
            "answer " + "X" * 17000,
            {"steps": [{"kind": "tool", "name": "bash", "args": {"command": "pwd"}, "result": "evidence"}]},
        )
        attachments.append(attachment)
    backend.db.accept_chat_message(chat, "current", "current")
    history = chat_sessions.legacy_history(chat, "current")
    assert len(history) == 14
    assert len(history[1]["content"]) > 16000
    assert history[1]["steps"][0]["result"] == "evidence"
    assert sum(len(m.get("attachments", [])) for m in history) == 4
    for i, attachment in enumerate(attachments):
        assert (
            chat_directory(chat) / "attachments" / f"{attachment['id']}.txt"
        ).read_bytes() == f"original {i}".encode()
    assert all("/chat-session/attachments/" in history[i]["content"] for i in range(0, 14, 2))


def test_native_resume_never_rebuilds_sql_history_and_fails_closed_on_missing_state(backend, monkeypatch):
    chat = backend.db.create_chat("Native", "default")["id"]
    path = native(chat)
    backend.db.execute("UPDATE chats SET pi_session_id = ? WHERE id = ?", ("stable-session", chat))
    monkeypatch.setattr(backend.db, "list_messages", Mock(side_effect=AssertionError("must not rebuild")))
    assert chat_sessions.legacy_history(chat, "turn") == []
    path.unlink()
    with pytest.raises(ValueError, match="missing"):
        chat_sessions.legacy_history(chat, "turn")
    native(chat, "wrong-session")
    with pytest.raises(ValueError, match="identity mismatch"):
        chat_sessions.legacy_history(chat, "turn")


async def test_turn_persists_identity_and_compaction_ui_projection(backend, monkeypatch):
    chat = backend.db.create_chat("Continuity", "default")["id"]
    backend.db.accept_chat_message(chat, "Run", "session-turn")

    async def agent(job):
        assert job["history"] == []
        yield {"type": "session_ready", "id": "stable-session"}
        yield {"type": "compaction", "state": "started"}
        yield {"type": "compaction", "state": "completed", "tokens_before": 12000}
        yield {"type": "result", "ok": True, "text": "Done"}

    monkeypatch.setattr(conversations, "stream_agent", agent)
    await conversations.run_turn("session-turn", chat, {})
    assert backend.db.get_chat(chat)["pi_session_id"] == "stable-session"
    steps = backend.db.list_messages(chat)[-1]["meta"]["steps"]
    assert steps[1] == {"kind": "compaction", "state": "completed", "tokens_before": 12000}


def test_authenticated_bounded_history_api_and_delete_cleanup(client, db):
    chat = db.create_chat("Inspect", "default")["id"]
    path = native(chat)
    with path.open("a") as handle:
        handle.write(json.dumps({"type": "custom", "id": "large", "data": "X" * 300000}) + "\n")
        handle.write(json.dumps({"type": "custom", "id": "small", "data": "evidence"}) + "\n")
    first = client.get(f"/api/chats/{chat}/session/entries?limit=1").json()
    assert first["entries"][0]["type"] == "session"
    second = client.get(f"/api/chats/{chat}/session/entries?after={first['next']}&limit=1").json()
    assert second["entries"][0]["type"] == "large_entry"
    assert len(json.dumps(second)) < 10000
    third = client.get(f"/api/chats/{chat}/session/entries?after={second['next']}").json()
    assert third["entries"][0]["id"] == "small" and third["next"] is None
    assert client.get(f"/api/chats/{chat}/session/entries?after=-1").status_code == 422
    assert (
        client.get(
            f"/api/chats/{chat}/session/entries", headers={"Authorization": "Bearer wrong"}
        ).status_code
        == 401
    )
    assert client.delete(f"/api/chats/{chat}").status_code == 200
    assert not path.parent.exists()
    assert client.get(f"/api/chats/{chat}/session/entries").status_code == 404


def test_delete_cannot_remove_an_active_native_session(client, db):
    chat = db.create_chat("Active", "default")["id"]
    path = native(chat)
    db.accept_chat_message(chat, "Run", "active-session-turn")
    assert client.delete(f"/api/chats/{chat}").status_code == 409
    assert path.exists() and db.get_chat(chat)


@pytest.mark.parametrize("chat_id", ["../escape", "/absolute/escape", "a/b", "", "x" * 129])
def test_shared_session_path_rejects_traversal(chat_id):
    with pytest.raises(ValueError, match="Invalid"):
        chat_directory(chat_id)
