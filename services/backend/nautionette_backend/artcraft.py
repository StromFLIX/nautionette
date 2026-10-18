"""Instance-managed native creative apps. Defaults are bundled; updates activate atomically."""

from __future__ import annotations

import json
import time
import uuid
from typing import Any

import httpx
from fastapi import HTTPException
from nautionette.artcraft import app_id, architecture, asset_name, bundled_release, catalog, release, version

from .clients import broker
from .clients.http import shared
from .db import db
from .events import bus

_PREFIX = "artcraft.app."
_releases: dict[str, tuple[float, list[dict[str, Any]]]] = {}


def _read(app: str, conn=None) -> dict[str, Any]:
    if conn is None:
        value = db.get_setting(_PREFIX + app, {})
    else:
        row = conn.execute("SELECT value FROM settings WHERE key = ?", (_PREFIX + app,)).fetchone()
        value = json.loads(row[0]) if row else {}
    return {"revision": 0, "enabled": True, "active": None, "pending": None, "error": "", **value}


def _write(conn, app: str, state: dict[str, Any]) -> None:
    conn.execute(
        "INSERT INTO settings (key, value) VALUES (?,?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (_PREFIX + app, json.dumps(state)),
    )


def apps() -> list[dict[str, Any]]:
    result = []
    for app, item in catalog().items():
        state = _read(app)
        active = state["active"]
        result.append(
            {
                "id": app,
                "name": item["name"],
                "description": item["description"],
                "repository": f"https://github.com/storytold/{app}",
                "bundled_version": item["version"],
                "version": active["release"]["version"] if active else item["version"],
                "bundled": active is None,
                "enabled": state["enabled"],
                "revision": state["revision"],
                "status": "installing" if state["pending"] else "ready",
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
                result.append(state["active"] or {"app": app, "release": bundled_release(app)})
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
            items.append({"version": spec["version"], "release": spec})
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
            pending = sum(bool(_read(other, conn)["pending"]) for other in catalog())
            if pending >= 2:
                raise HTTPException(409, "Two app updates are already running; retry shortly")
            state["pending"] = {"app": app, "release": spec, "installation_id": uuid.uuid4().hex}
        if selected == "bundled":
            state["active"] = None
        state.update(
            enabled=payload.get("enabled", state["enabled"]), error="", revision=state["revision"] + 1
        )
        _write(conn, app, state)
    bus.publish("artcraft.changed", {"app": app})
    return state["pending"]


async def finish_install(pending: dict[str, Any]) -> None:
    error = ""
    try:
        result = await broker.install_artcraft(pending["installation_id"], pending["release"])
        if result != pending["release"]:
            raise ValueError("Installer returned a different release")
    except Exception:
        error = (
            "Update failed verification. The previous version is unchanged. "
            "Check image readiness, release compatibility and available disk space, then retry."
        )
    with db.transaction() as conn:
        state = _read(pending["app"], conn)
        if state["pending"] != pending:
            return
        if not error:
            state["active"] = pending
        state.update(pending=None, error=error, revision=state["revision"] + 1)
        _write(conn, pending["app"], state)
    bus.publish("artcraft.changed", {"app": pending["app"]})


def recover_installations() -> None:
    with db.transaction() as conn:
        for app in catalog():
            state = _read(app, conn)
            if state["pending"]:
                state.update(
                    pending=None,
                    error="Update interrupted by restart; previous version unchanged. Retry the update.",
                    revision=state["revision"] + 1,
                )
                _write(conn, app, state)
