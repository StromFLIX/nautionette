import asyncio
import base64
import io
import json

import pytest
from nautionette_backend import background, chat_images, conversations, runtime
from nautionette_backend.agent.runner import build_history
from nautionette_backend.integrations.discovery import image_support
from PIL import Image

from .test_chat_queue import chat_client, finish_background


def picture(fmt="PNG"):
    out = io.BytesIO()
    Image.new("RGB", (4, 3), "red").save(out, format=fmt)
    return out.getvalue()


@pytest.mark.parametrize("fmt,mime", list(chat_images.MIME_TYPES.items()))
def test_upload_and_authenticated_retrieval(client, anonymous, backend, fmt, mime):
    chat_id = client.post("/api/chats", json={}).json()["id"]
    data = picture(fmt)
    path = f"/api/chats/{chat_id}/images"
    response = client.post(path + "?name=../../screen.png", content=data, headers={"Content-Type": mime})
    assert response.status_code == 200
    image = response.json()
    assert image["name"] == "screen.png" and image["size"] == len(data)
    assert "data" not in image
    url = path + "/" + image["id"]
    saved = client.get(url)
    assert saved.content == data and saved.headers["content-type"] == mime
    assert saved.headers["cache-control"] == "private, no-store"
    assert anonymous.get(url).status_code == 401
    assert anonymous.post(path, content=data, headers={"Content-Type": mime}).status_code == 401
    other = client.post("/api/chats", json={}).json()["id"]
    assert client.get(f"/api/chats/{other}/images/{image['id']}").status_code == 404
    client.delete(f"/api/chats/{chat_id}")
    assert client.get(url).status_code == 404


@pytest.mark.parametrize(
    "data,mime,status",
    [
        (b"<svg><script>alert(1)</script></svg>", "image/svg+xml", 415),
        (b"not an image", "image/png", 422),
        (picture(), "image/jpeg", 422),
        (picture()[:24], "image/png", 422),
        (picture("JPEG")[:-10], "image/jpeg", 422),
        (b"", "image/png", 413),
        (b"x" * (chat_images.MAX_IMAGE_BYTES + 1), "image/png", 413),
    ],
)
def test_upload_validation(client, backend, data, mime, status):
    chat_id = client.post("/api/chats", json={}).json()["id"]
    response = client.post(f"/api/chats/{chat_id}/images", content=data, headers={"Content-Type": mime})
    assert response.status_code == status
    assert not backend.db.query("SELECT id FROM chat_images")


def test_pixel_limit(client, monkeypatch):
    monkeypatch.setattr(chat_images, "MAX_PIXELS", 10)
    chat_id = client.post("/api/chats", json={}).json()["id"]
    assert (
        client.post(
            f"/api/chats/{chat_id}/images", content=picture(), headers={"Content-Type": "image/png"}
        ).status_code
        == 422
    )


async def test_image_only_message_retry_history_and_deletion(backend):
    async with chat_client() as client:
        chat_id = (await client.post("/api/chats", json={"title": "Images"})).json()["id"]
        path = f"/api/chats/{chat_id}"
        image = (
            await client.post(path + "/images", content=picture(), headers={"Content-Type": "image/png"})
        ).json()
        payload = {"text": "", "message_id": "image-turn", "attachment_ids": [image["id"]], "queue": True}
        sent = await client.post(path + "/messages", json=payload)
        assert sent.status_code == 202
        await finish_background()
        assert (await client.post(path + "/messages", json=payload)).json() == sent.json()
        assert len(backend.broker.jobs) == 1
        job = backend.broker.jobs[0]
        assert job["images"] == [
            {"type": "image", "mimeType": "image/png", "data": base64.b64encode(picture()).decode()}
        ]
        assert not json.loads(
            backend.db.one("SELECT job FROM chat_turns WHERE id = 'image-turn'")["job"]
        ).get("images")
        snapshot = (await client.get(path)).json()
        assert snapshot["messages"][0]["meta"]["attachments"] == [image]
        assert "base64" not in json.dumps(snapshot)
        changed = {**payload, "attachment_ids": [], "text": "changed"}
        assert (await client.post(path + "/messages", json=changed)).status_code == 422
        # Attached images cannot be removed through the draft-upload endpoint.
        await client.delete(path + "/images/" + image["id"])
        assert (await client.get(path + "/images/" + image["id"])).status_code == 200
        await client.post(path + "/messages", json={"text": "What color was it?", "message_id": "follow-up"})
        await finish_background()
        assert backend.broker.jobs[1]["history"][0]["images"] == job["images"]
        await client.delete(path)
        assert not backend.db.query("SELECT id FROM chat_images")


@pytest.mark.parametrize("ids", [["missing"], ["x"] * 5, ["x", "x"], "x", [123]])
def test_bad_attachment_references_never_accept_a_message(client, backend, ids):
    chat_id = client.post("/api/chats", json={"title": "Images"}).json()["id"]
    response = client.post(f"/api/chats/{chat_id}/messages", json={"text": "test", "attachment_ids": ids})
    assert response.status_code == 422
    assert backend.db.list_messages(chat_id) == []


def test_cross_chat_attachment_cannot_be_used(client, backend):
    first = client.post("/api/chats", json={}).json()["id"]
    second = client.post("/api/chats", json={}).json()["id"]
    image = chat_images.store_image(first, picture(), "image/png", "image")
    result = client.post(f"/api/chats/{second}/messages", json={"attachment_ids": [image["id"]]})
    assert result.status_code == 422
    assert backend.db.list_messages(second) == []


def test_text_only_model_rejects_images_without_losing_upload(client, backend):
    runtime.cache_catalog({"models": [{"id": "text-only", "supports_images": False}]})
    chat_id = client.post("/api/chats", json={"model": "text-only"}).json()["id"]
    image = chat_images.store_image(chat_id, picture(), "image/png", "image")
    result = client.post(f"/api/chats/{chat_id}/messages", json={"attachment_ids": [image["id"]]})
    assert result.status_code == 422 and "text-only" in result.text
    assert (
        backend.db.one("SELECT message_id FROM chat_images WHERE id = ?", (image["id"],))["message_id"]
        is None
    )


async def test_queued_image_waits_for_own_turn_and_survives_history(backend, monkeypatch):
    started, finish = asyncio.Event(), asyncio.Event()
    jobs, commands = [], []

    async def agent(job):
        jobs.append(job.copy())
        if len(jobs) == 1:
            started.set()
            await finish.wait()
        yield {"type": "delta", "text": "Answered"}
        yield {"type": "result", "ok": True}

    async def control(*args):
        commands.append(args)
        return True

    monkeypatch.setattr(conversations, "stream_agent", agent)
    monkeypatch.setattr(conversations.broker, "control_agent", control, raising=False)
    try:
        async with chat_client() as client:
            chat_id = (await client.post("/api/chats", json={"title": "Images"})).json()["id"]
            path = f"/api/chats/{chat_id}"
            await client.post(path + "/messages", json={"text": "Start", "message_id": "first"})
            await asyncio.wait_for(started.wait(), 2)
            image = chat_images.store_image(chat_id, picture(), "image/png", "queued.png")
            response = await client.post(
                path + "/messages",
                json={"message_id": "second", "queue": True, "attachment_ids": [image["id"]]},
            )
            assert response.json()["message"]["meta"]["queued"]
            await asyncio.sleep(0.3)
            assert commands == []
            finish.set()
            await finish_background()
            assert len(jobs) == 2 and jobs[1]["images"]
            assert jobs[1]["history"][-1]["content"] == "Answered"
    finally:
        finish.set()
        await background.drain()


async def test_switch_to_text_only_model_omits_history_images(backend):
    async with chat_client() as client:
        chat_id = (await client.post("/api/chats", json={"title": "Images"})).json()["id"]
        image = chat_images.store_image(chat_id, picture(), "image/png", "image")
        await client.post(f"/api/chats/{chat_id}/messages", json={"attachment_ids": [image["id"]]})
        await finish_background()
        runtime.cache_catalog({"models": [{"id": "text-only", "supports_images": False}]})
        await client.patch(f"/api/chats/{chat_id}", json={"model": "text-only"})
        response = await client.post(f"/api/chats/{chat_id}/messages", json={"text": "Continue with text"})
        assert response.status_code == 202
        await finish_background()
        history = backend.broker.jobs[-1]["history"]
        assert "omitted for this text-only model" in history[0]["content"]
        assert not any(message.get("images") for message in history)


def test_draft_images_can_be_discarded_and_queued_deletion_removes_images(client, backend):
    chat_id = client.post("/api/chats", json={"title": "Images"}).json()["id"]
    image = chat_images.store_image(chat_id, picture(), "image/png", "image")
    path = f"/api/chats/{chat_id}/images/{image['id']}"
    assert client.delete(path).status_code == 200
    assert client.get(path).status_code == 404
    image = chat_images.store_image(chat_id, picture(), "image/png", "image")
    backend.db.execute("UPDATE chats SET queue_paused = 1 WHERE id = ?", (chat_id,))
    response = client.post(
        f"/api/chats/{chat_id}/messages",
        json={"attachment_ids": [image["id"]], "queue": True, "message_id": "queued-image"},
    )
    assert response.status_code == 202
    assert client.delete(f"/api/chats/{chat_id}/queue/queued-image").status_code == 200
    assert not backend.db.query("SELECT id FROM chat_images")


def test_history_retains_only_four_recent_images_and_charges_context():
    messages = [
        {"role": "user", "content": str(i), "meta": {"attachments": [{"id": str(i)}]}} for i in range(8)
    ]
    history = build_history(messages)
    assert [m["attachments"][0]["id"] for m in history if m.get("attachments")] == ["4", "5", "6", "7"]
    assert "omitted" in history[0]["content"]
    assert build_history(messages, max_chars=8000) == []


@pytest.mark.parametrize(
    "item,expected",
    [
        ({"architecture": {"input_modalities": ["text", "image"]}}, True),
        ({"architecture": {"input_modalities": ["text"]}}, False),
        ({"capabilities": {"supports": {"vision": True}}}, True),
        ({"capabilities": {"supports": {"vision": False}}}, False),
        ({}, None),
    ],
)
def test_image_capabilities_are_not_guessed(item, expected):
    assert image_support(item) is expected
