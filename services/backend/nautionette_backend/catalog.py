"""What a chat can be pointed at: agent sets, models, MCP tools.

Both halves are attribution problems. A model is labelled with the integration
whose route agentgateway would actually pick for it, and a tool with the MCP
server it was federated from, so what the picker shows matches where a call goes.
"""

from __future__ import annotations

import asyncio
import contextlib
from typing import Any

from fastapi import HTTPException

from .clients import broker, gateway
from .config import settings
from .db import db
from .events import bus
from .gateway_config import attempt
from .integrations import configured_instances, discover_models, ensure_defaults, instance_label
from .integrations.registry import RESOURCE_PREFIX
from .runtime import cache_catalog, cached_catalog, context_hints, model_windows, runtime

EMPTY_CONFIG: dict[str, Any] = {
    "providers": [],
    "targets": [],
    "model_routes": [],
    "wildcard_models": False,
}


def route_for_model(model_id: str, config: dict[str, Any]) -> dict[str, Any] | None:
    """The route agentgateway itself would pick, so labels match where a call really goes."""
    winner: dict[str, Any] | None = None
    winner_score = -1
    for route in config.get("model_routes") or []:
        pattern = route.get("name", "")
        if not pattern or not route.get("provider"):
            continue
        if pattern == model_id:
            score = 10_000 + len(pattern)
        elif pattern.endswith("*") and model_id.startswith(pattern[:-1]):
            score = len(pattern) - 1
        else:
            continue
        if score > winner_score:
            winner, winner_score = route, score
    return winner


async def model_catalog(config: dict[str, Any]) -> list[dict[str, Any]]:
    """Models the gateway will serve, tagged with who owns them and who fronts them."""
    listed = await attempt(gateway.models(), [])
    instances = configured_instances(config)
    discoveries = await asyncio.gather(
        *(attempt(discover_models(instance), []) for instance in instances)
    )
    merged: dict[str, dict[str, Any]] = {}
    for model in [*({"id": item["id"]} for item in listed), *(m for d in discoveries for m in d)]:
        merged.setdefault(model["id"], {}).update(model)

    out = []
    for model in merged.values():
        # A leading "~" marks an always-latest alias, not a separate vendor.
        alias = model["id"].startswith("~")
        owner, separator, _ = model["id"].lstrip("~").partition("/")
        route = route_for_model(model["id"], config) or {}
        identifier = str(route.get("id") or "")
        serving = (
            identifier.removeprefix(RESOURCE_PREFIX)
            if identifier.startswith(RESOURCE_PREFIX)
            else ""
        )
        out.append(
            {
                "id": model["id"],
                "name": model.get("name") or model["id"],
                "provider": model.get("provider") or (owner if separator else "other"),
                # Attribution follows the winning route, so the label matches where calls go.
                "gateway": (
                    instance_label(serving) if serving else str(route.get("provider") or "gateway")
                ),
                "integration": serving or None,
                "context_length": model.get("context_length"),
                "alias": alias,
            }
        )
    return sorted(out, key=lambda model: (model["gateway"], model["provider"], model["id"]))


async def tool_catalog(config: dict[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Federated tools, each attributed to the MCP server it came from."""
    targets = config.get("targets") or []
    federated = await attempt(gateway.mcp_tools(), [])

    def prefixed(name: str) -> str:
        """agentgateway names a federated tool after the target it came from."""
        return next(
            (
                target["name"]
                for target in targets
                if any(name.startswith(f"{target['name']}{sep}") for sep in ("_", "-", ":"))
            ),
            "",
        )

    owner_of: dict[str, str] = {}
    # Asking every server directly is the fallback for a gateway that does not
    # prefix, and a remote server is slow, so only pay for it when a name needs it.
    if any(not prefixed(tool["name"]) for tool in federated):
        per_target = await asyncio.gather(
            *(attempt(gateway.mcp_tools(target["host"]), []) for target in targets)
        )
        for target, tools in zip(targets, per_target, strict=True):
            for tool in tools:
                owner_of[tool["name"]] = target["name"]

    def server_for(name: str) -> str:
        return owner_of.get(name) or prefixed(name) or "other"

    tools = [{**tool, "server": server_for(tool["name"])} for tool in federated]
    servers = [
        {
            "name": target["name"],
            "host": target["host"],
            "count": sum(1 for tool in tools if tool["server"] == target["name"]),
        }
        for target in targets
    ]
    if any(tool["server"] == "other" for tool in tools):
        servers.append(
            {
                "name": "other",
                "host": "",
                "count": sum(1 for tool in tools if tool["server"] == "other"),
            }
        )
    return tools, servers


async def build(refresh: bool = False) -> dict[str, Any]:
    if not refresh and (cached := cached_catalog()):
        return cached

    with contextlib.suppress(HTTPException):
        await ensure_defaults()
    config = await attempt(gateway.config(), dict(EMPTY_CONFIG))
    agent_sets, models, (tools, servers) = await asyncio.gather(
        attempt(broker.agent_sets(), []),
        model_catalog(config),
        tool_catalog(config),
    )
    model_windows.clear()
    model_windows.update(
        {model["id"]: model["context_length"] for model in models if model.get("context_length")}
    )
    return cache_catalog(
        {
            "agent_sets": agent_sets or [{"name": settings.default_agent_set, "ready": True}],
            "default_agent_set": runtime("default_agent_set"),
            "models": models,
            "default_model": runtime("default_model"),
            "gateways": sorted({model["gateway"] for model in models}),
            "tools": tools,
            "tool_servers": servers,
            "context": context_hints(),
        }
    )


async def reset_default_model(excluded: str | None = None) -> bool:
    """Point the default at something that still exists. True when it had to move."""
    config = await attempt(gateway.config(), dict(EMPTY_CONFIG))
    models = [model for model in await model_catalog(config) if model.get("integration") != excluded]
    ids = {model["id"] for model in models}
    if str(runtime("default_model") or "") in ids:
        return False
    replacement = settings.agent_model if settings.agent_model in ids else None
    replacement = replacement or (models[0]["id"] if models else None)
    if replacement:
        db.set_setting("default_model", replacement)
    else:
        db.execute("DELETE FROM settings WHERE key = ?", ("default_model",))
    bus.publish("settings.changed", {})
    return True
