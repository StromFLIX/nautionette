"""Authenticated creative-app version management (not a generic container runner)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException
from nautionette.artcraft import app_id, architecture

from .. import artcraft
from ..background import spawn
from ..security import require_user

router = APIRouter(dependencies=[Depends(require_user)])


def _app(value: str) -> str:
    try:
        return app_id(value)
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc


@router.get("/api/artcraft/apps")
async def list_creative_apps() -> dict[str, Any]:
    """List native creative apps, selected versions and update status.

    Changes apply to new agent turns. Existing turns retain their pinned binaries.
    """
    state = artcraft.updates()
    return {
        "apps": artcraft.apps(),
        "architecture": architecture(),
        "updates": {key: value for key, value in state.items() if key != "latest"},
    }


@router.post("/api/artcraft/updates/check")
async def check_creative_app_updates() -> dict[str, Any]:
    """Refresh all release statuses in the background without enabling updates."""
    if artcraft.begin_check():
        spawn(artcraft.check_updates(), name="artcraft-release-check")
    return await list_creative_apps()


@router.put("/api/artcraft/updates")
async def configure_creative_app_updates(payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
    """Opt into hourly verified stable updates for all apps, including disabled ones.

    This replaces manual version pins. Disabling does not cancel already queued updates.
    """
    artcraft.configure_updates(payload)
    if payload["automatic"] and artcraft.begin_check():
        spawn(artcraft.check_updates(), name="artcraft-auto-update-check")
    return await list_creative_apps()


@router.post("/api/artcraft/updates/apply")
async def update_all_creative_apps() -> dict[str, Any]:
    """Queue every known newer verified stable release, with at most two installers.

    Disabled apps stay disabled. Failed or unknown discovery states are skipped.
    """
    queued = artcraft.enqueue_updates()
    spawn(artcraft.process_queue(), name="artcraft-update-queue")
    return {**await list_creative_apps(), "queued": queued}


@router.get("/api/artcraft/apps/{app}/releases")
async def list_creative_app_releases(app: str, refresh: bool = False) -> dict[str, Any]:
    """Discover verified stable releases from the app's fixed upstream repository."""
    return {"versions": [item["version"] for item in await artcraft.releases(_app(app), refresh)]}


@router.put("/api/artcraft/apps/{app}")
async def configure_creative_app(app: str, payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
    """Enable an app or install an exact version; 'bundled' restores the deployment default.

    Installation is asynchronous: poll list_creative_apps until ready. Failed
    verification preserves the previous version. Do not claim a started update succeeded.
    """
    app = _app(app)
    pending = await artcraft.configure(app, payload)
    if pending:
        spawn(artcraft.process_queue(), name="artcraft-update-queue")
    return next(item for item in artcraft.apps() if item["id"] == app)
