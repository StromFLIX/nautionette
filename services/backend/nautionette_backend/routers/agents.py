"""Saved agents: named, inheritable configurations for new or explicitly switched chats."""

from __future__ import annotations

import json
import sqlite3
import time
import uuid
from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException

from .. import agent_profiles, catalog
from ..db import db
from ..events import bus
from ..reasoning import validate_effort
from ..runtime import forget_catalog
from ..security import require_user

router = APIRouter(dependencies=[Depends(require_user)])


def _changed(agent_id: str) -> None:
    forget_catalog()
    bus.publish("agent.profile.changed", {"agent_id": agent_id})


def _result(agent: dict[str, Any]) -> dict[str, Any]:
    return {**agent, "resolved": agent_profiles.resolve(agent["config"])}


async def _validated(payload: dict[str, Any], current: dict[str, Any] | None = None) -> dict[str, Any]:
    if payload.keys() - {"name", "description", "config"}:
        raise HTTPException(422, "An agent accepts name, description and config")
    values = {**(current or {"description": "", "config": {}}), **payload}
    name = values.get("name")
    if not isinstance(name, str) or not name.strip() or len(name.strip()) > 80:
        raise HTTPException(422, "Agent name must contain 1–80 characters")
    if name.strip().casefold() == "global defaults":
        raise HTTPException(422, "Choose a name other than Global defaults")
    description = values["description"]
    if not isinstance(description, str) or len(description) > 500:
        raise HTTPException(422, "Agent description must be at most 500 characters")
    config = values["config"]
    if current is None or "config" in payload:
        config = agent_profiles.normalize(config)
        resolved = agent_profiles.resolve(config)
        if resolved["reasoning_effort"] is not None:
            try:
                validate_effort(resolved["reasoning_effort"], await catalog.model_info(resolved["model"]))
            except ValueError as exc:
                raise HTTPException(422, str(exc)) from exc
    return {"name": name.strip(), "description": description.strip(), "config": config}


@router.get("/api/agents")
async def list_agents() -> dict[str, Any]:
    return agent_profiles.catalog_entries()


@router.post("/api/agents", status_code=201)
async def create_agent(payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
    values = await _validated(payload)
    agent_id = uuid.uuid4().hex[:12]
    now = time.time()
    try:
        db.execute(
            "INSERT INTO agent_profiles (id, name, description, config, created_at, updated_at) "
            "VALUES (?,?,?,?,?,?)",
            (agent_id, values["name"], values["description"], json.dumps(values["config"]), now, now),
        )
    except sqlite3.IntegrityError as exc:
        raise HTTPException(409, "An agent with that name already exists") from exc
    _changed(agent_id)
    return _result(agent_profiles.get_agent(agent_id))


@router.get("/api/agents/{agent_id}")
async def get_agent(agent_id: str) -> dict[str, Any]:
    return _result(agent_profiles.get_agent(agent_id))


@router.patch("/api/agents/{agent_id}")
async def update_agent(agent_id: str, payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
    """Update metadata or replace config. Omitted config keys inherit global defaults."""
    values = await _validated(payload, agent_profiles.get_agent(agent_id))
    try:
        changed = db.execute(
            "UPDATE agent_profiles SET name = ?, description = ?, config = ?, updated_at = ? WHERE id = ?",
            (values["name"], values["description"], json.dumps(values["config"]), time.time(), agent_id),
        ).rowcount
    except sqlite3.IntegrityError as exc:
        raise HTTPException(409, "An agent with that name already exists") from exc
    if not changed:
        raise HTTPException(404, "Agent not found")
    _changed(agent_id)
    return _result(agent_profiles.get_agent(agent_id))


@router.delete("/api/agents/{agent_id}")
async def delete_agent(agent_id: str) -> dict[str, bool]:
    agent_profiles.get_agent(agent_id)
    db.delete_agent(agent_id)
    _changed(agent_id)
    return {"ok": True}
