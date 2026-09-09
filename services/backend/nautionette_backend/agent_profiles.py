"""Reusable chat configurations, separate from the container's agent set.

Missing config keys inherit the instance defaults. Explicit null means all MCP
tools or provider-default reasoning; empty lists mean no tools/projects. Chats
copy the resolved values rather than following a mutable profile at execution.
"""

from __future__ import annotations

import json
import re
from typing import Any

from fastapi import HTTPException

from . import pi_packages, projects
from .db import db
from .runtime import runtime

CONFIG_KEYS = ("agent_set", "model", "reasoning_effort", "tools", "project_ids", "packages")
SETTING_KEYS = {key: f"default_{key}" for key in CONFIG_KEYS if key != "packages"}


def global_config() -> dict[str, Any]:
    return {**{key: runtime(setting) for key, setting in SETTING_KEYS.items()}, "packages": []}


def resolve(config: dict[str, Any], base: dict[str, Any] | None = None) -> dict[str, Any]:
    """Resolve only one level of inheritance; agents cannot inherit other agents."""
    base = global_config() if base is None else base
    result = {**base, **config}
    if result["model"] != base["model"] and "reasoning_effort" not in config:
        # An inherited effort belongs to the global model, not a different route.
        result["reasoning_effort"] = None
    return {key: list(value) if isinstance(value, list) else value for key, value in result.items()}


def normalize(config: Any) -> dict[str, Any]:
    """Validate explicit choices without turning empty selections into defaults."""
    if not isinstance(config, dict) or config.keys() - set(CONFIG_KEYS):
        raise HTTPException(
            422, "config must contain only agent_set, model, reasoning_effort, tools, project_ids, packages"
        )
    result = dict(config)
    if "agent_set" in result:
        value = result["agent_set"]
        if not isinstance(value, str) or not re.fullmatch(r"[a-z][a-z0-9-]{0,31}", value):
            raise HTTPException(422, "agent_set must name a container environment")
    if "model" in result:
        value = result["model"]
        if not isinstance(value, str) or not value.strip() or len(value) > 256:
            raise HTTPException(422, "model must be a non-empty model ID of at most 256 characters")
        result["model"] = value.strip()
    if "tools" in result:
        value = result["tools"]
        if value is not None:
            if (
                not isinstance(value, list)
                or len(value) > 2000
                or any(not isinstance(name, str) or not name.strip() or len(name) > 256 for name in value)
            ):
                raise HTTPException(422, "tools must be null (all tools) or a list of tool names")
            # Keep unavailable names pinned. A catalog outage must never expand access.
            result["tools"] = sorted(set(value))
    if "project_ids" in result:
        result["project_ids"] = projects.selection(result["project_ids"])
    if "packages" in result:
        result["packages"] = pi_packages.selection(result["packages"])
    return result


def list_agents() -> list[dict[str, Any]]:
    return [
        {**row, "config": json.loads(row["config"])}
        for row in db.query("SELECT * FROM agent_profiles ORDER BY name COLLATE NOCASE, id")
    ]


def get_agent(agent_id: Any) -> dict[str, Any]:
    if not isinstance(agent_id, str) or not agent_id:
        raise HTTPException(422, "agent_id must identify a saved agent or be null for global defaults")
    row = db.one("SELECT * FROM agent_profiles WHERE id = ?", (agent_id,))
    if not row:
        raise HTTPException(404, "Agent not found; choose another agent or global defaults")
    return {**row, "config": json.loads(row["config"])}


def for_chat(agent_id: Any) -> dict[str, Any]:
    agent = get_agent(agent_id) if agent_id is not None else None
    return {
        "agent_id": agent["id"] if agent else None,
        "agent_name": agent["name"] if agent else None,
        **resolve(agent["config"] if agent else {}),
    }


def catalog_entries() -> dict[str, Any]:
    base = global_config()
    agents = [{**agent, "resolved": resolve(agent["config"], base)} for agent in list_agents()]
    default_id = runtime("default_agent_id")
    selected = next((agent for agent in agents if agent["id"] == default_id), None)
    return {
        "agents": agents,
        "default_agent_id": selected["id"] if selected else None,
        "global_chat_defaults": base,
        "chat_defaults": {
            "agent_id": selected["id"] if selected else None,
            "agent_name": selected["name"] if selected else None,
            **(selected["resolved"] if selected else base),
        },
    }
