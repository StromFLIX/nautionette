import base64
import json
import time

import pytest
from nautionette_backend import chat_attachments, runtime
from nautionette_backend.agent.runner import build_history

from .test_chat_images import picture
from .test_chat_queue import chat_client, finish_background


@pytest.mark.parametrize(
    "mime,name,data",
    [
        ("application/pdf", "report.pdf", b"%PDF-1.7\noriginal scanned or encrypted PDF bytes\x00\xff"),
        ("text/csv", "report.csv", b"name,value\nhello,42\n"),
        ("application/zip", "archive.zip", b"PK\x03\x04\x00\xff"),
        ("text/html", "page.html", b"<script>alert(1)</script>"),
        ("image/svg+xml", "drawing.svg", b"<svg onload='alert(1)'/>"),
        ("", "unknown", b"\x00\xff\x01"),
    ],
)
def test_arbitrary_files_stay_unchanged_and_require_auth(client, anonymous, backend, mime, name, data):
    chat_id = client.post("/api/chats", json={}).json()["id"]
    path = f"/api/chats/{chat_id}/attachments"
    response = client.post(
        path, params={"name": "../folder/" + name}, content=data, headers={"Content-Type": mime}
    )
    assert response.status_code == 200
    attachment = response.json()
    assert attachment == {
        "id": attachment["id"],
        "name": name,
        "mime_type": mime or "application/octet-stream",
        "size": len(data),
    }
    url = f"{path}/{attachment['id']}"
    saved = client.get(url)
    assert saved.content == data
    assert saved.headers["content-disposition"].startswith("attachment;")
    assert saved.headers["cache-control"] == "private, no-store"
    assert saved.headers["x-content-type-options"] == "nosniff"
    assert "sandbox" in saved.headers["content-security-policy"]
    assert anonymous.get(url).status_code == 401
    assert anonymous.post(path, content=data).status_code == 401
    assert anonymous.delete(url).status_code == 401
    other = client.post("/api/chats", json={}).json()["id"]
    assert client.get(f"/api/chats/{other}/attachments/{attachment['id']}").status_code == 404
    assert (
        client.post(f"/api/chats/{other}/messages", json={"attachment_ids": [attachment["id"]]}).status_code
        == 422
    )
    assert client.delete(url).status_code == 200
    assert client.get(url).status_code == 404


@pytest.mark.parametrize("data", [b"", b"x" * (chat_attachments.MAX_FILE_BYTES + 1)])
def test_file_size_limits(client, backend, data):
    chat_id = client.post("/api/chats", json={}).json()["id"]
    assert (
        client.post(
            f"/api/chats/{chat_id}/attachments", content=data, headers={"Content-Type": "application/pdf"}
        ).status_code
        == 413
    )
    assert not backend.db.query("SELECT id FROM chat_images")


async def test_file_only_message_text_model_retry_history_and_cleanup(backend):
    runtime.cache_catalog({"models": [{"id": "text-only", "supports_images": False}]})
    data = b"%PDF-1.7\nno extraction, even encrypted or scanned\x00\xff"
    async with chat_client() as client:
        chat_id = (await client.post("/api/chats", json={"title": "Files", "model": "text-only"})).json()[
            "id"
        ]
        path = f"/api/chats/{chat_id}"
        attachment = (
            await client.post(
                path + "/attachments?name=report.pdf",
                content=data,
                headers={"Content-Type": "application/pdf"},
            )
        ).json()
        payload = {"message_id": "file-turn", "text": "", "attachment_ids": [attachment["id"]], "queue": True}
        response = await client.post(path + "/messages", json=payload)
        assert response.status_code == 202
        await finish_background()
        assert (await client.post(path + "/messages", json=payload)).json() == response.json()
        assert len(backend.broker.jobs) == 1
        job = backend.broker.jobs[0]
        assert not job["images"]
        assert base64.b64decode(job["files"][0]["data"]) == data
        assert job["files"][0]["name"] == "report.pdf"
        persisted = json.loads(backend.db.one("SELECT job FROM chat_turns WHERE id = 'file-turn'")["job"])
        assert "files" not in persisted and "images" not in persisted
        snapshot = (await client.get(path)).json()
        assert snapshot["messages"][0]["meta"]["attachments"] == [attachment]
        assert job["files"][0]["data"] not in json.dumps(snapshot)
        assert (
            await client.post(path + "/messages", json={**payload, "attachment_ids": []})
        ).status_code == 400
        await client.delete(path + "/attachments/" + attachment["id"])
        assert (await client.get(path + "/attachments/" + attachment["id"])).content == data
        await client.post(path + "/messages", json={"text": "Look at that file again"})
        await finish_background()
        assert backend.broker.jobs[-1]["history"][0]["files"] == job["files"]
        await client.delete(path)
        assert not backend.db.query("SELECT id FROM chat_images")


async def test_mixed_attachments_switch_models_without_losing_files(backend):
    async with chat_client() as client:
        chat_id = (await client.post("/api/chats", json={"title": "Mixed"})).json()["id"]
        path = f"/api/chats/{chat_id}"
        image = chat_attachments.store_attachment(chat_id, picture(), "image/png", "image.png")
        pdf = chat_attachments.store_attachment(chat_id, b"original PDF", "application/pdf", "file.pdf")
        response = await client.post(path + "/messages", json={"attachment_ids": [image["id"], pdf["id"]]})
        assert response.status_code == 202
        await finish_background()
        assert backend.broker.jobs[-1]["images"] and backend.broker.jobs[-1]["files"]
        runtime.cache_catalog({"models": [{"id": "text-only", "supports_images": False}]})
        await client.patch(path, json={"model": "text-only"})
        await client.post(path + "/messages", json={"text": "Revisit the PDF"})
        await finish_background()
        history = backend.broker.jobs[-1]["history"][0]
        assert not history["images"] and len(history["files"]) == 1
        assert history["files"][0]["id"] == pdf["id"]
        assert "image input unavailable" in history["content"]


def test_unsent_file_expiration_quota_and_safe_names(client, backend):
    chat_id = client.post("/api/chats", json={}).json()["id"]
    stored = chat_attachments.store_attachment(chat_id, b"original", "application/pdf", "..\\unsafe\n.pdf")
    assert stored["name"] == "unsafe.pdf"
    backend.db.execute("UPDATE chat_images SET created_at = ?", (time.time() - 86401,))
    for _ in range(20):
        chat_attachments.store_attachment(chat_id, b"file", "application/octet-stream", "file")
    assert not backend.db.one("SELECT id FROM chat_images WHERE id = ?", (stored["id"],))
    assert client.post(f"/api/chats/{chat_id}/attachments", content=b"file").status_code == 409


def test_file_history_is_bounded_and_charged_for_references_not_bytes():
    messages = [
        {
            "role": "user",
            "content": str(i),
            "meta": {
                "attachments": [
                    {"id": str(i), "mime_type": "application/pdf", "size": chat_attachments.MAX_FILE_BYTES}
                ]
            },
        }
        for i in range(8)
    ]
    history = build_history(messages, max_chars=3000)
    assert [m["attachments"][0]["id"] for m in history if m.get("attachments")] == ["4", "5", "6", "7"]
    assert "omitted" in history[0]["content"]
    assert build_history(messages, max_chars=512) == []
