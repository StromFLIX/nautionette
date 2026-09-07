"""The settings a user can change, and the catalog they choose from."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException

from .. import catalog as catalog_service
from ..db import db
from ..events import bus
from ..git_authorship import DEFAULTS as GIT_AUTHORSHIP_DEFAULTS
from ..git_authorship import validate as validate_git_authorship
from ..runtime import defaults, forget_catalog, runtime
from ..security import require_user

router = APIRouter(dependencies=[Depends(require_user)])

HISTORY_FLOOR = 2_000
HISTORY_CEILING = 2_000_000


def _current() -> dict[str, Any]:
    return {"settings": {key: runtime(key) for key in defaults()}, "defaults": defaults()}


@router.get("/api/settings")
async def get_settings() -> dict[str, Any]:
    return _current()


@router.put("/api/settings")
async def put_settings(payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
    git_keys = GIT_AUTHORSHIP_DEFAULTS.keys() & payload.keys()
    if git_keys:
        merged = {key: runtime(key) for key in GIT_AUTHORSHIP_DEFAULTS}
        merged.update(
            {
                key: GIT_AUTHORSHIP_DEFAULTS[key] if payload[key] in (None, "") else payload[key]
                for key in git_keys
            }
        )
        try:
            validated = validate_git_authorship(merged)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        payload = {
            **payload,
            **{key: validated[key] for key in git_keys if payload[key] not in (None, "")},
        }
    for key in defaults():
        if key not in payload:
            continue
        value = payload[key]
        if value in (None, ""):
            db.execute("DELETE FROM settings WHERE key = ?", (key,))
        elif key == "history_chars":
            chars = int(value)
            db.set_setting(key, 0 if chars <= 0 else max(HISTORY_FLOOR, min(HISTORY_CEILING, chars)))
        else:
            db.set_setting(key, str(value))
    forget_catalog()
    bus.publish("settings.changed", {})
    return _current()


@router.get("/api/catalog")
async def catalog(refresh: bool = False) -> dict[str, Any]:
    return await catalog_service.build(refresh)
