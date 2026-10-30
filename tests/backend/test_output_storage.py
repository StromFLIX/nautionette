"""Storage policy, binary control protocol, verified migration and cleanup."""

from __future__ import annotations

import errno
import hashlib
import json
import sqlite3
import time
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi import HTTPException
from nautionette_backend import chat_outputs, main, output_maintenance, output_storage
from nautionette_backend.config import settings
from nautionette_backend.db import Database
from nautionette_backend.output_ids import publication_request_id
from nautionette_backend.search_index import load_extension

from .test_chat_outputs import payload, raster


@pytest.fixture
def scope(db):
    chat = db.create_chat("Storage test chat", "default")
    db.accept_chat_message(chat["id"], "Generate", "storage-turn")
    with chat_outputs.publication_grant(chat["id"], "storage-turn") as grant:
        yield chat["id"], grant


def document(grant, data=b"editable\x00\xff", **changes):
    return {
        **grant,
        "request_id": "binary-publish",
        "title": "Binary output",
        "files": [
            {
                "name": "source.dxf",
                "mime_type": "image/vnd.dxf",
                "role": "source",
                "size": len(data),
                "sha256": hashlib.sha256(data).hexdigest(),
            }
        ],
        **changes,
    }


def stage(client, db, store, grant, data=b"editable\x00\xff", **changes):
    body = document(grant, data, **changes)
    response = client.post("/api/chat-outputs/prepare", json=body)
    assert response.status_code == 200, response.text
    prepared = response.json()
    if "output" not in prepared:
        upload = db.one("SELECT payload FROM chat_output_uploads WHERE id = ?", (prepared["upload_id"],))
        for item in json.loads(upload["payload"])["files"]:
            store.put(item["object_key"], data, len(data))
    return prepared


def commit(client, grant, prepared, request_id="binary-publish"):
    return client.post(
        "/api/chat-outputs/publish",
        json={**grant, "upload_id": prepared["upload_id"], "request_id": request_id},
    )


def legacy(db):
    chat = db.create_chat("Legacy", "default")
    output_id, file_id = "a" * 32, "b" * 32
    data, preview = b"legacy-source\x00\xff", raster()
    manifest = {
        "id": output_id,
        "revision": 1,
        "title": "Legacy output",
        "turn_id": "legacy-turn",
        "created_at": 1,
        "files": [
            {
                "id": file_id,
                "name": "source.dxf",
                "size": len(data),
                "sha256": hashlib.sha256(data).hexdigest(),
                "previewable": True,
            }
        ],
    }
    db.execute(
        "INSERT INTO chat_outputs (id,chat_id,current_revision,created_at,updated_at,next_revision) "
        "VALUES (?,?,?,?,?,?)",
        (output_id, chat["id"], 1, 1, 1, 2),
    )
    db.execute(
        "INSERT INTO chat_output_revisions VALUES (?,?,?,?,?,?,?)",
        (output_id, 1, "legacy-turn", "legacy-request", "digest", json.dumps(manifest), 1),
    )
    db.execute(
        "INSERT INTO chat_output_files (id,output_id,revision,name,mime_type,role,size,data,preview) "
        "VALUES (?,?,?,?,?,?,?,?,?)",
        (file_id, output_id, 1, "source.dxf", "image/vnd.dxf", "source", len(data), data, preview),
    )
    return db.one("SELECT * FROM chat_output_files WHERE id = ?", (file_id,)), manifest, data, preview


def test_prepare_and_commit_are_small_metadata_only_and_never_write_blobs(client, db, output_store, scope):
    chat_id, grant = scope
    prepared = stage(client, db, output_store, grant)
    assert len(json.dumps(prepared)) < 4096
    assert prepared["limits"] == output_storage.DEFAULT_POLICY
    assert db.list_chat_outputs(chat_id) == []
    output = commit(client, grant, prepared).json()
    stored = db.one("SELECT * FROM chat_output_files")
    assert stored["data"] is None and stored["preview"] is None
    assert stored["object_key"].startswith("outputs/")
    assert output_store.objects[stored["object_key"]] == b"editable\x00\xff"
    snapshot = client.get(f"/api/chats/{chat_id}").json()
    assert snapshot["outputs"] == [output]
    assert "private-upload" not in json.dumps(snapshot) and "object_key" not in json.dumps(snapshot)
    assert "download_url" not in json.dumps(snapshot) and grant["grant"] not in json.dumps(snapshot)
    restored = client.post(
        "/api/chat-outputs/read",
        json={**grant, "binary": True, "output_id": output["id"], "file_id": output["files"][0]["id"]},
    ).json()
    assert "data" not in restored["file"] and "download_url" in restored["file"]
    assert restored["file"]["sha256"] == hashlib.sha256(b"editable\x00\xff").hexdigest()


def test_configurable_limits_allow_files_above_the_old_inline_limit(client, db, output_store, scope):
    _, grant = scope
    output_storage.save_policy({"max_file_bytes": 32 * output_storage.MiB})
    data = b"x" * (21 * output_storage.MiB)
    prepared = stage(client, db, output_store, grant, data)
    response = commit(client, grant, prepared)
    assert response.status_code == 200
    assert response.json()["files"][0]["size"] == len(data)
    assert db.one("SELECT length(data) AS n FROM chat_output_files")["n"] is None


@pytest.mark.parametrize(
    "setting,amount,code",
    [
        ("global_limit_bytes", 1, "output_storage_limit_exceeded"),
        ("chat_limit_bytes", 1, "output_storage_limit_exceeded"),
        ("max_file_bytes", 1, "output_file_too_large"),
        ("max_publication_bytes", 1, "output_publication_too_large"),
    ],
)
def test_limits_return_usage_and_a_real_settings_deep_link(
    client, db, scope, monkeypatch, setting, amount, code
):
    _, grant = scope
    monkeypatch.setattr(settings, "public_url", "https://app.example.test")
    db.set_setting("output_storage_policy", {setting: amount})
    response = client.post("/api/chat-outputs/prepare", json=document(grant))
    assert response.status_code == 413
    error = response.json()["detail"]
    assert error["code"] == code and error["settings_url"] == "https://app.example.test/settings/storage"
    assert error["limit_bytes"] == amount and error["requested_bytes"] > amount
    assert grant["grant"] not in response.text
    assert not db.query("SELECT * FROM chat_output_uploads")


@pytest.mark.parametrize(
    "changes",
    [
        {"global_limit_bytes": 0},
        {"chat_limit_bytes": True},
        {"max_file_bytes": 1.5},
        {"max_file_bytes": 2**30 + 1},
        {"max_publication_bytes": 2**31 + 1},
        {"minimum_free_bytes": -1},
        {"retention_days": -1},
        {"retention_days": "3"},
        {"retention_keep_latest": "false"},
        {"unknown_setting": 1},
        {"max_file_bytes": 50 * 1024**2},
        {"global_limit_bytes": 2**53},
    ],
)
def test_policy_edits_are_strict_and_atomic(client, db, changes):
    original = output_storage.policy()
    assert client.put("/api/settings/storage", json=changes).status_code == 422
    assert output_storage.policy() == original
    assert db.get_setting("output_storage_policy") is None


def test_policy_and_inventory_require_authentication(anonymous):
    assert anonymous.get("/api/settings/storage").status_code == 401
    assert anonymous.get("/api/settings/storage/outputs").status_code == 401
    assert anonymous.put("/api/settings/storage", json={}).status_code == 401
    assert anonymous.post("/api/settings/storage/cleanup", json={}).status_code == 401


def test_concurrent_reservations_cannot_overbook_instance_quota(client, db, scope):
    _, grant = scope
    output_storage.save_policy({"global_limit_bytes": 10})

    def reserve(number):
        try:
            return chat_outputs.prepare(document(grant, b"123456", request_id=f"reservation-{number}"))
        except HTTPException as exc:
            return exc.status_code

    with ThreadPoolExecutor(max_workers=2) as pool:
        result = list(pool.map(reserve, [1, 2]))
    assert sum(item == 413 for item in result) == 1
    assert len(db.query("SELECT * FROM chat_output_uploads")) == 1
    assert client.get("/api/settings/storage").json()["usage"]["reserved_bytes"] == 6


def test_quota_changes_between_prepare_and_commit_are_enforced(client, db, output_store, scope):
    _, grant = scope
    prepared = stage(client, db, output_store, grant)
    output_storage.save_policy({"global_limit_bytes": 1})
    response = commit(client, grant, prepared)
    assert response.status_code == 413
    assert not db.query("SELECT * FROM chat_outputs")


def test_repreparing_an_unfinished_upload_obeys_new_quotas(client, db, output_store, scope):
    _, grant = scope
    stage(client, db, output_store, grant)
    output_storage.save_policy({"global_limit_bytes": 1})
    response = client.post("/api/chat-outputs/prepare", json=document(grant))
    assert response.status_code == 413
    assert response.json()["detail"]["code"] == "output_storage_limit_exceeded"
    assert not db.query("SELECT * FROM chat_outputs")


def test_completed_retries_need_no_new_quota_or_storage_connection(client, db, output_store, scope):
    _, grant = scope
    prepared = stage(client, db, output_store, grant)
    output = commit(client, grant, prepared).json()
    output_storage.save_policy({"global_limit_bytes": 1, "max_file_bytes": 1, "max_publication_bytes": 1})
    output_store.unavailable = True
    response = client.post("/api/chat-outputs/prepare", json=document(grant))
    assert response.status_code == 200 and response.json() == {"output": output}
    assert commit(client, grant, prepared).json() == output
    assert len(db.query("SELECT * FROM chat_output_revisions")) == 1


def test_previews_count_towards_quota_at_commit(client, db, output_store, scope):
    _, grant = scope
    image = raster()
    body = document(grant, image)
    body["files"][0].update(name="preview.png", mime_type="image/png", role="preview")
    output_storage.save_policy({"global_limit_bytes": len(image)})
    prepared = client.post("/api/chat-outputs/prepare", json=body).json()
    staged = json.loads(db.one("SELECT payload FROM chat_output_uploads")["payload"])["files"][0]
    output_store.put(staged["object_key"], image, len(image))
    assert commit(client, grant, prepared).status_code == 413
    assert not db.query("SELECT * FROM chat_outputs")


def test_idempotent_prepare_commit_and_changed_content_conflict(client, db, output_store, scope):
    _, grant = scope
    first = stage(client, db, output_store, grant)
    assert (
        client.post("/api/chat-outputs/prepare", json=document(grant)).json()["upload_id"]
        == first["upload_id"]
    )
    output = commit(client, grant, first).json()
    assert commit(client, grant, first).json() == output
    assert client.post("/api/chat-outputs/prepare", json=document(grant)).json() == {"output": output}
    assert (
        client.post("/api/chat-outputs/prepare", json=document(grant, title="different")).status_code == 409
    )
    assert len(db.query("SELECT * FROM chat_output_revisions")) == 1


def test_wrong_turn_or_changed_bytes_cannot_commit(client, db, output_store, scope):
    _, grant = scope
    prepared = stage(client, db, output_store, grant)
    staged = json.loads(db.one("SELECT payload FROM chat_output_uploads")["payload"])["files"][0]
    output_store.objects[staged["object_key"]] = b"different-bytes"
    response = commit(client, grant, prepared)
    assert response.status_code == 422 and response.json()["detail"]["code"] == "output_integrity_error"
    assert not db.query("SELECT * FROM chat_outputs")


def test_s3_outages_have_distinct_actionable_errors_without_credentials(client, db, output_store, scope):
    _, grant = scope
    output_store.unavailable = True
    response = client.post("/api/chat-outputs/prepare", json=document(grant))
    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "output_storage_unavailable"
    state = client.get("/api/settings/storage").json()
    assert state["health"]["status"] == "unavailable"


def test_backing_filesystem_reserve_and_unavailable_remote_capacity(client, scope, monkeypatch):
    _, grant = scope
    monkeypatch.setattr(settings, "output_storage_disk_path", "/a/mounted/maxio-volume")
    monkeypatch.setattr(
        output_storage.shutil,
        "disk_usage",
        lambda _path: type("Usage", (), {"total": 1000, "used": 999, "free": 1})(),
    )
    response = client.post("/api/chat-outputs/prepare", json=document(grant))
    assert response.status_code == 507 and response.json()["detail"]["code"] == "output_disk_space_low"
    monkeypatch.setattr(settings, "output_storage_disk_path", "")
    state = client.get("/api/settings/storage").json()
    assert state["filesystems"][0]["available"] is False
    assert state["filesystems"][0]["reason"] == "Not exposed by the S3 API"
    assert state["filesystems"][1]["available"] is True


def test_failed_metadata_commit_keeps_a_durable_object_cleanup_ledger(client, db, output_store, scope):
    _, grant = scope
    prepared = stage(client, db, output_store, grant)
    db.execute(
        "CREATE TEMP TRIGGER storage_commit_failure BEFORE INSERT ON chat_output_files "
        "BEGIN SELECT RAISE(ABORT,'failure'); END"
    )
    with pytest.raises(sqlite3.IntegrityError):
        chat_outputs.publish({**grant, "upload_id": prepared["upload_id"], "request_id": "binary-publish"})
    db.execute("DROP TRIGGER storage_commit_failure")
    assert not db.query("SELECT * FROM chat_output_files")
    assert db.query("SELECT * FROM output_storage_objects WHERE state = 'delete' AND kind = 'original'")
    output_maintenance.collect()
    assert not any(key.startswith("outputs/") for key in output_store.objects)
    assert any(key.startswith("uploads/") for key in output_store.objects)  # PUT URL hasn't expired.
    db.execute("UPDATE output_storage_objects SET delete_after = 0")
    output_maintenance.collect()
    assert output_store.objects == {}


def test_chat_deletion_queues_originals_and_previews_even_through_foreign_key_cascades(
    client, db, output_store, scope
):
    chat_id, grant = scope
    chat_outputs.publish(payload(grant))
    db.execute("UPDATE chat_turns SET state = 'completed' WHERE chat_id = ?", (chat_id,))
    assert client.delete(f"/api/chats/{chat_id}").status_code == 200
    assert not db.query("SELECT * FROM chat_output_files")
    assert db.query("SELECT * FROM output_storage_objects WHERE state = 'delete' AND kind != 'staging'")
    output_maintenance.collect()
    assert not any(key.startswith("outputs/") for key in output_store.objects)


def test_inventory_lists_all_revisions_and_full_sizes_for_filename_search(client, db, scope):
    _, grant = scope
    first = chat_outputs.publish(payload(grant))
    second = chat_outputs.publish(
        payload(grant, request_id="second", output_id=first["id"], expected_revision=1)
    )
    result = client.get("/api/settings/storage/outputs?limit=1&query=drawing.pdf").json()
    assert result["total"] == 2 and result["has_more"] is True
    row = result["outputs"][0]
    assert row["file_count"] == 3 and row["preview_bytes"] > 0
    assert row["total_bytes"] == row["original_bytes"] + row["preview_bytes"]
    assert "data" not in json.dumps(result) and "object_key" not in json.dumps(result)
    assert (
        client.get("/api/settings/storage/outputs?sort=oldest").json()["outputs"][0]["revision"]
        == first["revision"]
    )
    assert (
        client.get("/api/settings/storage/outputs?sort=newest").json()["outputs"][0]["revision"]
        == second["revision"]
    )


def test_inventory_pagination_has_stable_tiebreakers_for_equal_sizes_and_timestamps(client, db, scope):
    _, grant = scope
    first = chat_outputs.publish(payload(grant))
    chat_outputs.publish(payload(grant, request_id="second", output_id=first["id"], expected_revision=1))
    db.execute("UPDATE chat_output_revisions SET created_at = 1")
    for sort, expected in (("oldest", [1, 2]), ("newest", [2, 1]), ("largest", [2, 1])):
        pages = [
            client.get(f"/api/settings/storage/outputs?sort={sort}&offset={offset}&limit=1").json()
            for offset in (0, 1)
        ]
        assert [page["outputs"][0]["revision"] for page in pages] == expected


def test_cleanup_preview_never_expands_to_new_publications_and_removes_reply_cards(client, db, scope):
    chat_id, grant = scope
    first = chat_outputs.publish(payload(grant))
    db.add_message(chat_id, "assistant", "First result", {"outputs": [first]})
    preview = client.post("/api/settings/storage/cleanup/preview", json={"output_ids": [first["id"]]}).json()
    assert preview["revision_count"] == 1 and preview["includes_current"] is True
    second = chat_outputs.publish(
        payload(grant, request_id="second", output_id=first["id"], expected_revision=1)
    )
    assert (
        client.post(
            "/api/settings/storage/cleanup", json={"plan_id": preview["plan_id"], "confirm": False}
        ).status_code
        == 422
    )
    response = client.post(
        "/api/settings/storage/cleanup", json={"plan_id": preview["plan_id"], "confirm": True}
    )
    assert response.status_code == 200 and response.json()["deleted_revisions"] == 1
    assert db.list_chat_outputs(chat_id) == [second]
    assert not db.list_messages(chat_id)[-1]["meta"]["outputs"]
    assert (
        client.post(
            "/api/settings/storage/cleanup", json={"plan_id": preview["plan_id"], "confirm": True}
        ).status_code
        == 409
    )


def test_deleting_current_revision_falls_back_but_never_reuses_deleted_revision_numbers(client, db, scope):
    chat_id, grant = scope
    first = chat_outputs.publish(payload(grant))
    second = chat_outputs.publish(
        payload(grant, request_id="second", output_id=first["id"], expected_revision=1)
    )
    plan = output_maintenance.preview_cleanup({"revisions": [{"output_id": first["id"], "revision": 2}]})
    output_maintenance.apply_cleanup(plan["plan_id"])
    assert db.list_chat_outputs(chat_id) == [first]
    third = chat_outputs.publish(
        payload(grant, request_id="third", output_id=first["id"], expected_revision=1)
    )
    assert third["revision"] == 3
    assert (
        client.get(
            f"/api/chats/{chat_id}/outputs/{first['id']}/revisions/2/files/{second['files'][0]['id']}"
        ).status_code
        == 404
    )


def test_pending_durable_deletions_keep_charging_quota_until_s3_recovers(client, db, output_store, scope):
    _, grant = scope
    first = chat_outputs.publish(payload(grant))
    plan = output_maintenance.preview_cleanup({"output_ids": [first["id"]]})
    output_store.unavailable = True
    result = output_maintenance.apply_cleanup(plan["plan_id"])
    assert result["collection"]["errors"]
    state = client.get("/api/settings/storage").json()["usage"]
    assert state["used_bytes"] == 0 and state["charged_bytes"] > 0
    assert state["charged_bytes"] == state["pending_deletion_bytes"]
    output_store.unavailable = False
    output_maintenance.collect()
    assert client.get("/api/settings/storage").json()["usage"]["charged_bytes"] == 0


def test_retention_is_disabled_by_default_and_can_preserve_current_revisions(client, db, scope):
    chat_id, grant = scope
    first = chat_outputs.publish(payload(grant))
    second = chat_outputs.publish(
        payload(grant, request_id="second", output_id=first["id"], expected_revision=1)
    )
    db.execute("UPDATE chat_output_revisions SET created_at = ?", (time.time() - 90 * 86400,))
    assert output_maintenance.maintain()["deleted_revisions"] == 0
    output_storage.save_policy({"retention_days": 30, "retention_keep_latest": True})
    assert output_maintenance.maintain()["deleted_revisions"] == 1
    assert db.list_chat_outputs(chat_id) == [second]
    output_storage.save_policy({"retention_keep_latest": False})
    assert output_maintenance.maintain()["deleted_revisions"] == 1
    assert db.list_chat_outputs(chat_id) == []


def test_verified_legacy_migration_releases_both_blobs_and_keeps_manifests(client, db, output_store):
    stored, manifest, data, preview = legacy(db)
    output_maintenance.migrate_file(stored)
    migrated = db.one("SELECT * FROM chat_output_files")
    assert migrated["data"] is None and migrated["preview"] is None
    assert migrated["sha256"] == hashlib.sha256(data).hexdigest()
    assert output_store.objects[migrated["object_key"]] == data
    assert output_store.objects[migrated["preview_key"]] == preview
    assert migrated["preview_size"] == len(preview)
    assert json.loads(db.one("SELECT manifest FROM chat_output_revisions")["manifest"]) == manifest
    output_maintenance.migrate_file(stored)  # Restart/retry is a no-op.
    assert len([key for key in output_store.objects if key.startswith("outputs/")]) == 2


def test_failed_verification_preserves_legacy_bytes_and_surfaces_failure(
    client, db, output_store, monkeypatch
):
    stored, _, data, preview = legacy(db)

    def corrupted(*_args):
        raise output_storage.failure("output_integrity_error", "Checksum failed", 422)

    monkeypatch.setattr(output_store, "download", corrupted)
    result = output_maintenance.maintain()
    assert result["errors"][0]["code"] == "output_integrity_error"
    current = db.one("SELECT * FROM chat_output_files")
    assert current["data"] == data and current["preview"] == preview
    assert current["object_key"] is None
    assert client.get("/api/settings/storage").json()["usage"]["legacy_file_count"] == 1
    assert output_store.objects == {}  # Failed copies were collected without deleting legacy bytes.


def test_nullable_blob_schema_upgrade_retains_legacy_bytes_and_foreign_key_cleanup(tmp_path):
    path = str(tmp_path / "legacy.db")
    database = Database(path)
    stored, manifest, data, preview = legacy(database)
    chat_id = database.one("SELECT id FROM chats")["id"]
    database._conn.close()
    raw = sqlite3.connect(path)
    # This fixture downgrades a current DB; ALTER validates its vec0 triggers.
    load_extension(raw)
    raw.execute("DROP TRIGGER output_file_deleted")
    raw.execute("ALTER TABLE chat_output_files RENAME TO temporary_files")
    raw.execute(
        "CREATE TABLE chat_output_files (id TEXT PRIMARY KEY, output_id TEXT NOT NULL, "
        "revision INTEGER NOT NULL, name TEXT NOT NULL, mime_type TEXT NOT NULL, role TEXT NOT NULL, "
        "size INTEGER NOT NULL, data BLOB NOT NULL, preview BLOB, FOREIGN KEY (output_id,revision) "
        "REFERENCES chat_output_revisions(output_id,revision) ON DELETE CASCADE)"
    )
    raw.execute(
        "INSERT INTO chat_output_files "
        "SELECT id,output_id,revision,name,mime_type,role,size,data,preview FROM temporary_files"
    )
    raw.execute("DROP TABLE temporary_files")
    raw.commit()
    raw.close()
    upgraded = Database(path)
    columns = {row["name"]: row for row in upgraded.query("PRAGMA table_info(chat_output_files)")}
    assert columns["data"]["notnull"] == 0 and "object_key" in columns
    assert upgraded.query("PRAGMA foreign_key_check") == []
    migrated = upgraded.one("SELECT * FROM chat_output_files WHERE id = ?", (stored["id"],))
    assert migrated["data"] == data and migrated["preview"] == preview
    assert upgraded.list_chat_outputs(chat_id) == [manifest]
    assert upgraded.one("SELECT next_revision FROM chat_outputs")["next_revision"] == 2
    upgraded.execute("DELETE FROM chats WHERE id = ?", (chat_id,))
    assert upgraded.query("SELECT * FROM chat_output_files") == []
    upgraded._conn.close()


def test_final_copy_is_verified_even_if_a_store_ignores_source_immutability(
    client, db, output_store, scope, monkeypatch
):
    _, grant = scope
    prepared = stage(client, db, output_store, grant)
    copy = output_store.copy

    def corrupt_copy(source, target):
        copy(source, target)
        output_store.objects[target] = b"corrupted-final-copy"

    monkeypatch.setattr(output_store, "copy", corrupt_copy)
    response = commit(client, grant, prepared)
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "output_integrity_error"
    assert not db.query("SELECT * FROM chat_output_files")


def test_upload_sessions_are_scoped_expiring_and_fenced_after_stop(client, db, output_store, scope):
    chat_id, grant = scope
    prepared = stage(client, db, output_store, grant)
    other = db.create_chat("Other", "default")
    db.accept_chat_message(other["id"], "Hello", "another-output-turn")
    with chat_outputs.publication_grant(other["id"], "another-output-turn") as other_grant:
        assert commit(client, other_grant, prepared).status_code == 409
    db.execute("UPDATE chat_output_uploads SET expires_at = 0")
    assert commit(client, grant, prepared).status_code == 409
    next_plan = stage(client, db, output_store, grant, request_id="next")
    db.stop_chat_turn(chat_id, "storage-turn")
    assert commit(client, grant, next_plan, "next").status_code == 409
    output_maintenance.collect()
    assert not db.query("SELECT * FROM chat_output_uploads WHERE chat_id = ?", (chat_id,))
    assert db.list_chat_outputs(chat_id) == []
    assert all(row["state"] == "delete" for row in db.query("SELECT * FROM output_storage_objects"))


def test_disk_reservations_prevent_parallel_uploads_overbooking_physical_capacity(
    client, db, scope, monkeypatch
):
    _, grant = scope
    output_storage.save_policy({"minimum_free_bytes": 0})
    monkeypatch.setattr(settings, "output_storage_disk_path", "/mounted-store")
    monkeypatch.setattr(
        output_storage.shutil,
        "disk_usage",
        lambda _: type("Usage", (), {"total": 100, "used": 0, "free": 100})(),
    )
    assert client.post("/api/chat-outputs/prepare", json=document(grant, b"x" * 20)).status_code == 200
    response = client.post(
        "/api/chat-outputs/prepare", json=document(grant, b"x" * 40, request_id="parallel")
    )
    assert response.status_code == 507 and response.json()["detail"]["code"] == "output_disk_space_low"
    assert len(db.query("SELECT * FROM chat_output_uploads")) == 1


def test_physical_reservations_include_thumbnails_of_other_prepared_uploads(client, db, scope, monkeypatch):
    _, grant = scope
    output_storage.save_policy({"minimum_free_bytes": 0})
    monkeypatch.setattr(settings, "output_storage_disk_path", "/mounted-store")
    monkeypatch.setattr(
        output_storage.shutil,
        "disk_usage",
        lambda _: type(
            "Usage", (), {"total": 15 * output_storage.MiB, "used": 0, "free": 15 * output_storage.MiB}
        )(),
    )
    body = document(grant, raster())
    body["files"][0].update(name="preview.png", mime_type="image/png", role="preview")
    assert client.post("/api/chat-outputs/prepare", json=body).status_code == 200
    response = client.post("/api/chat-outputs/prepare", json={**body, "request_id": "parallel-image"})
    assert response.status_code == 507 and response.json()["detail"]["code"] == "output_disk_space_low"
    assert len(db.query("SELECT * FROM chat_output_uploads")) == 1


def test_temporary_filesystem_full_is_distinct_from_an_s3_outage(
    client, db, output_store, scope, monkeypatch
):
    _, grant = scope
    prepared = stage(client, db, output_store, grant)

    def no_space(*_args, **_kwargs):
        raise OSError(errno.ENOSPC, "Disk full")

    monkeypatch.setattr(chat_outputs, "TemporaryFile", no_space)
    response = commit(client, grant, prepared)
    assert response.status_code == 507 and response.json()["detail"]["code"] == "output_disk_full"
    assert response.json()["detail"]["settings_url"] == "/settings/storage"
    assert not db.query("SELECT * FROM chat_outputs")


def test_legacy_migration_rejects_bytes_that_no_longer_match_the_immutable_manifest(db, output_store):
    stored, _, data, preview = legacy(db)
    db.execute("UPDATE chat_output_files SET data = ?", (b"corrupt-source",))
    with pytest.raises(HTTPException, match="Legacy bytes"):
        output_maintenance.migrate_file(stored)
    assert db.one("SELECT preview FROM chat_output_files")["preview"] == preview
    assert not output_store.objects


@pytest.mark.parametrize(
    "origin", ["https://[broken", "https://app.example:invalid", "https://app.example:65536"]
)
def test_malformed_application_urls_fall_back_to_the_implemented_storage_route(monkeypatch, origin):
    monkeypatch.setattr(settings, "public_url", origin)
    assert output_storage.settings_url() == "/settings/storage"


@pytest.mark.parametrize(
    "endpoint", ["http://[private-detail", "http://maxio:private-detail", "http://maxio:65536"]
)
def test_malformed_s3_origins_return_actionable_settings_health_without_echoing_them(
    client, monkeypatch, endpoint
):
    monkeypatch.setattr(settings, "output_s3_endpoint", endpoint)
    monkeypatch.setattr(settings, "output_s3_access_key", "test-access")
    monkeypatch.setattr(settings, "output_s3_secret_key", "test-secret")
    monkeypatch.setattr(output_storage, "get_store", output_storage.S3Store)
    response = client.get("/api/settings/storage")
    assert response.status_code == 200
    state = response.json()
    assert state["health"]["error"]["code"] == "output_storage_unconfigured"
    assert state["health"]["error"]["settings_url"] == "/settings/storage"
    assert state["endpoint"] == "Invalid server-side S3 endpoint (details hidden)"
    assert "private-detail" not in response.text


@pytest.mark.parametrize("status", ["Enabled", "Suspended"])
def test_versioned_buckets_are_rejected_without_changing_their_policy(monkeypatch, status):
    store = object.__new__(output_storage.S3Store)
    operations = []

    def call(operation):
        operations.append(operation)
        return {"Status": status} if operation == "get_bucket_versioning" else {}

    monkeypatch.setattr(store, "call", call)
    with pytest.raises(HTTPException) as error:
        store.ready()
    assert error.value.detail["code"] == "output_storage_configuration_error"
    assert operations == ["head_bucket", "get_bucket_versioning"]


def test_maximum_unicode_metadata_fits_the_binary_control_protocol(client, scope):
    _, grant = scope
    body = document(grant, title="📁" * 120, summary="📁" * 2000, source_app="📁" * 64)
    body["files"] = [{**body["files"][0], "name": "📁" * 196 + ".dxf"} for _ in range(8)]
    assert len(json.dumps(body).encode()) > 16384
    assert client.post("/api/chat-outputs/prepare", json=body).status_code == 200


def test_long_tool_ids_remain_idempotent_and_link_to_the_correct_answer(db, scope):
    chat_id, grant = scope
    long_id = "provider-call-" * 100
    request_id = publication_request_id(long_id)
    assert len(request_id) < 256
    output = chat_outputs.publish(payload(grant, request_id=request_id))
    db.finish_chat_turn("storage-turn", "Result", {"steps": [{"kind": "tool", "id": long_id}]})
    assert db.list_messages(chat_id)[-1]["meta"]["outputs"] == [output]


async def test_mcp_has_storage_management_and_quota_errors_preserve_the_settings_link(backend, scope):
    _, grant = scope
    names = {tool.name for tool in (await main.backend_mcp.list_tools(None, None)).tools}
    assert {
        "output_storage_status",
        "configure_output_storage",
        "list_stored_outputs",
        "preview_output_cleanup",
        "delete_stored_outputs",
    } <= names
    output_storage.save_policy({"global_limit_bytes": 1})
    result = await main.backend_mcp.call("prepare_chat_output", {"body": document(grant)})
    assert result.is_error
    assert json.loads(result.content[0].text)["detail"]["settings_url"] == "/settings/storage"
    assert grant["grant"] not in result.content[0].text
