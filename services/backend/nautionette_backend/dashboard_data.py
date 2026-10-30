"""Bounded, deterministic data feeds. Active views refresh; no model/notification loop."""

from __future__ import annotations

import asyncio
import json
import re
import time
from typing import Any
from weakref import WeakValueDictionary

import jsonschema
from fastapi import HTTPException

from . import dashboards
from .clients import gateway
from .dashboard_schema import Manifest, Source, bounded, check_schema, pointer, props_schema
from .db import db
from .events import SSE_HEARTBEAT, bus, sse

_locks: WeakValueDictionary[str, asyncio.Lock] = WeakValueDictionary()
_SECRET = re.compile(
    r"(?:password|secret|credential|authorization|access.?token|api.?key|private.?key)", re.I
)


def lock(key: str) -> asyncio.Lock:
    existing = _locks.get(key)
    if existing is None:
        existing = asyncio.Lock()
        _locks[key] = existing
    return existing


def public_data(value: Any, depth=0) -> Any:
    if depth > 16:
        raise ValueError("Data nesting limit")
    if isinstance(value, dict):
        return {
            key: "[redacted]" if _SECRET.search(key) else public_data(item, depth + 1)
            for key, item in value.items()
            if key not in {"__proto__", "constructor", "prototype"}
        }
    if isinstance(value, list):
        return [public_data(item, depth + 1) for item in value]
    return value


def shape(source: Source, value: Any) -> Any:
    value = pointer(value, source.select)
    if source.fields:

        def project(item):
            return {key: pointer(item, path) for key, path in source.fields.items()}

        value = [project(item) for item in value] if isinstance(value, list) else project(value)
    value = public_data(value)
    bounded(value, 65536)
    jsonschema.validate(value, source.output_schema)
    return value


def cached(dashboard_id: str, source_id: str, source: Source) -> dict[str, Any] | None:
    row = db.one(
        "SELECT * FROM dashboard_sources WHERE dashboard_id = ? AND source_id = ?", (dashboard_id, source_id)
    )
    return row if row and row["fingerprint"] == dashboards.digest(source.model_dump()) else None


def store(
    dashboard_id: str, source_id: str, source: Source, data: Any, observed_at: float | None, error: str = ""
) -> None:
    db.execute(
        "INSERT INTO dashboard_sources VALUES (?,?,?,?,?,?,?) ON CONFLICT(dashboard_id,source_id) "
        "DO UPDATE SET fingerprint=excluded.fingerprint, data=excluded.data, "
        "observed_at=excluded.observed_at, "
        "attempted_at=excluded.attempted_at, error=excluded.error",
        (
            dashboard_id,
            source_id,
            dashboards.digest(source.model_dump()),
            dashboards.encoded(data) if observed_at is not None else None,
            observed_at,
            time.time(),
            error,
        ),
    )
    bus.publish("dashboard.data", {"dashboard_id": dashboard_id, "source_id": source_id})


def source_view(dashboard_id: str, source_id: str, source: Source, agent_id: str | None) -> dict[str, Any]:
    if source.kind == "literal":
        try:
            return {"state": "static", "data": shape(source, source.data), "error": "", "observed_at": None}
        except (ValueError, jsonschema.ValidationError):
            return {"state": "error", "data": None, "error": "Literal data does not match its contract"}
    if source.kind == "tool":
        try:
            dashboards.permit(agent_id, source.tool)
        except HTTPException:
            return {"state": "denied", "data": None, "error": "Owning agent no longer has source access"}
    row = cached(dashboard_id, source_id, source)
    if not row:
        return {"state": "empty", "data": None, "error": "Waiting for source data", "observed_at": None}
    age = time.time() - row["observed_at"] if row["observed_at"] is not None else None
    state = "error" if row["error"] else ("fresh" if age is not None else "empty")
    if age is not None and age >= source.stale_seconds:
        state = "stale"
    return {
        "state": state,
        "data": json.loads(row["data"]) if row["data"] is not None else None,
        "observed_at": row["observed_at"],
        "attempted_at": row["attempted_at"],
        "error": row["error"] or ("Waiting for source data" if state == "empty" else ""),
        "stale_after": row["observed_at"] + source.stale_seconds if row["observed_at"] else None,
    }


async def refresh(dashboard: dict[str, Any], force=False) -> None:
    manifest = Manifest.model_validate(dashboard["manifest"])
    slots = asyncio.Semaphore(4)

    async def one(source_id: str, source: Source):
        if source.kind not in {"tool", "workflow-result"}:
            return
        async with lock(f"source:{dashboard['id']}:{source_id}"):
            previous = cached(dashboard["id"], source_id, source)
            if previous and time.time() - previous["attempted_at"] < (5 if force else source.refresh_seconds):
                return
            try:
                async with asyncio.timeout(25), slots:
                    if source.kind == "tool":
                        dashboards.permit(manifest.agent_id, source.tool)
                        tools = {
                            item["name"]: item for item in await dashboards.read_tools(manifest.agent_id)
                        }
                        tool = tools.get(source.tool)
                        if not tool:
                            raise PermissionError("Source declaration unavailable")
                        check_schema(tool["inputSchema"], local_refs=True)
                        jsonschema.validate(source.arguments, tool["inputSchema"])
                        value = await gateway.call_read_tool(source.tool, source.arguments)
                        observed_at = time.time()
                    else:
                        rows = db.list_runs(source.workflow, limit=1)
                        if not rows:
                            store(dashboard["id"], source_id, source, None, None)
                            return
                        row = rows[0]
                        value = row
                        observed_at = row["updated_at"]
                    value = shape(source, value)
                store(dashboard["id"], source_id, source, value, observed_at)
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 - never echo provider errors/configuration to a card
                store(
                    dashboard["id"],
                    source_id,
                    source,
                    json.loads(previous["data"]) if previous and previous["data"] else None,
                    previous["observed_at"] if previous else None,
                    "Source refresh failed; inspect the integration, permissions and data contract",
                )

    await asyncio.gather(*(one(key, source) for key, source in manifest.sources.items()))


def publish(dashboard: dict[str, Any], source_id: str, data: Any) -> dict[str, Any]:
    manifest = Manifest.model_validate(dashboard["manifest"])
    source = manifest.sources.get(source_id)
    if not source or source.kind != "snapshot":
        raise HTTPException(422, "Publish only to a registered snapshot source")
    try:
        value = shape(source, data)
    except (ValueError, jsonschema.ValidationError):
        raise HTTPException(
            422, "Snapshot exceeds limits or does not match the registered output schema"
        ) from None
    # Do not let a workflow publish into a different source definition after an edit.
    with db.transaction() as conn:
        current = conn.execute(
            "SELECT revision, archived FROM dashboards WHERE id = ?", (dashboard["id"],)
        ).fetchone()
        if not current or current["archived"] or current["revision"] != dashboard["revision"]:
            raise HTTPException(409, "Dashboard changed; re-read before publishing data")
        # store uses the same reentrant lock; perform SQL here to keep one commit.
        conn.execute(
            "INSERT INTO dashboard_sources VALUES (?,?,?,?,?,?,?) ON CONFLICT(dashboard_id,source_id) "
            "DO UPDATE SET fingerprint=excluded.fingerprint, data=excluded.data, "
            "observed_at=excluded.observed_at, attempted_at=excluded.attempted_at, error=''",
            (
                dashboard["id"],
                source_id,
                dashboards.digest(source.model_dump()),
                dashboards.encoded(value),
                time.time(),
                time.time(),
                "",
            ),
        )
    bus.publish("dashboard.data", {"dashboard_id": dashboard["id"], "source_id": source_id})
    return source_view(dashboard["id"], source_id, source, manifest.agent_id)


def render_widgets(dashboard: dict[str, Any], sources: dict[str, Any]) -> list[dict[str, Any]]:
    widgets = []
    manifest = dashboard["manifest"]
    for widget in manifest["widgets"]:
        rendered = []
        error = ""
        try:
            for part in dashboards.parts(widget, manifest["agent_id"]):
                props = dict(part["props"])
                for key, path in part["bindings"].items():
                    props[key] = pointer(sources.get(widget["source"], {}).get("data"), path)
                jsonschema.validate(props, props_schema(part["component"]))
                rendered.append({"component": part["component"], "props": props})
        except (HTTPException, ValueError, jsonschema.ValidationError):
            error = (
                "Component data or registration is unavailable; the agent can inspect and repair this card"
            )
        widgets.append({**widget, "parts": rendered, "error": error})
    return widgets


async def stream(dashboard_id: str):
    """Refresh authorized reads only while a view is open; push snapshots over SSE."""
    async with bus.subscribe() as queue:
        previous = ""
        while True:
            try:
                value = await snapshot(dashboard_id)
            except HTTPException as exc:
                yield sse({"type": "unavailable", "status": exc.status_code})
                return
            fingerprint = dashboards.digest(
                {key: value[key] for key in ("revision", "widgets", "sources", "actions", "runs")}
            )
            if fingerprint != previous:
                yield sse({"type": "snapshot", "snapshot": value})
                previous = fingerprint
            else:
                yield SSE_HEARTBEAT
            # Drain events caused by this refresh, then wake promptly for genuine writes.
            while not queue.empty():
                queue.get_nowait()
            deadline = time.monotonic() + 15
            while True:
                try:
                    event = await asyncio.wait_for(queue.get(), max(0.01, deadline - time.monotonic()))
                except TimeoutError:
                    break
                if event.get("kind") in {"connected", "settings.changed", "agent.profile.changed"} or (
                    event.get("dashboard_id") == dashboard_id or event.get("kind", "").startswith("run.")
                ):
                    break


async def snapshot(dashboard_id: str, force=False) -> dict[str, Any]:
    dashboard = dashboards.get(dashboard_id)
    await refresh(dashboard, force)
    # A slow provider read must not return a superseded manifest.
    current = dashboards.get(dashboard_id)
    manifest = Manifest.model_validate(current["manifest"])
    sources = {
        key: source_view(dashboard_id, key, source, manifest.agent_id)
        for key, source in manifest.sources.items()
    }
    from .dashboard_actions import availability, history  # keep data/actions independently testable

    actions = await availability(current, sources)
    return {
        **current,
        "widgets": render_widgets(current, sources),
        "sources": sources,
        "actions": actions,
        "runs": history(dashboard_id),
        "observed_at": time.time(),
        "refresh_seconds": 15,
    }
