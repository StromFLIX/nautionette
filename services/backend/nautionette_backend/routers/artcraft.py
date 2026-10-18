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
    return {"apps": artcraft.apps(), "architecture": architecture()}


@router.get("/api/artcraft/apps/{app}/releases")
async def list_creative_app_releases(app: str, refresh: bool = False) -> dict[str, Any]:
    """Discover verified stable releases from the app's fixed upstream repository."""
    return {"versions": [item["version"] for item in await artcraft.releases(_app(app), refresh)]}


@router.put("/api/artcraft/apps/{app}")
async def configure_creative_app(app: str, payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
    """Enable an app or install an exact version; 'bundled' restores the image default.

    Installation is asynchronous: poll list_creative_apps until ready. Failed
    verification preserves the previous version. Do not claim a started update succeeded.
    """
    app = _app(app)
    pending = await artcraft.configure(app, payload)
    if pending:
        spawn(artcraft.finish_install(pending), name=f"artcraft-{pending['installation_id']}")
    return next(item for item in artcraft.apps() if item["id"] == app)
