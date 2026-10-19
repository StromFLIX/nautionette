"""Restart-safe output migration, object collection, inventory and retention."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import time
import uuid
from tempfile import TemporaryFile
from typing import Any

from fastapi import HTTPException
from starlette.concurrency import run_in_threadpool

from . import output_storage as storage
from .config import settings
from .db import db
from .events import bus

log = logging.getLogger("nautionette.outputs")


def inventory(*, offset: int = 0, limit: int = 50, sort: str = "largest", query: str = "") -> dict[str, Any]:
    orders = {
        "largest": "total_bytes DESC, r.created_at DESC, r.output_id, r.revision DESC",
        "oldest": "r.created_at, r.output_id, r.revision",
        "newest": "r.created_at DESC, r.output_id, r.revision DESC",
    }
    if sort not in orders:
        raise HTTPException(422, "Sort outputs by largest, oldest or newest")
    search = (query, *([f"%{query}%"] * 3))
    # An EXISTS-style subquery preserves full revision sizes when a filename
    # matches only one file in the snapshot.
    source = (
        " FROM chat_output_revisions r JOIN chat_outputs o ON o.id = r.output_id "
        "JOIN chats c ON c.id = o.chat_id JOIN chat_output_files f "
        "ON f.output_id = r.output_id AND f.revision = r.revision "
        "WHERE (? = '' OR c.title LIKE ? OR json_extract(r.manifest,'$.title') LIKE ? "
        "OR EXISTS (SELECT 1 FROM chat_output_files q WHERE q.output_id = r.output_id "
        "AND q.revision = r.revision AND q.name LIKE ?)) "
    )
    select = (
        "SELECT r.output_id, r.revision, r.created_at, r.manifest, o.chat_id, c.title AS chat_title, "
        "o.current_revision, SUM(f.size) AS original_bytes, "
        "SUM(CASE WHEN f.preview_key IS NOT NULL THEN f.preview_size "
        "ELSE COALESCE(length(f.preview),0) END) AS preview_bytes, "
        "SUM(f.size + CASE WHEN f.preview_key IS NOT NULL THEN f.preview_size "
        "ELSE COALESCE(length(f.preview),0) END) AS total_bytes, "
        "COUNT(*) AS file_count"
    )
    grouped = source + " GROUP BY r.output_id, r.revision"
    total = db.one("SELECT COUNT(*) AS n FROM (SELECT 1" + grouped + ")", search)["n"]  # noqa: S608 - static SQL
    rows = db.query(
        select + grouped + " ORDER BY " + orders[sort] + " LIMIT ? OFFSET ?",  # noqa: S608 - whitelisted sort
        (*search, limit, offset),
    )
    for row in rows:
        manifest = json.loads(row.pop("manifest"))
        row.update(
            title=manifest["title"],
            source_app=manifest.get("source_app", ""),
            files=manifest["files"],
            is_current=row["revision"] == row["current_revision"],
        )
    return {
        "outputs": rows,
        "total": total,
        "offset": offset,
        "limit": limit,
        "has_more": offset + len(rows) < total,
    }


def _candidates(selector: dict[str, Any], *, limit: int = 1001) -> list[dict[str, Any]]:
    criteria = [key for key in ("revisions", "output_ids", "older_than_days") if key in selector]
    if len(criteria) != 1:
        raise HTTPException(422, "Select revisions, whole outputs, or an age filter (exactly one)")
    where, args = [], []
    if "revisions" in selector:
        if not selector["revisions"]:
            raise HTTPException(422, "Select at least one output revision")
        where.append(
            "(" + " OR ".join("(r.output_id = ? AND r.revision = ?)" for _ in selector["revisions"]) + ")"
        )
        for item in selector["revisions"]:
            args.extend((item["output_id"], item["revision"]))
    elif "output_ids" in selector:
        if not selector["output_ids"]:
            raise HTTPException(422, "Select at least one output")
        where.append("r.output_id IN (" + ",".join("?" for _ in selector["output_ids"]) + ")")
        args.extend(selector["output_ids"])
    else:
        where.append("r.created_at < ?")
        args.append(time.time() - selector["older_than_days"] * 86400)
    if selector.get("keep_latest", False):
        where.append("r.revision != o.current_revision")
    rows = db.query(
        "SELECT r.output_id, r.revision, r.created_at, o.chat_id, o.current_revision, "
        "json_extract(r.manifest,'$.title') AS title, SUM(f.size + CASE WHEN f.preview_key IS NOT NULL "
        "THEN f.preview_size ELSE COALESCE(length(f.preview),0) END) AS total_bytes "
        "FROM chat_output_revisions r JOIN chat_outputs o ON o.id = r.output_id "
        "JOIN chat_output_files f ON f.output_id = r.output_id AND f.revision = r.revision WHERE "
        + " AND ".join(where)
        + " GROUP BY r.output_id, r.revision ORDER BY r.created_at, r.output_id, r.revision LIMIT ?",  # noqa: S608 - fixed predicates/placeholders
        (*args, limit),
    )
    return rows


def preview_cleanup(selector: dict[str, Any]) -> dict[str, Any]:
    with storage.lock:
        rows = _candidates(selector)
        if len(rows) > 1000:
            raise HTTPException(
                422, "More than 1000 revisions match. Narrow the selection or clean up in batches."
            )
        plan_id = uuid.uuid4().hex
        expires = time.time() + 900
        db.execute("INSERT INTO output_cleanup_plans VALUES (?,?,?)", (plan_id, json.dumps(rows), expires))
    return {
        "plan_id": plan_id,
        "expires_at": expires,
        "revisions": rows,
        "revision_count": len(rows),
        "output_count": len({row["output_id"] for row in rows}),
        "bytes": sum(row["total_bytes"] for row in rows),
        "includes_current": any(row["revision"] == row["current_revision"] for row in rows),
    }


def _delete_revisions(rows: list[dict[str, Any]]) -> dict[str, Any]:
    removed: set[tuple[str, int]] = set()
    chats: set[str] = set()
    removed_bytes = 0
    with db.transaction() as conn:
        for row in rows:
            # Revisions are immutable; a preview never expands to newer results.
            stored = conn.execute(
                "SELECT o.chat_id FROM chat_output_revisions r JOIN chat_outputs o ON o.id = r.output_id "
                "WHERE r.output_id = ? AND r.revision = ?",
                (row["output_id"], row["revision"]),
            ).fetchone()
            if not stored:
                continue
            conn.execute(
                "DELETE FROM chat_output_revisions WHERE output_id = ? AND revision = ?",
                (row["output_id"], row["revision"]),
            )
            removed.add((row["output_id"], row["revision"]))
            removed_bytes += row["total_bytes"]
            chats.add(stored["chat_id"])
        for output_id in {item[0] for item in removed}:
            latest = conn.execute(
                "SELECT MAX(revision) FROM chat_output_revisions WHERE output_id = ?", (output_id,)
            ).fetchone()[0]
            if latest is None:
                conn.execute("DELETE FROM chat_outputs WHERE id = ?", (output_id,))
            else:
                # Never reset next_revision: deleted revision URLs must never
                # later refer to a different document.
                conn.execute(
                    "UPDATE chat_outputs SET current_revision = ?, updated_at = ? WHERE id = ?",
                    (latest, time.time(), output_id),
                )
        for chat_id in chats:
            messages = conn.execute("SELECT id, meta FROM messages WHERE chat_id = ?", (chat_id,)).fetchall()
            for message in messages:
                meta = json.loads(message["meta"])
                if "outputs" in meta:
                    meta["outputs"] = [
                        item for item in meta["outputs"] if (item["id"], item["revision"]) not in removed
                    ]
                    conn.execute(
                        "UPDATE messages SET meta = ? WHERE id = ?", (json.dumps(meta), message["id"])
                    )
            conn.execute("UPDATE chats SET updated_at = ? WHERE id = ?", (time.time(), chat_id))
    return {"deleted_revisions": len(removed), "deleted_bytes": removed_bytes, "chat_ids": sorted(chats)}


def apply_cleanup(plan_id: str) -> dict[str, Any]:
    with storage.lock:
        plan = db.one("SELECT * FROM output_cleanup_plans WHERE id = ?", (plan_id,))
        if not plan or plan["expires_at"] <= time.time():
            raise HTTPException(409, "Cleanup preview expired. Review the affected outputs again.")
        result = _delete_revisions(json.loads(plan["items"]))
        db.execute("DELETE FROM output_cleanup_plans WHERE id = ?", (plan_id,))
        result["collection"] = collect()
        return result


def collect(*, limit: int = 100) -> dict[str, Any]:
    """Deleting metadata always queues objects, even through FK chat cascades."""
    deleted = reclaimed = 0
    errors = []
    with storage.lock:
        now = time.time()
        with db.transaction() as conn:
            conn.execute("DELETE FROM output_cleanup_plans WHERE expires_at <= ?", (now,))
            conn.execute(
                "DELETE FROM chat_output_uploads WHERE expires_at <= ? OR NOT EXISTS "
                "(SELECT 1 FROM chat_turns t WHERE t.id = chat_output_uploads.turn_id "
                "AND t.state = 'running' AND t.stop_requested = 0)",
                (now,),
            )
            conn.execute(
                "UPDATE output_storage_objects SET state = 'delete', delete_after = 0 "
                "WHERE state = 'pending' AND delete_after <= ?",
                (now,),
            )
        rows = db.query(
            "SELECT key,size FROM output_storage_objects WHERE state = 'delete' "
            "AND delete_after <= ? ORDER BY created_at LIMIT ?",
            (now, limit),
        )
        for row in rows:
            try:
                storage.get_store().delete(row["key"])
            except HTTPException as exc:
                errors.append(exc.detail)
                break  # Retry later; do not hammer an unavailable store.
            db.execute("DELETE FROM output_storage_objects WHERE key = ? AND state = 'delete'", (row["key"],))
            deleted += 1
            reclaimed += row["size"]
    return {"deleted_objects": deleted, "reclaimed_bytes": reclaimed, "errors": errors}


def migrate_file(stored: dict[str, Any]) -> None:
    """Copy, read back/checksum, then release BOTH legacy blobs atomically."""
    keys: list[str] = []
    with storage.lock:
        current = db.one("SELECT * FROM chat_output_files WHERE id = ?", (stored["id"],))
        if not current or current["object_key"]:
            return
        if current["data"] is None:
            raise storage.failure(
                "output_integrity_error", "Legacy output bytes are missing; migration stopped.", 500
            )
        from .chat_outputs import _register

        store = storage.get_store()
        store.ready()
        data, preview = current["data"], current["preview"]
        sha = hashlib.sha256(data).hexdigest()
        revision = db.one(
            "SELECT manifest FROM chat_output_revisions WHERE output_id = ? AND revision = ?",
            (current["output_id"], current["revision"]),
        )
        if not revision:
            return  # The owning chat/revision was deleted before migration began.
        original = next(
            item for item in json.loads(revision["manifest"])["files"] if item["id"] == current["id"]
        )
        if len(data) != current["size"] or original.get("sha256") != sha:
            raise storage.failure(
                "output_integrity_error",
                "Legacy bytes do not match the original manifest; migration stopped.",
                422,
            )
        storage.require_space(len(data) + len(preview or b""))
        storage.require_space(max(len(data), len(preview or b"")), backend=True)
        key = f"outputs/{current['output_id']}/{current['revision']}/migrated-{uuid.uuid4().hex}"
        preview_key = key + ".preview.png" if preview is not None else None
        try:
            for target, content, kind in [(key, data, "original"), (preview_key, preview, "preview")]:
                if target is None:
                    continue
                _register(target, len(content), kind)
                keys.append(target)
                store.put(target, content, len(content))
                with TemporaryFile(dir=settings.data_dir) as verified:
                    store.download(target, verified, len(content), hashlib.sha256(content).hexdigest())
            with db.transaction() as conn:
                changed = conn.execute(
                    "UPDATE chat_output_files SET object_key = ?, preview_key = ?, preview_size = ?, "
                    "sha256 = ?, data = NULL, preview = NULL WHERE id = ? AND object_key IS NULL",
                    (key, preview_key, len(preview or b""), sha, current["id"]),
                ).rowcount
                if changed:
                    conn.executemany(
                        "UPDATE output_storage_objects SET state = 'live', delete_after = 0 WHERE key = ?",
                        [(key,) for key in keys],
                    )
                else:
                    conn.executemany(
                        "UPDATE output_storage_objects SET state = 'delete', delete_after = 0 WHERE key = ?",
                        [(key,) for key in keys],
                    )
            db.set_setting("output_storage_compaction_pending", True)
        except Exception as exc:
            with db.transaction() as conn:
                conn.executemany(
                    "UPDATE output_storage_objects SET state = 'delete', delete_after = 0 WHERE key = ?",
                    [(key,) for key in keys],
                )
            if isinstance(exc, OSError):
                raise storage.io_failure(exc) from None
            raise


def compact_database() -> dict[str, bool]:
    with storage.lock:
        from pathlib import Path

        size = Path(db.path).stat().st_size
        storage.require_space(size, backend=True)
        db.compact()
        db.set_setting("output_storage_compaction_pending", False)
    return {"ok": True}


def maintain() -> dict[str, Any]:
    started = time.time()
    with storage.lock:
        migrated = 0
        errors = []
        rows = db.query("SELECT id FROM chat_output_files WHERE object_key IS NULL LIMIT 10")
        for row in rows:
            try:
                migrate_file(row)
                migrated += 1
            except HTTPException as exc:
                errors.append(exc.detail)
                break
        limits = storage.policy()
        result = {"deleted_revisions": 0, "chat_ids": []}
        if limits["retention_days"]:
            candidates = _candidates(
                {"older_than_days": limits["retention_days"], "keep_latest": limits["retention_keep_latest"]},
                limit=500,
            )
            result = _delete_revisions(candidates)
        collection = collect()
        errors.extend(collection["errors"])
        # Do not block active turns with a large VACUUM. The explicit settings
        # action remains available; a quiet later maintenance pass reclaims it.
        if (
            db.get_setting("output_storage_compaction_pending", False)
            and not db.one("SELECT 1 FROM chat_output_files WHERE object_key IS NULL LIMIT 1")
            and not db.one("SELECT 1 FROM chat_turns WHERE state = 'running' LIMIT 1")
        ):
            try:
                compact_database()
            except HTTPException as exc:
                errors.append(exc.detail)
        state = {
            "last_run_at": started,
            "migrated_files": migrated,
            "errors": errors,
            "collection": collection,
        }
        db.set_setting("output_storage_maintenance", state)
        return {**result, **state}


async def run() -> None:
    while True:
        try:
            result = await run_in_threadpool(maintain)
            for chat_id in result["chat_ids"]:
                bus.publish("chat.output.deleted", {"chat_id": chat_id})
            if result["errors"]:
                log.warning("Output storage maintenance needs attention; see Settings → Storage")
        except Exception:
            # Never emit S3 exception repr/URLs/credentials to events or logs.
            db.set_setting(
                "output_storage_maintenance",
                {
                    "last_run_at": time.time(),
                    "errors": [
                        {
                            "code": "output_maintenance_failed",
                            "message": "Output storage maintenance failed. Check the storage service "
                            "and retry in Settings → Storage.",
                        }
                    ],
                },
            )
            log.error("Output storage maintenance failed; see Settings → Storage")
        await asyncio.sleep(60)
