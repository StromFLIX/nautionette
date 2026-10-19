"""A published output is durable, scoped, atomic, and contains no sandbox paths."""

from __future__ import annotations

import asyncio
import base64
import io
import json
import sqlite3
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import httpx
import pytest
from fastapi import HTTPException
from nautionette_backend import chat_outputs, main
from nautionette_backend.db import Database
from nautionette_backend.events import bus
from PIL import Image


@pytest.fixture
def output_scope(db):
    chat = db.create_chat("Outputs", "default")
    message, _ = db.accept_chat_message(chat["id"], "Create a drawing", "output-turn")
    with chat_outputs.publication_grant(chat["id"], message["id"]) as grant:
        yield chat["id"], grant


def raster():
    buffer = io.BytesIO()
    Image.new("RGB", (32, 24), "red").save(buffer, format="PNG")
    return buffer.getvalue()


def attachment(name="drawing.pdf", data=b"%PDF-test", mime="application/pdf", role="file"):
    return {"name": name, "mime_type": mime, "role": role, "data": base64.b64encode(data).decode()}


def payload(grant, **overrides):
    return {
        **grant,
        "request_id": "publish-1",
        "title": "Mounting bracket",
        "summary": "Dimensioned drawing and editable source.",
        "source_app": "CADCraft",
        "files": [
            attachment(),
            attachment("preview.png", raster(), "image/png", "preview"),
            attachment("bracket.dxf", b"source-document", "image/vnd.dxf", "source"),
        ],
        **overrides,
    }


def test_published_files_and_previews_are_durable_and_bytes_never_enter_snapshots(client, db, output_scope):
    chat_id, grant = output_scope
    response = client.post("/api/chat-outputs/publish", json=payload(grant))
    assert response.status_code == 200, response.text
    output = response.json()
    assert output["revision"] == 1 and len(output["files"]) == 3
    snapshot = client.get(f"/api/chats/{chat_id}").json()
    assert snapshot["outputs"] == [output]
    encoded = json.dumps(snapshot)
    assert "source-document" not in encoded and "data" not in output["files"][0]
    assert grant["grant"] not in encoded and "path" not in output["files"][0]
    assert client.get(f"/api/chats/{chat_id}/outputs").json() == {"outputs": [output]}
    db.finish_chat_turn("output-turn", "Done", {"steps": [{"kind": "tool", "id": "publish-1"}]})
    # The saved answer links the immutable revision; no live worker is needed.
    assert db.list_messages(chat_id)[-1]["meta"]["outputs"] == [output]
    for item in output["files"]:
        url = f"/api/chats/{chat_id}/outputs/{output['id']}/revisions/1/files/{item['id']}"
        download = client.get(url)
        assert download.status_code == 200
        assert download.headers["content-type"] == "application/octet-stream"
        assert download.headers["content-disposition"].startswith("attachment;")
        assert download.headers["x-content-type-options"] == "nosniff"
        assert download.headers["cache-control"] == "private, no-store"
        assert "sandbox" in download.headers["content-security-policy"]
        preview = client.get(url + "?preview=true")
        if item["role"] == "preview":
            assert preview.status_code == 200 and preview.headers["content-type"] == "image/png"
            Image.open(io.BytesIO(preview.content)).verify()
        else:
            assert preview.status_code == 404
    assert client.get(url).content == b"source-document"


def test_old_revisions_keep_their_exact_files_and_publishing_is_idempotent(client, db, output_scope):
    chat_id, grant = output_scope
    first = chat_outputs.publish(payload(grant))
    repeated = chat_outputs.publish(payload(grant))
    assert repeated == first
    update = payload(
        grant, request_id="publish-2", output_id=first["id"], expected_revision=1, title="Revised bracket"
    )
    update["files"][0] = attachment(data=b"%PDF-revision2")
    second = chat_outputs.publish(update)
    assert second["revision"] == 2
    assert chat_outputs.publish(update) == second
    assert db.list_chat_outputs(chat_id) == [second]
    history = client.get(f"/api/chats/{chat_id}/outputs/{first['id']}/revisions").json()["revisions"]
    assert history == [second, first]
    assert chat_outputs.file(chat_id, first["id"], 1, first["files"][0]["id"])["data"] == b"%PDF-test"
    assert chat_outputs.file(chat_id, second["id"], 2, second["files"][0]["id"])["data"] == b"%PDF-revision2"
    with pytest.raises(HTTPException) as conflict:
        chat_outputs.publish({**update, "request_id": "publish-3"})
    assert conflict.value.status_code == 409
    with pytest.raises(HTTPException) as conflict:
        chat_outputs.publish(payload(grant, title="Changed existing request"))
    assert conflict.value.status_code == 409
    assert len(db.query("SELECT id FROM chat_output_files")) == 6


def test_answer_segments_link_only_the_revisions_they_published(db, output_scope):
    chat_id, grant = output_scope
    first = chat_outputs.publish(payload(grant))
    db.accept_chat_message(chat_id, "Enlarge the hole", "steering-input", queue=True)
    assert db.consume_chat_input(
        "output-turn", "steering-input", "First result", {"steps": [{"kind": "tool", "id": "publish-1"}]}
    )
    second = chat_outputs.publish(
        payload(grant, request_id="publish-2", output_id=first["id"], expected_revision=1)
    )
    db.finish_chat_turn("output-turn", "Revised result", {"steps": [{"kind": "tool", "id": "publish-2"}]})
    answers = [message for message in db.list_messages(chat_id) if message["role"] == "assistant"]
    assert [message["meta"]["outputs"] for message in answers] == [[first], [second]]


def test_validation_is_atomic_and_never_returns_capability_or_bytes(client, db, output_scope):
    _, grant = output_scope
    body = payload(grant)
    body["files"].append(attachment("invalid.png", b"not-an-image", "image/png"))
    response = client.post("/api/chat-outputs/publish", json=body)
    assert response.status_code == 422
    assert db.query("SELECT * FROM chat_outputs") == []
    assert db.query("SELECT * FROM chat_output_files") == []
    body["files"][0]["unexpected"] = "sensitive-content"
    response = client.post("/api/chat-outputs/publish", json=body)
    assert response.status_code == 422
    assert grant["grant"] not in response.text and "sensitive-content" not in response.text


@pytest.mark.parametrize(
    "files",
    [
        [],
        [attachment(data=b"")],
        [attachment(".")],
        [attachment("..")],
        [attachment("preview.svg", b"<svg onload='alert(1)'/>", "image/svg+xml", "preview")],
        [{**attachment(), "data": "YR=="}],
        [{**attachment(), "data": "%%%%"}],
        [attachment()] * 9,
    ],
)
def test_bad_publications_are_rejected(client, output_scope, files):
    _, grant = output_scope
    assert client.post("/api/chat-outputs/publish", json=payload(grant, files=files)).status_code >= 400


def test_stop_during_validation_is_checked_again_before_commit(client, db, output_scope, monkeypatch):
    chat_id, grant = output_scope
    prepare = chat_outputs._prepare_file

    def stopped(item):
        file = prepare(item)
        db.stop_chat_turn(chat_id, "output-turn")
        return file

    monkeypatch.setattr(chat_outputs, "_prepare_file", stopped)
    assert client.post("/api/chat-outputs/publish", json=payload(grant)).status_code == 409
    assert db.query("SELECT * FROM chat_outputs") == []
    assert db.query("SELECT * FROM chat_output_files") == []


def test_commit_failure_rolls_back_metadata_files_and_idempotency_record(db, output_scope):
    _, grant = output_scope
    db.execute(
        "CREATE TEMP TRIGGER fail_output_file BEFORE INSERT ON chat_output_files "
        "BEGIN SELECT RAISE(ABORT, 'injected storage failure'); END"
    )
    try:
        with pytest.raises(sqlite3.IntegrityError, match="injected storage failure"):
            chat_outputs.publish(payload(grant))
        for table in ("chat_outputs", "chat_output_revisions", "chat_output_files"):
            assert db.query(f"SELECT * FROM {table}") == []  # noqa: S608 - fixed test table names
    finally:
        db.execute("DROP TRIGGER fail_output_file")
    assert chat_outputs.publish(payload(grant))["revision"] == 1


def test_active_content_is_download_only_and_filenames_are_normalized(client, output_scope):
    chat_id, grant = output_scope
    active = b"<svg onload='alert(1)'/>"
    output = chat_outputs.publish(
        payload(grant, files=[attachment("../../report.svg", active, "image/svg+xml")])
    )
    item = output["files"][0]
    assert item["name"] == "report.svg" and not item["previewable"]
    url = f"/api/chats/{chat_id}/outputs/{output['id']}/revisions/1/files/{item['id']}"
    response = client.get(url)
    assert response.content == active
    assert response.headers["content-type"] == "application/octet-stream"
    assert client.get(url + "?preview=true").status_code == 404


def test_authentication_and_chat_revision_scope_cannot_be_bypassed(client, anonymous, db, output_scope):
    chat_id, grant = output_scope
    output = chat_outputs.publish(payload(grant))
    other = db.create_chat("Other", "default")["id"]
    db.accept_chat_message(other, "Other task", "other-turn")
    item = output["files"][0]
    suffix = f"/outputs/{output['id']}/revisions/1/files/{item['id']}"
    assert anonymous.get(f"/api/chats/{chat_id}{suffix}").status_code == 401
    assert anonymous.post("/api/chat-outputs/publish", json=payload(grant)).status_code == 401
    assert client.get(f"/api/chats/{other}{suffix}").status_code == 404
    wrong_revision = suffix.replace("/revisions/1/", "/revisions/2/")
    assert client.get(f"/api/chats/{chat_id}{wrong_revision}").status_code == 404
    with chat_outputs.publication_grant(other, "other-turn") as other_grant:
        assert (
            client.post("/api/chat-outputs/read", json={**other_grant, "output_id": output["id"]}).status_code
            == 404
        )
        update = payload(other_grant, output_id=output["id"], expected_revision=1)
        assert client.post("/api/chat-outputs/publish", json=update).status_code == 404
    assert client.post("/api/chat-outputs/publish", json=payload({"grant": "expired"})).status_code == 401
    # Chat IDs are not accepted as authority, even with a valid grant.
    assert client.post("/api/chat-outputs/publish", json=payload(grant, chat_id=other)).status_code == 422


@pytest.mark.parametrize("state", ["stop", "complete", "revoke", "delete", "queued"])
def test_late_calls_are_fenced_but_committed_outputs_are_preserved(client, db, output_scope, state):
    chat_id, grant = output_scope
    chat_outputs.publish(payload(grant))
    if state == "stop":
        db.stop_chat_turn(chat_id, "output-turn")
    elif state == "complete":
        db.finish_chat_turn("output-turn", "Done", {})
    elif state == "revoke":
        with chat_outputs.publication_grant(chat_id, "output-turn") as revoked:
            pass
        grant = revoked
    elif state == "queued":
        db.execute("UPDATE chat_turns SET state = 'queued' WHERE id = 'output-turn'")
    else:
        client.delete(f"/api/chats/{chat_id}")
    assert client.post("/api/chat-outputs/publish", json=payload(grant, request_id="late")).status_code in {
        401,
        409,
    }
    assert client.post("/api/chat-outputs/read", json=grant).status_code in {401, 409}
    assert len(db.list_chat_outputs(chat_id)) == (0 if state == "delete" else 1)
    if state == "delete":
        assert db.query("SELECT * FROM chat_output_revisions") == []
        assert db.query("SELECT * FROM chat_output_files") == []


def test_read_output_restores_original_bytes_for_a_later_turn(client, db, output_scope):
    chat_id, grant = output_scope
    output = chat_outputs.publish(payload(grant))
    db.finish_chat_turn("output-turn", "Done", {})
    db.accept_chat_message(chat_id, "Enlarge the hole", "later-turn")
    with chat_outputs.publication_grant(chat_id, "later-turn") as later:
        assert client.post("/api/chat-outputs/read", json=later).json() == {"outputs": [output]}
        inspected = client.post("/api/chat-outputs/read", json={**later, "output_id": output["id"]}).json()
        assert inspected == {"output": output, "revisions": [1]}
        response = client.post(
            "/api/chat-outputs/read",
            json={**later, "output_id": output["id"], "file_id": output["files"][2]["id"]},
        )
        assert response.status_code == 200
        assert base64.b64decode(response.json()["file"]["data"]) == b"source-document"


def test_quotas_and_request_bytes_are_bounded(client, db, output_scope, monkeypatch):
    _, grant = output_scope
    db.set_setting("output_storage_policy", {"chat_limit_bytes": 1})
    assert client.post("/api/chat-outputs/publish", json=payload(grant)).status_code == 413
    assert db.query("SELECT * FROM chat_outputs") == []
    monkeypatch.setattr(chat_outputs, "MAX_REQUEST_BYTES", 8)
    assert client.post("/api/chat-outputs/publish", content=b" " * 9).status_code == 413


async def test_publication_notifies_on_event_loop_after_commit(client, db, output_scope, monkeypatch):
    chat_id, grant = output_scope
    owning_thread = threading.get_ident()
    events = []

    def published(kind, metadata):
        # asyncio.Queue subscribers must be notified on their own event loop,
        # never from the raster-validation/database worker thread.
        assert threading.get_ident() == owning_thread
        assert db.list_chat_outputs(chat_id)[0]["id"] == metadata["output_id"]
        events.append((kind, metadata))

    monkeypatch.setattr(bus, "publish", published)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=main.app), base_url="http://test", headers=client.headers
    ) as http:
        response = await http.post("/api/chat-outputs/publish", json=payload(grant))
    assert response.status_code == 200
    output = response.json()
    assert events == [
        ("chat.output.published", {"chat_id": chat_id, "output_id": output["id"], "revision": 1})
    ]


async def test_http_publications_have_a_separate_bounded_admission_queue(client, output_scope, monkeypatch):
    _, grant = output_scope
    lock = threading.Lock()
    active = peak = 0

    def publish(_payload):
        nonlocal active, peak
        with lock:
            active += 1
            peak = max(active, peak)
        time.sleep(0.1)
        with lock:
            active -= 1
        return {"id": "test-output", "revision": 1}

    monkeypatch.setattr(chat_outputs, "publish", publish)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=main.app), base_url="http://test", headers=client.headers
    ) as http:
        responses = await asyncio.gather(
            *(http.post("/api/chat-outputs/publish", json=payload(grant)) for _ in range(6))
        )
    assert all(response.status_code == 200 for response in responses)
    assert peak == 2 and active == 0


def test_concurrent_updates_commit_only_one_revision(db, output_scope):
    _, grant = output_scope
    first = chat_outputs.publish(payload(grant))

    def update(number):
        try:
            return chat_outputs.publish(
                payload(grant, request_id=f"parallel-{number}", output_id=first["id"], expected_revision=1)
            )
        except HTTPException as exc:
            return exc.status_code

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(update, [1, 2]))
    assert len([item for item in results if item == 409]) == 1
    assert len(db.query("SELECT * FROM chat_output_revisions")) == 2


def test_private_transports_and_reserved_local_names_are_not_selectable_mcp_tools(client, backend):
    backend.gateway.tools = {
        "": [{"name": name} for name in [*chat_outputs.RESERVED_TOOL_NAMES, "backend_system_status"]]
    }
    available = client.get("/api/catalog").json()["tools"]
    assert [tool["name"] for tool in available] == ["backend_system_status"]


async def test_mcp_exposes_only_the_two_scoped_transports(backend, output_scope):
    chat_id, grant = output_scope
    names = {tool.name for tool in (await main.backend_mcp.list_tools(None, None)).tools}
    assert {"prepare_chat_output", "publish_chat_output", "read_chat_output"} <= names
    assert not {"list_chat_outputs", "get_output_file", "list_output_revisions"} & names
    result = await main.backend_mcp.call("publish_chat_output", {"body": payload(grant)})
    assert not result.is_error
    output = json.loads(result.content[0].text)
    assert backend.db.list_chat_outputs(chat_id) == [output]
    denied = await main.backend_mcp.call("read_chat_output", {"body": {"grant": "expired"}})
    assert denied.is_error
    invalid = "private-capability-that-must-not-be-echoed" * 5
    denied = await main.backend_mcp.call("read_chat_output", {"body": {"grant": invalid}})
    assert denied.is_error and invalid not in denied.content[0].text


async def test_stream_grant_exists_only_while_broker_is_active(backend, live, monkeypatch):
    from nautionette_backend.agent.runner import stream_agent

    chat = backend.db.create_chat("Grant lifetime", "default")
    backend.db.accept_chat_message(chat["id"], "Generate", "grant-turn")
    seen = []

    async def run(job, timeout):
        token = job["output_publication"]["grant"]
        seen.append(token)
        assert chat_outputs.grant_scope(token) == (chat["id"], "grant-turn")
        yield {"type": "delta", "text": "working"}
        yield {"type": "result", "ok": True}

    monkeypatch.setattr(live.broker, "run_agent", run)
    job = {"mode": "interactive", "chat_id": chat["id"], "turn_id": "grant-turn", "prompt": "Generate"}
    stream = stream_agent(job)
    assert (await anext(stream))["type"] == "delta"
    assert "output_publication" not in job
    await stream.aclose()  # Early shutdown/cancellation revokes it too.
    with pytest.raises(HTTPException) as error:
        chat_outputs.grant_scope(seen[0])
    assert error.value.status_code == 401


def test_database_reopen_retains_manifests_and_file_bytes(tmp_path, monkeypatch):
    path = str(tmp_path / "outputs.db")
    database = Database(path)
    monkeypatch.setattr(chat_outputs, "db", database)
    chat = database.create_chat("Persistent", "default")
    database.accept_chat_message(chat["id"], "Create", "persist-turn")
    with chat_outputs.publication_grant(chat["id"], "persist-turn") as grant:
        output = chat_outputs.publish(payload(grant))
    database._conn.close()
    reopened = Database(path)
    monkeypatch.setattr(chat_outputs, "db", reopened)
    assert reopened.chat_snapshot(chat["id"])["outputs"] == [output]
    assert chat_outputs.file(chat["id"], output["id"], 1, output["files"][0]["id"])["data"] == b"%PDF-test"
    reopened._conn.close()
