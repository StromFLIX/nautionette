"""Versioned mini-app publication, recovery and at-most-once mutation dispatch."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
import threading
import time
from contextlib import contextmanager
from typing import Any

import httpx
from fastapi import HTTPException

from . import miniapp_capabilities as capabilities
from .clients import broker
from .db import db
from .events import bus
from .miniapp_models import AppRequest, Publication

ID = re.compile(r"[a-z][a-z0-9-]{0,47}")
CALL_ID = re.compile(r"([0-9]{13})-[0-9a-f]{32}")
LIMIT = 65536
log = logging.getLogger(__name__)


_writers: set[str] = set()
_writers_lock = threading.Lock()


@contextmanager
def exclusive(app_id: str):
    checked(app_id)
    with _writers_lock:
        if app_id in _writers:
            raise HTTPException(409, "Another app lifecycle operation is in progress; inspect state")
        _writers.add(app_id)
    try:
        yield
    finally:
        with _writers_lock:
            _writers.discard(app_id)


def checked(app_id: str) -> str:
    if not ID.fullmatch(app_id):
        raise HTTPException(422, "Invalid mini-app ID")
    return app_id


def app(app_id: str) -> dict:
    rows = db.query("SELECT * FROM mini_apps WHERE id=?", (checked(app_id),))
    if not rows:
        raise HTTPException(404, "Mini-app not found")
    return rows[0]


def release(app_id: str, revision: int) -> dict:
    rows = db.query(
        "SELECT * FROM mini_app_releases WHERE app_id=? AND revision=?", (checked(app_id), revision)
    )
    if not rows:
        raise HTTPException(404, "Mini-app release not found")
    return {**rows[0], "manifest": json.loads(rows[0]["manifest"])}


def list_apps() -> list[dict]:
    return db.query("SELECT * FROM mini_apps ORDER BY pinned DESC, pinned_at DESC, updated_at DESC, id")


def details(app_id: str) -> dict:
    item = app(app_id)
    versions = db.query(
        "SELECT revision,state,error,created_at FROM mini_app_releases WHERE app_id=? ORDER BY revision DESC",
        (app_id,),
    )
    active = release(app_id, item["active_revision"]) if item["active_revision"] else None
    # Always JSON. There is deliberately no executable/raw-HTML asset endpoint.
    return {**item, "active": active, "releases": versions}


def activate(app_id: str, revision: int, manifest: dict) -> None:
    with db.transaction() as conn:
        owner = manifest["agent_id"]
        if owner and not conn.execute("SELECT 1 FROM agent_profiles WHERE id=?", (owner,)).fetchone():
            owner = None  # The profile may have been deleted while compilation was running.
        changed = conn.execute(
            "UPDATE mini_app_releases SET state='ready',error='' "
            "WHERE app_id=? AND revision=? AND state='building'",
            (app_id, revision),
        )
        if not changed.rowcount:
            return  # A delayed recovery reply cannot revert newer activation or metadata edits.
        conn.execute(
            "UPDATE mini_apps SET active_revision=?,title=?,agent_id=?,icon=?,image=?,pinned=?,"
            "pinned_revision=?,pinned_at=?,updated_at=? WHERE id=?",
            (
                revision,
                manifest["title"],
                owner,
                manifest.get("icon", "apps"),
                manifest.get("image", ""),
                int(manifest["pinned"]),
                revision if manifest["pinned"] else None,
                time.time() if manifest["pinned"] else 0,
                time.time(),
                app_id,
            ),
        )
    bus.publish("miniapp.changed", {"app_id": app_id, "revision": revision})


async def publish(app_id: str, publication: Publication) -> dict:
    checked(app_id)
    manifest = publication.manifest.model_dump()
    if manifest["agent_id"] and not db.query(
        "SELECT id FROM agent_profiles WHERE id=?", (manifest["agent_id"],)
    ):
        raise HTTPException(422, "Unknown owning agent profile")
    now = time.time()
    with db.transaction() as conn:
        existing = conn.execute("SELECT * FROM mini_apps WHERE id=?", (app_id,)).fetchone()
        if (existing["revision"] if existing else 0) != publication.expected_revision:
            raise HTTPException(409, "Revision changed; read the app before publishing again")
        if existing and existing["archived"]:
            raise HTTPException(409, "Unarchive the app before publishing")
        if (
            existing
            and conn.execute(
                "SELECT 1 FROM mini_app_releases WHERE app_id=? AND state='building'", (app_id,)
            ).fetchone()
        ):
            raise HTTPException(409, "A publication is already pending; inspect its status")
        if not existing and conn.execute("SELECT COUNT(*) FROM mini_apps").fetchone()[0] >= 16:
            raise HTTPException(413, "The instance supports at most 16 mini-apps; remove an unused app first")
        if existing and existing["active_revision"]:
            old = json.loads(
                conn.execute(
                    "SELECT manifest FROM mini_app_releases WHERE app_id=? AND revision=?",
                    (app_id, existing["active_revision"]),
                ).fetchone()[0]
            )
            if old["backend"] and not manifest["backend"]:
                raise HTTPException(
                    409, "Removing a backend would strand its data; use a separate frontend-only app"
                )
        revision = publication.expected_revision + 1
        if not existing:
            conn.execute(
                "INSERT INTO mini_apps(id,title,updated_at,pinned) VALUES(?,?,?,0)",
                (app_id, manifest["title"], now),
            )
        conn.execute("UPDATE mini_apps SET revision=? WHERE id=?", (revision, app_id))
        conn.execute(
            "INSERT INTO mini_app_releases(app_id,revision,manifest,state,created_at) "
            "VALUES(?,?,?,'building',?)",
            (app_id, revision, json.dumps(manifest), now),
        )
        # Keep the active release and the five newest sources. Runtime images are
        # rebuilt from these locked sources on rollback, not retained indefinitely.
        conn.execute(
            "DELETE FROM mini_app_releases WHERE app_id=? AND revision NOT IN "
            "(SELECT revision FROM mini_app_releases WHERE app_id=? ORDER BY revision DESC LIMIT 5) "
            "AND revision != COALESCE((SELECT active_revision FROM mini_apps WHERE id=?),0)",
            (app_id, app_id, app_id),
        )
    if manifest["backend"]:
        runtime = manifest["backend"]
        try:
            status = await broker.miniapp(
                app_id,
                "deploy",
                {
                    "revision": revision,
                    "source": runtime["source"],
                    "memory_mb": runtime["memory_mb"],
                    "mode": runtime["mode"],
                },
            )
            if status.get("revision") != revision:
                raise RuntimeError("Runtime did not confirm the requested revision")
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 422:
                # Explicit broker rejection, not an ambiguous transport failure.
                diagnostic = str(exc.response.json().get("detail", "Build rejected"))[:7000]
                db.execute(
                    "UPDATE mini_app_releases SET state='failed',error=? WHERE app_id=? AND revision=?",
                    (diagnostic, app_id, revision),
                )
                bus.publish("miniapp.changed", {"app_id": app_id, "revision": revision})
                raise HTTPException(422, diagnostic) from exc
            raise HTTPException(503, "Publication not confirmed; inspect status before republishing") from exc
        except Exception as exc:
            # A lost response is not a failed deployment. Reconciliation checks
            # the broker's durable active pointer, never repeats a deployment POST.
            db.execute(
                "UPDATE mini_app_releases SET error='Build pending or unavailable; checking runtime state' "
                "WHERE app_id=? AND revision=?",
                (app_id, revision),
            )
            raise HTTPException(
                503,
                {
                    "code": "publication_pending",
                    "message": "Publication not confirmed. Inspect this app; do not blindly republish.",
                    "app_id": app_id,
                    "revision": revision,
                },
            ) from exc
    activate(app_id, revision, manifest)
    return details(app_id)


async def reconcile() -> None:
    for row in db.query(
        "SELECT app_id,revision,manifest,created_at FROM mini_app_releases WHERE state='building'"
    ):
        manifest = json.loads(row["manifest"])
        if not manifest["backend"]:
            activate(row["app_id"], row["revision"], manifest)
            continue
        try:
            status = await broker.miniapp(row["app_id"])
        except Exception:  # noqa: S112 - pending state remains visible; outage is not a failed build
            # Infrastructure outage must not silently erase a pending publication.
            # The pending state/error remains visible in authenticated app status.
            continue
        if status.get("revision") == row["revision"]:
            activate(row["app_id"], row["revision"], manifest)
        elif time.time() - row["created_at"] > 270:
            db.execute(
                "UPDATE mini_app_releases SET state='failed', "
                "error='Build or health check failed; previous release retained' "
                "WHERE app_id=? AND revision=?",
                (row["app_id"], row["revision"]),
            )
            bus.publish("miniapp.changed", {"app_id": row["app_id"], "revision": row["revision"]})
    # Recover a crash/lost reply between persisting archive intent and applying
    # it to Docker. Inspect the cheap journal, not expensive per-container stats.
    for item in list_apps():
        if (
            not item["active_revision"]
            or not release(item["id"], item["active_revision"])["manifest"]["backend"]
        ):
            continue
        try:
            with exclusive(item["id"]):
                desired = app(item["id"])
                status = await broker.miniapp(item["id"], "state")
                if (
                    status.get("revision") is not None
                    and not status.get("deleting")
                    and bool(status.get("disabled")) != bool(desired["archived"])
                ):
                    await broker.miniapp(
                        item["id"],
                        "control",
                        {
                            "action": "suspend" if desired["archived"] else "resume",
                        },
                    )
        except Exception as exc:
            log.debug("App lifecycle reconciliation pending for %s: %s", item["id"], type(exc).__name__)
    await capabilities.reap()


async def maintenance() -> None:
    try:
        while True:
            await reconcile()
            await asyncio.sleep(30)
    finally:
        for app_id in list(capabilities.sessions):
            await capabilities.close(app_id)


def current(app_id: str, revision: int) -> dict:
    item = app(app_id)
    if item["archived"] or item["active_revision"] != revision:
        raise HTTPException(409, "This mini-app release is inactive or archived; reopen it")
    return release(app_id, revision)["manifest"]


def begin(app_id: str, revision: int, channel: str, payload: dict) -> tuple[str, Any]:
    request_id = payload.get("request_id", "")
    match = CALL_ID.fullmatch(request_id) if isinstance(request_id, str) else None
    if not match or not -60 <= time.time() - int(match[1]) / 1000 <= 86400:
        raise HTTPException(
            422, "Mutations need a fresh timestamp-UUID request_id; retain it when checking a lost result"
        )
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    if len(encoded.encode()) > 48 * 1024:
        raise HTTPException(413, "App request exceeds 48 KiB")
    digest = hashlib.sha256(f"{revision}:{encoded}".encode()).hexdigest()
    with db.transaction() as conn:
        conn.execute("DELETE FROM mini_app_calls WHERE created_at < ?", (time.time() - 86500,))
        old = conn.execute(
            "SELECT * FROM mini_app_calls WHERE app_id=? AND channel=? AND request_id=?",
            (app_id, channel, request_id),
        ).fetchone()
        if old:
            if old["request_hash"] != digest:
                raise HTTPException(409, "Request ID was already used with different arguments or release")
            if old["state"] != "complete":
                raise HTTPException(
                    409,
                    {
                        "code": "outcome_unknown",
                        "message": (
                            "Operation may already have executed. Inspect state; "
                            "it will not be automatically repeated."
                        ),
                    },
                )
            return request_id, json.loads(old["response"])
        if conn.execute("SELECT COUNT(*) FROM mini_app_calls").fetchone()[0] >= 4096:
            raise HTTPException(429, "Instance mutation ledger is full for this 24-hour window")
        conn.execute(
            "INSERT INTO mini_app_calls(app_id,channel,request_id,request_hash,revision,state,created_at) "
            "VALUES(?,?,?,?,?,'started',?)",
            (app_id, channel, request_id, digest, revision, time.time()),
        )
    return request_id, None


async def dispatch(
    app_id: str, revision: int, channel: str, payload: dict, operation, *, mutation: bool
) -> Any:
    request_id, cached = begin(app_id, revision, channel, payload) if mutation else ("", None)
    if cached is not None:
        return cached["result"]
    started = time.monotonic()
    outcome = "failed"
    try:
        result = await operation()
        encoded = json.dumps({"result": result}, ensure_ascii=False, allow_nan=False)
        if len(encoded.encode()) > LIMIT:
            raise HTTPException(413, "App response exceeds 64 KiB")
        if mutation:
            db.execute(
                "UPDATE mini_app_calls SET state='complete',response=? "
                "WHERE app_id=? AND channel=? AND request_id=?",
                (encoded, app_id, channel, request_id),
            )
        bus.publish(
            "miniapp.operation",
            {"app_id": app_id, "revision": revision, "channel": channel, "mutation": mutation},
        )
        outcome = "complete"
        return result
    except BaseException:
        if mutation:
            outcome = "uncertain"
            db.execute(
                "UPDATE mini_app_calls SET state='uncertain' WHERE app_id=? AND channel=? AND request_id=?",
                (app_id, channel, request_id),
            )
        raise
    finally:
        # Bounded, structured diagnostics only: never store request/response data,
        # tool arguments, URLs, credentials or application exception messages.
        operation_name = (
            f"{payload['method']} {payload['path']}"
            if channel == "app"
            else "MCP capability"
            if payload.get("kind") == "mcp"
            else "HTTPS capability"
        )
        try:
            with db.transaction() as conn:
                conn.execute(
                    "INSERT INTO mini_app_events(app_id,revision,channel,operation,request_id,outcome,"
                    "duration_ms,created_at) VALUES(?,?,?,?,?,?,?,?)",
                    (
                        app_id,
                        revision,
                        channel,
                        operation_name,
                        request_id,
                        outcome,
                        int((time.monotonic() - started) * 1000),
                        time.time(),
                    ),
                )
                conn.execute(
                    "DELETE FROM mini_app_events WHERE app_id=? AND id NOT IN "
                    "(SELECT id FROM mini_app_events WHERE app_id=? ORDER BY id DESC LIMIT 100)",
                    (app_id, app_id),
                )
        except Exception:
            log.warning("Mini-app diagnostic recording unavailable for %s", app_id)


async def request(app_id: str, payload: AppRequest) -> Any:
    manifest = current(app_id, payload.revision)
    if not manifest["backend"]:
        raise HTTPException(409, "This app has no backend")

    async def perform():
        try:
            return await broker.miniapp(
                app_id,
                "request",
                {"revision": payload.revision, "request": payload.model_dump(exclude={"revision"})},
            )
        except Exception as exc:
            raise HTTPException(
                503,
                "App unavailable. A mutation may already have executed; "
                "inspect state before retrying with a new ID.",
            ) from exc

    return await dispatch(
        app_id, payload.revision, "app", payload.model_dump(), perform, mutation=payload.method != "GET"
    )


async def capability(app_id: str, revision: int, payload: dict) -> Any:
    manifest = current(app_id, revision)
    try:
        serialized = json.dumps(payload, ensure_ascii=False, allow_nan=False)
    except (ValueError, RecursionError) as exc:
        raise HTTPException(422, "Provide bounded, finite JSON") from exc
    if len(serialized.encode()) > 48 * 1024:
        raise HTTPException(413, "Capability request exceeds 48 KiB")
    capabilities.authorize(manifest, payload)
    mutation = True
    if payload["kind"] == "mcp":
        tool = await capabilities.describe(app_id, payload)
        mutation = tool.get("annotations", {}).get("readOnlyHint") is not True

        async def perform():
            current(app_id, revision)  # Initialization may have awaited an archive or new release.
            return await capabilities.mcp(app_id, payload)
    else:
        mutation = payload["method"] != "GET"

        async def perform():
            current(app_id, revision)
            return await capabilities.https(payload)

    return await dispatch(app_id, revision, "capability", payload, perform, mutation=mutation)
