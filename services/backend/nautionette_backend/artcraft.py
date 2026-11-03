"""Verified creative-app artifacts, persistent update discovery and a bounded update queue."""

from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from typing import Any

import httpx
from fastapi import HTTPException
from nautionette.artcraft import (
    app_id,
    architecture,
    asset_name,
    bundled_selection,
    catalog,
    release,
    version,
)

from .clients import broker
from .clients.http import shared
from .db import db
from .events import bus

_PREFIX = "artcraft.app."
_releases: dict[str, tuple[float, list[dict[str, Any]]]] = {}
_UPDATE_KEY = "artcraft.updates"
CHECK_INTERVAL = 3600
log = logging.getLogger("nautionette.artcraft")


def updates(conn=None) -> dict[str, Any]:
    if conn is None:
        value = db.get_setting(_UPDATE_KEY, {})
    else:
        row = conn.execute("SELECT value FROM settings WHERE key = ?", (_UPDATE_KEY,)).fetchone()
        value = json.loads(row[0]) if row else {}
    return {"revision": 0, "automatic": False, "checking": False, "checked_at": None, "latest": {}, **value}


def _write_updates(conn, state) -> None:
    conn.execute(
        "INSERT INTO settings (key, value) VALUES (?,?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (_UPDATE_KEY, json.dumps(state)),
    )


def _version_key(value: str) -> tuple:
    base, _, suffix = value.partition("-")
    return (*map(int, base.split(".")), not bool(suffix))


def _read(app: str, conn=None) -> dict[str, Any]:
    if conn is None:
        value = db.get_setting(_PREFIX + app, {})
    else:
        row = conn.execute("SELECT value FROM settings WHERE key = ?", (_PREFIX + app,)).fetchone()
        value = json.loads(row[0]) if row else {}
    return {
        "revision": 0,
        "enabled": True,
        "active": None,
        "pending": None,
        "installing": False,
        "error": "",
        **value,
    }


def _write(conn, app: str, state: dict[str, Any]) -> None:
    conn.execute(
        "INSERT INTO settings (key, value) VALUES (?,?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (_PREFIX + app, json.dumps(state)),
    )


def apps() -> list[dict[str, Any]]:
    result = []
    discovery = updates()
    for app, item in catalog().items():
        state = _read(app)
        active = state["active"]
        current_version = active["release"]["version"] if active else item["version"]
        latest = discovery["latest"].get(app, {})
        latest_version = latest.get("release", {}).get("version")
        result.append(
            {
                "id": app,
                "name": item["name"],
                "description": item["description"],
                "repository": f"https://github.com/storytold/{app}",
                "bundled_version": item["version"],
                "version": current_version,
                "latest_version": latest_version,
                "update_available": bool(
                    latest_version
                    and not latest.get("error")
                    and _version_key(latest_version) > _version_key(current_version)
                ),
                "update_error": latest.get("error", ""),
                "checked_at": latest.get("checked_at"),
                "bundled": active is None,
                "enabled": state["enabled"],
                "revision": state["revision"],
                "status": ("installing" if state["installing"] else "queued")
                if state["pending"]
                else "ready",
                "pending_version": state["pending"]["release"]["version"] if state["pending"] else None,
                "error": state["error"],
            }
        )
    return result


def for_run() -> list[dict[str, Any]]:
    # Read a coherent snapshot before handing the call to the broker. Updating
    # settings never replaces files already mounted by an active turn.
    with db.transaction() as conn:
        result = []
        for app in catalog():
            state = _read(app, conn)
            if state["enabled"]:
                result.append(state["active"] or bundled_selection(app))
        return result


def _asset(app: str, data: dict[str, Any]) -> dict[str, str]:
    selected = version(str(data.get("tag_name", "")).removeprefix("v"))
    arch = architecture()
    name = asset_name(app, selected, arch)
    asset = next((item for item in data.get("assets", []) if item.get("name") == name), {})
    digest = str(asset.get("digest", ""))
    if not digest.startswith("sha256:"):
        raise ValueError("Release has no SHA-256-verified native CLI for this server architecture")
    return release({"app": app, "version": selected, "arch": arch, "sha256": digest[7:]})


async def releases(app: str, refresh: bool = False) -> list[dict[str, Any]]:
    app_id(app)
    cached = _releases.get(app)
    if not refresh and cached and time.monotonic() - cached[0] < 300:
        return cached[1]
    try:
        response = await shared().get(
            f"https://api.github.com/repos/storytold/{app}/releases",
            params={"per_page": 20},
            headers={"Accept": "application/vnd.github+json", "User-Agent": "Nautionette-ArtCraft"},
            timeout=15,
        )
        response.raise_for_status()
        items = []
        for data in response.json():
            if data.get("draft") or data.get("prerelease"):
                continue
            try:
                spec = _asset(app, data)
            except ValueError:
                continue
            if "-" not in spec["version"]:
                items.append({"version": spec["version"], "release": spec})
        items.sort(key=lambda item: _version_key(item["version"]), reverse=True)
    except (httpx.HTTPError, ValueError, TypeError, AttributeError) as exc:
        raise HTTPException(
            502, "ArtCraft release lookup is unavailable or rate-limited; retry later"
        ) from exc
    _releases[app] = (time.monotonic(), items)
    return items


async def resolve(app: str, selected: str) -> dict[str, str]:
    for item in await releases(app):
        if item["version"] == selected:
            return item["release"]
    # Support older pins without an unbounded release-list scan.
    try:
        response = await shared().get(
            f"https://api.github.com/repos/storytold/{app}/releases/tags/v{selected}",
            headers={"Accept": "application/vnd.github+json", "User-Agent": "Nautionette-ArtCraft"},
            timeout=15,
        )
        if response.status_code == 404:
            raise HTTPException(422, "That ArtCraft release does not exist")
        response.raise_for_status()
        data = response.json()
        if data.get("draft"):
            raise ValueError("Draft releases cannot be installed")
        spec = _asset(app, data)
        if spec["version"] != selected:
            raise ValueError("Release tag does not match the requested version")
        return spec
    except (httpx.HTTPError, ValueError, TypeError, AttributeError) as exc:
        raise HTTPException(
            422, "Release is unavailable or lacks a verified native CLI for this server"
        ) from exc


async def configure(app: str, payload: dict[str, Any]) -> dict[str, Any] | None:
    app_id(app)
    if not payload.keys() & {"enabled", "version"} or payload.keys() - {
        "enabled",
        "version",
        "expected_revision",
    }:
        raise HTTPException(422, "Provide enabled and/or version, with optional expected_revision")
    if "enabled" in payload and type(payload["enabled"]) is not bool:
        raise HTTPException(422, "enabled must be a boolean")
    if "expected_revision" in payload and type(payload["expected_revision"]) is not int:
        raise HTTPException(422, "expected_revision must be an integer")
    selected = payload.get("version")
    if "version" in payload and selected is None:
        raise HTTPException(422, "version must be an exact release version or 'bundled'")
    spec = None
    if selected is not None and selected != "bundled":
        try:
            version(selected)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        current = _read(app)
        if not current["active"] or current["active"]["release"]["version"] != selected:
            spec = await resolve(app, selected)
    with db.transaction() as conn:
        state = _read(app, conn)
        if "expected_revision" in payload and state["revision"] != payload["expected_revision"]:
            raise HTTPException(409, "App settings changed; reload before saving")
        if state["pending"]:
            raise HTTPException(409, "This app update is still being verified; wait before changing it")
        if spec:
            state["pending"] = {"app": app, "release": spec, "installation_id": uuid.uuid4().hex}
            state["installing"] = False
        if selected == "bundled":
            state["active"] = None
        state.update(
            enabled=payload.get("enabled", state["enabled"]), error="", revision=state["revision"] + 1
        )
        _write(conn, app, state)
    bus.publish("artcraft.changed", {"app": app})
    return state["pending"]


async def finish_install(pending: dict[str, Any]) -> bool:
    error, retry = "", False
    try:
        result = await broker.install_artcraft(pending["installation_id"], pending["release"])
        if result != pending["release"]:
            raise ValueError("Installer returned a different release")
    except Exception as exc:
        retry = isinstance(exc, httpx.TransportError)
        if isinstance(exc, httpx.HTTPStatusError):
            retry = exc.response.status_code == 503
            if exc.response.status_code == 422:
                # Fixed broker messages only; never expose arbitrary upstream logs.
                try:
                    detail = str(exc.response.json().get("detail", ""))
                except (ValueError, AttributeError):
                    detail = ""
                retry = any(
                    message in detail
                    for message in (
                        "already being prepared",
                        "Two ArtCraft installations",
                        "Agent image is not ready",
                    )
                )
        error = (
            "Waiting for the app installer. The working version is unchanged; this queued update will retry."
            if retry
            else "Update failed verification. The previous version is unchanged. "
            "Check image readiness, release compatibility and available disk space, then retry."
        )
    with db.transaction() as conn:
        state = _read(pending["app"], conn)
        if state["pending"] != pending:
            return True
        if not error:
            state["active"] = pending
        state.update(
            pending=pending if retry else None, installing=False, error=error, revision=state["revision"] + 1
        )
        _write(conn, pending["app"], state)
    bus.publish("artcraft.changed", {"app": pending["app"]})
    return not retry


def recover_installations() -> None:
    with db.transaction() as conn:
        discovery = updates(conn)
        discovery["checking"] = False
        _write_updates(conn, discovery)
        for app in catalog():
            state = _read(app, conn)
            if state["pending"]:
                # The fixed broker installer can verify/reuse a completed volume
                # after a lost response. Keep the exact release and artifact ID.
                state.update(installing=False, revision=state["revision"] + 1)
                _write(conn, app, state)


def begin_check() -> bool:
    with db.transaction() as conn:
        state = updates(conn)
        if state["checking"]:
            return False
        state["checking"] = True
        _write_updates(conn, state)
    return True


async def check_updates() -> None:
    """Called after begin_check. Persist partial results; outages are not 'up to date'."""
    slots = asyncio.Semaphore(3)

    async def check(app):
        async with slots:
            error, spec = "", None
            try:
                available = await releases(app, refresh=True)
                if not available:
                    raise ValueError("No verified stable releases")
                spec = available[0]["release"]
            except (HTTPException, ValueError):
                error = (
                    "Could not check verified stable releases. "
                    "Upstream may be unavailable or rate-limited; retry later."
                )
            with db.transaction() as conn:
                state = updates(conn)
                previous = state["latest"].get(app, {})
                state["latest"][app] = {
                    **previous,
                    **({"release": spec} if spec else {}),
                    "checked_at": time.time(),
                    "error": error,
                }
                _write_updates(conn, state)
            bus.publish("artcraft.changed", {"app": app})

    try:
        await asyncio.gather(*(check(app) for app in catalog()))
    finally:
        with db.transaction() as conn:
            state = updates(conn)
            state.update(checking=False, checked_at=time.time())
            _write_updates(conn, state)
    # Re-read the preference: disabling auto-update while discovery is running
    # must not enqueue new updates. Already queued work intentionally continues.
    enqueue_updates(automatic_only=True)
    await process_queue()


def configure_updates(payload: dict[str, Any]) -> None:
    if (
        set(payload) != {"automatic", "expected_revision"}
        or type(payload["automatic"]) is not bool
        or type(payload["expected_revision"]) is not int
    ):
        raise HTTPException(422, "Provide automatic (boolean) and expected_revision (integer)")
    with db.transaction() as conn:
        state = updates(conn)
        if state["revision"] != payload["expected_revision"]:
            raise HTTPException(409, "Update preferences changed; reload before saving")
        state.update(automatic=payload["automatic"], revision=state["revision"] + 1)
        _write_updates(conn, state)
    bus.publish("artcraft.changed", {})


def enqueue_updates(*, automatic_only: bool = False) -> list[str]:
    queued = []
    with db.transaction() as conn:
        discovery = updates(conn)
        if automatic_only and not discovery["automatic"]:
            return queued
        for app in catalog():
            latest = discovery["latest"].get(app, {})
            spec = latest.get("release")
            if not spec or latest.get("error"):
                continue
            state = _read(app, conn)
            current = (state["active"] or bundled_selection(app))["release"]["version"]
            if state["pending"] or _version_key(spec["version"]) <= _version_key(current):
                continue
            state.update(
                pending={"app": app, "release": spec, "installation_id": uuid.uuid4().hex},
                installing=False,
                error="",
                revision=state["revision"] + 1,
            )
            _write(conn, app, state)
            queued.append(app)
    bus.publish("artcraft.changed", {})
    return queued


async def process_queue() -> None:
    """Database claims bound all request/maintenance dispatchers to two installers."""
    while True:
        selected = []
        with db.transaction() as conn:
            states = [(app, _read(app, conn)) for app in catalog()]
            capacity = 2 - sum(bool(state["installing"]) for _, state in states)
            for app, state in states:
                if len(selected) >= capacity:
                    break
                if state["pending"] and not state["installing"]:
                    state["installing"] = True
                    _write(conn, app, state)
                    selected.append(state["pending"])
        if not selected:
            return
        bus.publish("artcraft.changed", {})
        completed = await asyncio.gather(*(finish_install(pending) for pending in selected))
        if not all(completed):
            return  # maintenance retries delayed jobs, without a busy request loop


async def maintenance() -> None:
    """Discovery works even with Settings closed; opt-in updates run hourly."""
    while True:
        try:
            state = updates()
            if (
                not state["checking"]
                and (not state["checked_at"] or time.time() - state["checked_at"] >= CHECK_INTERVAL)
                and begin_check()
            ):
                await check_updates()
            await process_queue()
        except Exception:
            log.exception("Creative-app maintenance failed; will retry")
        await asyncio.sleep(30)
