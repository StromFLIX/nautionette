"""The settings a user can change, and the catalog they choose from."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException

from .. import agent_profiles
from .. import catalog as catalog_service
from ..db import db
from ..events import bus
from ..git_authorship import DEFAULTS as GIT_AUTHORSHIP_DEFAULTS
from ..git_authorship import validate as validate_git_authorship
from ..reasoning import validate_effort
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
    baseline = defaults()
    values = {
        key: None if value in (None, "") else value for key, value in payload.items() if key in baseline
    }
    git_keys = GIT_AUTHORSHIP_DEFAULTS.keys() & values.keys()
    if git_keys:
        merged = {key: runtime(key) for key in GIT_AUTHORSHIP_DEFAULTS}
        merged.update({key: baseline[key] if values[key] is None else values[key] for key in git_keys})
        try:
            validated = validate_git_authorship(merged)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        values.update({key: validated[key] for key in git_keys if values[key] is not None})

    changes = agent_profiles.normalize(
        {
            key: baseline[setting] if values[setting] is None else values[setting]
            for key, setting in agent_profiles.SETTING_KEYS.items()
            if setting in values
        }
    )
    current = agent_profiles.global_config()
    if "model" in changes and changes["model"] != current["model"] and "reasoning_effort" not in changes:
        changes["reasoning_effort"] = None
        values["default_reasoning_effort"] = None
    merged = {**current, **changes}
    if {"model", "reasoning_effort"} & changes.keys() and merged["reasoning_effort"] is not None:
        try:
            validate_effort(merged["reasoning_effort"], await catalog_service.model_info(merged["model"]))
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
    values.update(
        {
            agent_profiles.SETTING_KEYS[key]: value
            for key, value in changes.items()
            if values.get(agent_profiles.SETTING_KEYS[key]) is not None
        }
    )
    if "history_chars" in values and values["history_chars"] is not None:
        try:
            if type(values["history_chars"]) not in (int, str):
                raise ValueError
            chars = int(values["history_chars"])
        except (ValueError, TypeError) as exc:
            raise HTTPException(422, "history_chars must be an integer") from exc
        values["history_chars"] = 0 if chars <= 0 else max(HISTORY_FLOOR, min(HISTORY_CEILING, chars))
    if values.get("default_agent_id") is not None:
        agent_profiles.get_agent(values["default_agent_id"])
    # No setting is written until the entire edit is valid. Arrays stay arrays.
    db.save_settings(values)
    forget_catalog()
    bus.publish("settings.changed", {})
    return _current()


@router.get("/api/catalog")
async def catalog(refresh: bool = False) -> dict[str, Any]:
    return await catalog_service.build(refresh)
