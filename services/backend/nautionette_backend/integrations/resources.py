"""Turning an integration declaration into the resources agentgateway holds."""

from __future__ import annotations

import asyncio
from typing import Any

import httpx

from ..clients import gateway
from ..db import db
from ..gateway_config import gateway_problem, resource_map, storage_mode
from .registry import (
    CONFIG_SETTING,
    INTEGRATION_TYPES,
    RESOURCE_PREFIX,
    integration_context,
    integration_prefix,
    integration_type,
    render,
)


def resource_id(instance: str) -> str:
    return f"{RESOURCE_PREFIX}{instance}"


def discovery_resource_id(instance: str) -> str:
    return f"{resource_id(instance)}-discovery"


def stored_config(instance: str) -> dict[str, str]:
    stored = db.get_setting(f"{CONFIG_SETTING}{instance}", {})
    return stored if isinstance(stored, dict) else {}


def forget_config(instance: str) -> None:
    db.execute("DELETE FROM settings WHERE key = ?", (f"{CONFIG_SETTING}{instance}",))


def credential(spec: dict[str, Any], supplied: str, existing: str) -> str:
    """A typed key wins, then whatever the gateway already holds, then the declared variable."""
    auth = spec.get("auth", {})
    if auth.get("kind") != "key":
        return ""
    if supplied:
        return supplied
    if existing:
        return existing
    if auth.get("builtin"):
        return ""  # sending nothing lets agentgateway fall back to its own credential
    variable = auth.get("env", "")
    return f"${variable}" if variable else ""


def existing_credential(instance: str, models: list[dict[str, Any]]) -> str:
    """The key agentgateway already holds, so reconfiguring never needs it retyped."""
    resource = resource_map(models).get(resource_id(instance), {})
    return str((resource.get("value", {}).get("params") or {}).get("apiKey") or "")


def model_value(
    spec: dict[str, Any], instance: str, config: dict[str, str], key: str = ""
) -> dict[str, Any]:
    context = integration_context(spec, config)
    prefix = integration_prefix(spec, context)
    value: dict[str, Any] = {
        "id": resource_id(instance),
        "name": f"{prefix}/*" if prefix else "*",
        "provider": render(spec["provider"], context),
    }
    params = dict(render(spec.get("params", {}), context))
    if key:
        params["apiKey"] = key
    if params:
        value["params"] = params
    if prefix:
        value["transformation"] = {"model": f'llmRequest.model.stripPrefix("{prefix}/")'}
    headers = render(spec.get("headers", {}), context)
    if headers:
        value["requestHeaders"] = {"set": headers}
    return value


def discovery_value(
    spec: dict[str, Any], instance: str, config: dict[str, str], key: str = ""
) -> dict[str, Any] | None:
    """The route that lets the backend read a provider's own model list, credential included."""
    discovery = spec.get("discovery", {})
    if "host" not in discovery:
        return None
    context = integration_context(spec, config)
    policies: dict[str, Any] = {
        "urlRewrite": {"path": {"full": render(discovery["path"], context)}},
        "backendTLS": {},
    }
    headers = render({**spec.get("headers", {}), **discovery.get("headers", {})}, context)
    if headers:
        policies["requestHeaderModifier"] = {"set": headers}
    backend: dict[str, Any] = {"host": render(discovery["host"], context)}
    auth = spec.get("auth", {})
    if key:
        entry: dict[str, Any] = {"value": key}
        if auth.get("location"):
            entry["location"] = auth["location"]
        backend["policies"] = {"backendAuth": {"key": entry}}
    elif auth.get("builtin"):
        backend["policies"] = {"backendAuth": auth["builtin"]}
    return {
        "name": discovery_resource_id(instance),
        "gateways": ["default"],
        "matches": [
            {"path": {"exact": f"/_nautionette/integrations/{instance}/models"}, "method": "GET"}
        ],
        "policies": policies,
        "backends": [backend],
    }


def configured_instances(config: dict[str, Any]) -> list[str]:
    """Which integrations the gateway's effective config says are in place."""
    out: list[str] = []
    for route in config.get("model_routes") or []:
        identifier = route.get("id") or ""
        if identifier.startswith(RESOURCE_PREFIX):
            instance = identifier.removeprefix(RESOURCE_PREFIX)
            if integration_type(instance):
                out.append(instance)
    return out


async def fetch_resources() -> tuple[str, list[dict[str, Any]], list[dict[str, Any]]]:
    try:
        runtime_state, models, routes = await asyncio.gather(
            gateway.runtime(),
            gateway.config_resources("llm.model"),
            gateway.config_resources("traffic.route"),
        )
    except httpx.HTTPError as exc:
        raise gateway_problem(exc) from exc
    return storage_mode(runtime_state), models, routes


async def _upsert_if_changed(kind: str, value: dict[str, Any], resources: list[dict[str, Any]]) -> None:
    identifier = value.get("id") or value.get("name")
    current = resource_map(resources).get(identifier, {})
    if current.get("value") != value:
        await gateway.put_config_resources(kind, [value])


async def write(
    type_id: str,
    instance: str,
    config: dict[str, str],
    models: list[dict[str, Any]],
    routes: list[dict[str, Any]],
) -> None:
    spec = INTEGRATION_TYPES[type_id]
    key = credential(spec, config.pop("api_key", ""), existing_credential(instance, models))
    await _upsert_if_changed("llm.model", model_value(spec, instance, config, key), models)
    discovery = discovery_value(spec, instance, config, key)
    if discovery:
        await _upsert_if_changed("traffic.route", discovery, routes)
    # The key stays with agentgateway; only the visible fields are kept here.
    db.set_setting(f"{CONFIG_SETTING}{instance}", config)
