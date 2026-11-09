import json
from unittest.mock import AsyncMock, Mock

import pytest
from nautionette.chat_sessions import chat_directory, session_lock
from nautionette_backend import chat_attachments, chat_branches, chat_sessions
from nautionette_backend.routers import chats


def native(db, chat):
    directory = chat_directory(chat["id"])
    directory.mkdir(parents=True, exist_ok=True)
    lines = [
        {"type": "session", "id": "original-session", "version": 3},
        {
            "type": "message",
            "id": "entry-1",
            "parentId": None,
            "message": {"role": "user", "content": [{"type": "text", "text": "Idea"}]},
        },
        {"type": "compaction", "id": "entry-2", "parentId": "entry-1", "summary": "Full context"},
        {
            "type": "message",
            "id": "entry-3",
            "parentId": "entry-2",
            "message": {"role": "toolResult", "toolCallId": "tool-1", "content": "Exact evidence"},
        },
    ]
    path = directory / "session.jsonl"
    path.write_text("".join(json.dumps(line) + "\n" for line in lines))
    db.execute("UPDATE chats SET pi_session_id = ? WHERE id = ?", ("original-session", chat["id"]))
    return path, lines


def test_branch_preserves_settings_native_context_and_independent_messages(client, db):
    original = db.create_chat(
        "Explore",
        "default",
        model="test/model",
        tools=["bash"],
        reasoning_effort="high",
        system_prompt="Explore carefully",
        packages=["package-id"],
        timeout_exempt=True,
        agent_id="deleted-profile",
        agent_name="Researcher",
        agent_customized=True,
    )
    question = db.add_message(original["id"], "user", "One idea")
    answer = db.add_message(
        original["id"], "assistant", "A direction", {"steps": [{"kind": "tool", "result": "evidence"}]}
    )
    path, lines = native(db, original)
    before = path.read_bytes()
    response = client.post(f"/api/chats/{original['id']}/branch")
    assert response.status_code == 200, response.text
    branch = response.json()
    assert branch["id"] != original["id"]
    assert branch["parent_chat_id"] == original["id"]
    assert branch["parent_chat_title"] == "Explore"
    assert branch["title"] == "Branch: Explore"
    for key in [
        "model",
        "tools",
        "reasoning_effort",
        "agent_set",
        "system_prompt",
        "packages",
        "agent_id",
        "agent_name",
        "agent_customized",
        "timeout_exempt",
    ]:
        assert branch[key] == original[key]
    assert branch["pi_session_id"] != "original-session"
    copied = chat_sessions.entries(branch["id"])["entries"]
    assert copied[0]["id"] == branch["pi_session_id"]
    assert copied[1:] == lines[1:]
    assert path.read_bytes() == before
    messages = db.list_messages(branch["id"])
    assert [m["content"] for m in messages] == ["One idea", "A direction"]
    assert {m["id"] for m in messages}.isdisjoint({question["id"], answer["id"]})
    assert messages[-1]["meta"] == answer["meta"]
    assert not branch["unread"]
    assert db.chat_snapshot(branch["id"])["active_turn"] is None
    assert chat_sessions.legacy_history(branch["id"], "next-turn") == []
    db.add_message(branch["id"], "user", "Another idea")
    assert len(db.list_messages(original["id"])) == 2
    assert client.delete(f"/api/chats/{original['id']}").status_code == 200
    assert chat_sessions.entries(branch["id"])["entries"][1:] == lines[1:]
    assert len(db.list_messages(branch["id"])) == 3


def test_branch_copies_attachments_without_sharing_ids_and_legacy_import_works(client, db):
    chat = db.create_chat("Files", "default")
    file = chat_attachments.store_attachment(chat["id"], b"original bytes", "text/plain", "note.txt")
    message = db.add_message(chat["id"], "user", "Read this", {"attachments": [file]})
    db.execute("UPDATE chat_images SET message_id = ? WHERE id = ?", (message["id"], file["id"]))
    history = chat_sessions.legacy_history(chat["id"], "next")
    assert history
    branch = client.post(f"/api/chats/{chat['id']}/branch").json()
    copied = db.list_messages(branch["id"])[0]["meta"]["attachments"][0]
    assert copied["id"] != file["id"]
    assert chat_attachments.load_attachments(branch["id"], [copied])["files"][0]["name"] == "note.txt"
    assert (
        chat_directory(branch["id"]) / "attachments" / f"{file['id']}.txt"
    ).read_bytes() == b"original bytes"
    client.delete(f"/api/chats/{chat['id']}")
    assert chat_sessions.legacy_history(branch["id"], "next")[0]["attachments"] == [copied]


def test_branch_rejects_running_queued_and_locked_sessions(client, db):
    chat = db.create_chat("Busy", "default")
    db.accept_chat_message(chat["id"], "Work", "active")
    endpoint = f"/api/chats/{chat['id']}/branch"
    assert client.post(endpoint).status_code == 409
    db.execute("UPDATE chat_turns SET state = 'queued' WHERE id = 'active'")
    assert client.post(endpoint).status_code == 409
    db.execute("DELETE FROM chat_turns WHERE id = 'active'")
    with session_lock(chat["id"]):
        assert client.post(endpoint).status_code == 409
    assert len(db.list_chats()) == 1


@pytest.mark.parametrize("problem", ["missing", "identity", "symlink", "size"])
def test_branch_fails_closed_and_cleans_up_partial_state(client, db, monkeypatch, tmp_path, problem):
    chat = db.create_chat("Original", "default")
    path, _ = native(db, chat)
    if problem == "missing":
        path.unlink()
    elif problem == "identity":
        db.execute("UPDATE chats SET pi_session_id = 'wrong' WHERE id = ?", (chat["id"],))
    elif problem == "symlink":
        (chat_directory(chat["id"]) / "unsafe").symlink_to(tmp_path)
    else:
        monkeypatch.setattr(chat_branches, "MAX_COPY_BYTES", 1)
    before = set(path.parent.parent.iterdir())
    response = client.post(f"/api/chats/{chat['id']}/branch")
    assert response.status_code == 409, response.text
    assert len(db.list_chats()) == 1
    assert {p for p in path.parent.parent.iterdir() if p.is_dir()} == {p for p in before if p.is_dir()}


def test_branch_rolls_back_sql_and_native_copy_on_failure(client, db, monkeypatch):
    chat = db.create_chat("Rollback", "default")
    path, _ = native(db, chat)
    file = chat_attachments.store_attachment(chat["id"], b"content", "text/plain", "a.txt")
    db.add_message(chat["id"], "user", "missing file", {"attachments": [file]})
    db.execute("DELETE FROM chat_images WHERE id = ?", (file["id"],))
    before = {p for p in path.parent.parent.iterdir() if p.is_dir()}
    assert client.post(f"/api/chats/{chat['id']}/branch").status_code == 409
    assert len(db.list_chats()) == 1 and path.exists()
    assert {p for p in path.parent.parent.iterdir() if p.is_dir()} == before


def test_branch_requires_auth_and_existing_chat(client, anonymous, db):
    chat = db.create_chat("Private", "default")
    assert anonymous.post(f"/api/chats/{chat['id']}/branch").status_code == 401
    assert client.post("/api/chats/does-not-exist/branch").status_code == 404


def test_branch_fails_closed_when_orphan_cleanup_is_unavailable(client, db, monkeypatch):
    chat = db.create_chat("Orphan writer", "default")
    monkeypatch.setattr(chats, "reconcile_chat_agents", AsyncMock(side_effect=RuntimeError("Broker down")))
    response = client.post(f"/api/chats/{chat['id']}/branch")
    assert response.status_code == 503
    assert len(db.list_chats()) == 1


def test_empty_chat_can_branch_without_contacting_models(client, db, monkeypatch):
    chat = db.create_chat("Empty", "default")
    monkeypatch.setattr(
        chat_sessions, "legacy_history", Mock(side_effect=AssertionError("no reconstruction"))
    )
    branch = client.post(f"/api/chats/{chat['id']}/branch").json()
    assert branch["pi_session_id"] is None
    assert db.list_messages(branch["id"]) == []
    assert branch["parent_chat_id"] == chat["id"]
