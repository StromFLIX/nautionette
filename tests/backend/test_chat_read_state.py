"""Persistent unread reminders and race-safe acknowledgements of displayed replies."""

import pytest
from nautionette_backend.db import Database


def test_only_assistant_replies_make_a_chat_unread(client, db):
    chat = db.create_chat("Read state", "default")
    path = f"/api/chats/{chat['id']}"
    db.add_message(chat["id"], "user", "hello")
    assert client.get("/api/chats").json()["chats"][0]["unread"] is False
    reply = db.add_message(chat["id"], "assistant", "hello back")
    before = db.get_chat(chat["id"])["updated_at"]
    assert client.get(path).json()["chat"]["unread"] is True
    # Fetching history (including cache warming) never marks it read.
    assert client.get("/api/chats").json()["chats"][0]["unread"] is True
    result = client.patch(path + "/read-state", json={"message_id": reply["id"], "revision": 0})
    assert result.status_code == 200
    assert result.json()["unread"] is False
    assert result.json()["updated_at"] == before
    db.add_message(chat["id"], "user", "follow-up")
    assert db.get_chat(chat["id"])["unread"] is False
    db.add_message(chat["id"], "assistant", "New workflow notification", {"workflow": "daily"})
    assert client.get("/api/chats").json()["chats"][0]["unread"] is True


def test_acknowledgements_cannot_hide_newer_replies_or_move_backwards(client, db):
    chat = db.create_chat("Concurrent", "default")
    first = db.add_message(chat["id"], "assistant", "first")
    second = db.add_message(chat["id"], "assistant", "second")
    path = f"/api/chats/{chat['id']}/read-state"
    assert client.patch(path, json={"message_id": first["id"], "revision": 0}).json()["unread"]
    assert not client.patch(path, json={"message_id": second["id"], "revision": 0}).json()["unread"]
    assert not client.patch(path, json={"message_id": first["id"], "revision": 0}).json()["unread"]


def test_manual_reminders_survive_old_acknowledgements_and_clear_on_next_visit(client, db):
    chat = db.create_chat("Reminder", "default")
    reply = db.add_message(chat["id"], "assistant", "done")
    path = f"/api/chats/{chat['id']}/read-state"
    client.patch(path, json={"unread": False})
    result = client.patch(path, json={"unread": True}).json()
    assert result["unread"] is True
    revision = result["read_revision"]
    # A request sent before 'mark unread' must not undo the reminder.
    stale = {"message_id": reply["id"], "revision": revision - 1, "clear_manual": True}
    assert client.patch(path, json=stale).json()["unread"] is True
    # Even fresh background updates preserve manual reminders.
    fresh = {"message_id": reply["id"], "revision": revision}
    assert client.patch(path, json=fresh).json()["unread"] is True
    assert not client.patch(path, json={**fresh, "clear_manual": True}).json()["unread"]


def test_mark_read_does_not_dismiss_internet_approval(client, db):
    chat = db.create_chat("Approval", "default")
    db.execute("UPDATE chats SET internet_status = 'pending' WHERE id = ?", (chat["id"],))
    path = f"/api/chats/{chat['id']}/read-state"
    assert client.patch(path, json={"unread": True}).json()["unread"]
    result = client.patch(path, json={"unread": False}).json()
    assert result["unread"] is False
    assert result["internet_status"] == "pending"


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"unread": "yes"},
        {"revision": 0},
        {"message_id": None, "revision": True},
        {"message_id": None, "revision": -1},
        {"message_id": [], "revision": 0},
        {"message_id": None, "revision": 0, "clear_manual": "true"},
        {"message_id": "missing", "revision": 0},
    ],
)
def test_invalid_read_state_is_rejected(client, db, payload):
    chat = db.create_chat("Validation", "default")
    assert client.patch(f"/api/chats/{chat['id']}/read-state", json=payload).status_code == 422


def test_acknowledgements_are_scoped_to_assistant_replies_in_one_chat(client, db):
    chat = db.create_chat("First", "default")
    other = db.create_chat("Second", "default")
    own_user = db.add_message(chat["id"], "user", "hello")
    other_reply = db.add_message(other["id"], "assistant", "private")
    for message in (own_user, other_reply):
        response = client.patch(
            f"/api/chats/{chat['id']}/read-state",
            json={
                "message_id": message["id"],
                "revision": 0,
            },
        )
        assert response.status_code == 422
    assert client.patch("/api/chats/missing/read-state", json={"unread": True}).status_code == 404


def test_read_state_survives_database_reopen(tmp_path):
    path = str(tmp_path / "read-state.db")
    db = Database(path)
    chat = db.create_chat("Persistent", "default")
    reply = db.add_message(chat["id"], "assistant", "hello")
    db.set_chat_read_state(chat["id"], message_id=reply["id"], revision=0)
    db._conn.close()
    db = Database(path)
    assert db.get_chat(chat["id"])["unread"] is False
    db.set_chat_read_state(chat["id"], unread=True)
    db._conn.close()
    db = Database(path)
    assert db.list_chats()[0]["unread"] is True
    db._conn.close()
