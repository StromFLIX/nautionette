"""The settings a user can change, and the catalog they choose from."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, Depends

from .. import catalog as catalog_service
from ..db import db
from ..events import bus
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
