"""Opt-in integration against the exact MaxIO release shipped in Compose.

CI provisions a disposable bucket; never point this at a real application's bucket.
No model, agentgateway or provider credentials are required.
"""

from __future__ import annotations

import hashlib
import os
from urllib.parse import urlsplit

import httpx
import pytest
from nautionette_backend import chat_outputs, output_maintenance, output_storage
from nautionette_backend.config import settings

from .test_output_storage import document, legacy

pytestmark = pytest.mark.skipif(
    not os.getenv("MAXIO_TEST_ENDPOINT"), reason="Disposable MaxIO is not configured"
)


@pytest.fixture
def maxio(monkeypatch, db, output_store):
    monkeypatch.setattr(settings, "output_s3_endpoint", os.environ["MAXIO_TEST_ENDPOINT"])
    monkeypatch.setattr(settings, "output_s3_access_key", os.environ["MAXIO_TEST_ACCESS_KEY"])
    monkeypatch.setattr(settings, "output_s3_secret_key", os.environ["MAXIO_TEST_SECRET_KEY"])
    monkeypatch.setattr(
        settings, "output_s3_bucket", os.getenv("MAXIO_TEST_BUCKET", "nautionette-test-outputs")
    )
    monkeypatch.setattr(settings, "output_s3_region", "us-east-1")
    assert settings.output_s3_bucket.startswith("nautionette-test-"), "A disposable test bucket is required"
    store = output_storage.S3Store()
    store.ready()
    monkeypatch.setattr(output_storage, "get_store", lambda: store)
    try:
        yield store
    finally:
        for row in db.query("SELECT key FROM output_storage_objects"):
            store.delete(row["key"])


def publish(client, db, grant, data, **changes):
    body = document(grant, data, **changes)
    response = client.post("/api/chat-outputs/prepare", json=body)
    assert response.status_code == 200, response.text
    assert len(response.content) < 8192
    plan = response.json()
    with httpx.Client(trust_env=False, timeout=60) as transfer:
        uploaded = transfer.put(plan["files"][0]["url"], headers=plan["files"][0]["headers"], content=data)
        assert uploaded.status_code == 200, uploaded.text
    response = client.post(
        "/api/chat-outputs/publish",
        json={**grant, "upload_id": plan["upload_id"], "request_id": body["request_id"]},
    )
    assert response.status_code == 200, response.text
    assert "X-Amz" not in response.text and "data" not in response.json()["files"][0]
    assert all(
        row["data"] is None and row["preview"] is None for row in db.query("SELECT * FROM chat_output_files")
    )
    return response.json()


def test_real_maxio_large_binary_publication_restore_authenticated_download_and_revision_cleanup(
    client, db, maxio
):
    output_storage.save_policy({"max_file_bytes": 32 * output_storage.MiB})
    chat = db.create_chat("Real MaxIO", "default")
    db.accept_chat_message(chat["id"], "Publish", "real-maxio-turn")
    data = b"CAD\x00\xff" * (5 * output_storage.MiB)  # 25 MiB: above the old per-file ceiling.
    with chat_outputs.publication_grant(chat["id"], "real-maxio-turn") as grant:
        output = publish(client, db, grant, data)
        file = output["files"][0]
        restored = client.post(
            "/api/chat-outputs/read",
            json={**grant, "binary": True, "output_id": output["id"], "file_id": file["id"]},
        ).json()
        with httpx.Client(trust_env=False, timeout=60) as transfer:
            response = transfer.get(restored["file"]["download_url"])
            assert response.status_code == 200 and response.content == data
            unsigned = urlsplit(restored["file"]["download_url"])._replace(query="").geturl()
            assert transfer.get(unsigned).status_code == 403
        route = f"/api/chats/{chat['id']}/outputs/{output['id']}/revisions/1/files/{file['id']}"
        response = client.get(route)
        assert response.content == data and response.headers["content-type"] == "application/octet-stream"
        assert "location" not in response.headers and response.headers["cache-control"] == "private, no-store"
        updated = publish(
            client,
            db,
            grant,
            b"Revised CAD\x00\xff",
            request_id="second",
            output_id=output["id"],
            expected_revision=1,
        )
        plan = output_maintenance.preview_cleanup({"revisions": [{"output_id": output["id"], "revision": 1}]})
        removed = output_maintenance.apply_cleanup(plan["plan_id"])
        assert not removed["collection"]["errors"]
        assert db.list_chat_outputs(chat["id"]) == [updated]
        assert client.get(route).status_code == 404


def test_real_maxio_signed_put_binds_headers_size_and_sha256(client, db, maxio):
    chat = db.create_chat("Checksum-bound upload", "default")
    db.accept_chat_message(chat["id"], "Publish", "checksum-maxio-turn")
    data = b"right-payload"
    with chat_outputs.publication_grant(chat["id"], "checksum-maxio-turn") as grant:
        prepared = client.post("/api/chat-outputs/prepare", json=document(grant, data)).json()
        target = prepared["files"][0]
        assert "X-Amz-SignedHeaders=content-length;content-type;host;x-amz-checksum-sha256" in target["url"]
        with httpx.Client(trust_env=False, timeout=10) as transfer:
            response = transfer.put(target["url"], headers=target["headers"], content=b"wrong-payload")
            assert response.status_code == 400, response.text
            assert "checksum" in response.text.lower()
            headers = {
                **target["headers"],
                "x-amz-checksum-sha256": "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=",
            }
            assert transfer.put(target["url"], headers=headers, content=data).status_code == 403
            assert transfer.put(target["url"], headers=target["headers"], content=data).status_code == 200
        committed = client.post(
            "/api/chat-outputs/publish",
            json={**grant, "upload_id": prepared["upload_id"], "request_id": "binary-publish"},
        )
        assert committed.status_code == 200
        assert committed.json()["files"][0]["sha256"] == hashlib.sha256(data).hexdigest()


def test_real_maxio_verified_legacy_migration_includes_sanitized_previews(client, db, maxio):
    stored, original, data, preview = legacy(db)
    output_maintenance.migrate_file(stored)
    migrated = db.one("SELECT * FROM chat_output_files")
    assert migrated["data"] is None and migrated["preview"] is None
    assert maxio.bytes(migrated["object_key"], len(data), hashlib.sha256(data).hexdigest()) == data
    assert maxio.bytes(migrated["preview_key"], len(preview), hashlib.sha256(preview).hexdigest()) == preview
    assert db.list_chat_outputs(db.one("SELECT id FROM chats")["id"]) == [original]
