"""Drafts survive navigation/devices without becoming messages or losing files."""

import pytest


def test_draft_roundtrip_conflicts_and_idempotent_clear(client, db):
    chat = db.create_chat("Draft", "default")
    path = f"/api/chats/{chat['id']}/draft"
    assert client.get(path).json() == {"revision": 0, "mutation_id": "", "text": "", "attachments": []}
    payload = {"revision": 0, "mutation_id": "first", "text": "  long\nunsent message  "}
    saved = client.put(path, json=payload).json()
    assert saved["revision"] == 1
    assert client.put(path, json=payload).json() == saved
    assert client.get(path).json()["text"] == payload["text"]
    assert client.get(f"/api/chats/{chat['id']}").json()["messages"] == []
    assert db.get_chat(chat["id"])["updated_at"] == chat["updated_at"]
    assert client.put(path, json={**payload, "mutation_id": "other"}).status_code == 409
    clear = {"revision": 1, "mutation_id": "clear", "text": ""}
    assert client.put(path, json=clear).json()["revision"] == 2
    assert client.put(path, json=clear).json()["revision"] == 2
    assert client.put(path, json={**payload, "mutation_id": "late"}).status_code == 409
    assert client.get(path).json()["text"] == ""
    db.delete_chat(chat["id"])
    assert db.one("SELECT * FROM chat_drafts WHERE chat_id = ?", (chat["id"],)) is None
    assert client.get(path).status_code == 404


def test_draft_attachments_survive_expiration_and_discard(client, db):
    chat = db.create_chat("Files", "default")
    root = f"/api/chats/{chat['id']}"
    upload = client.post(root + "/attachments?name=notes.txt", content=b"precious notes").json()
    saved = client.put(
        root + "/draft",
        json={
            "revision": 0,
            "mutation_id": "files",
            "text": "notes",
            "attachment_ids": [upload["id"]],
        },
    )
    assert saved.status_code == 200
    assert saved.json()["attachments"] == [upload]
    db.execute("UPDATE chat_images SET created_at = 0")
    client.post(root + "/attachments?name=another.txt", content=b"trigger cleanup")
    client.delete(root + f"/attachments/{upload['id']}")
    assert client.get(root + f"/attachments/{upload['id']}").content == b"precious notes"
    other = db.create_chat("Other", "default")
    invalid = client.put(
        f"/api/chats/{other['id']}/draft",
        json={
            "revision": 0,
            "mutation_id": "bad",
            "text": "",
            "attachment_ids": [upload["id"]],
        },
    )
    assert invalid.status_code == 422


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"revision": True, "mutation_id": "a", "text": ""},
        {"revision": -1, "mutation_id": "a", "text": ""},
        {"revision": 0, "mutation_id": "", "text": ""},
        {"revision": 0, "mutation_id": "a", "text": []},
        {"revision": 0, "mutation_id": "a", "text": "", "attachment_ids": ["missing"]},
    ],
)
def test_invalid_drafts(client, db, payload):
    chat = db.create_chat("Invalid", "default")
    assert client.put(f"/api/chats/{chat['id']}/draft", json=payload).status_code == 422
