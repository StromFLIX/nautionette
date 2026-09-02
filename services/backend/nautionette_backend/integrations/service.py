"""Adding, reading, removing and testing a model integration."""

from __future__ import annotations

import asyncio
import contextlib
from typing import Any

import httpx
from fastapi import HTTPException

from ..clients import gateway, upstream_problem
from ..db import db
from ..gateway_config import credential_state, gateway_problem, require_writable, resource_map
from ..runtime import forget_catalog, runtime
from .discovery import discover_models
from .registry import (
    INITIALIZED_SETTING,
    INSTANCE_PATTERN,
    INTEGRATION_TYPES,
    LEGACY_COPILOT_PREFIX,
    RESOURCE_PREFIX,
    instance_label,
    integration_context,
    integration_fields,
    integration_prefix,
    integration_spec,
    integration_type,
    render,
)
from .resources import (
    credential,
    discovery_resource_id,
    existing_credential,
    fetch_resources,
    forget_config,
    model_value,
    resource_id,
    stored_config,
    write,
)

_lock = asyncio.Lock()


def credential_hint(spec: dict[str, Any], key: str) -> str:
    state = credential_state(key, spec.get("auth", {}))
    if state["mode"] == "stored":
        return "the stored API key"
    return state["variable"] or "a key"


def _discovery_failure(spec: dict[str, Any], key: str, exc: Exception) -> dict[str, Any]:
    status = exc.response.status_code if isinstance(exc, httpx.HTTPStatusError) else None
    body = exc.response.text if isinstance(exc, httpx.HTTPStatusError) else ""
    return {
        "ok": False,
        "status": status,
        "message": upstream_problem(str(spec["name"]), credential_hint(spec, key), status, body),
    }


# ------------------------------------------------------------------ bootstrap


async def ensure_defaults() -> None:
    """Create the integrations that ship switched on, once, on a writable gateway."""
    if db.get_setting(INITIALIZED_SETTING, False):
        return
    async with _lock:
        if db.get_setting(INITIALIZED_SETTING, False):
            return
        mode, models, routes = await fetch_resources()
        if mode != "hybrid":
            return
        try:
            existing = resource_map(models)
            for type_id, spec in INTEGRATION_TYPES.items():
                if not spec.get("default"):
                    continue
                value = model_value(spec, type_id, {})
                if existing.get(value["id"], {}).get("value") != value:
                    await gateway.put_config_resources("llm.model", [value])

            # Copilot was once configured one model at a time; fold those into its integration.
            legacy = [
                resource["id"]
                for resource in models
                if isinstance(resource.get("id"), str)
                and resource["id"].startswith(LEGACY_COPILOT_PREFIX)
            ]
            if legacy:
                await write("copilot", "copilot", {}, models, routes)
                for identifier in legacy:
                    await gateway.delete_config_resource("llm.model", identifier)
        except httpx.HTTPError as exc:
            raise gateway_problem(exc) from exc
        db.set_setting(INITIALIZED_SETTING, True)
        forget_catalog()


async def bootstrap() -> None:
    """Retry the first-start setup until agentgateway is up, then stop."""
    for attempt in range(8):
        try:
            await ensure_defaults()
            if db.get_setting(INITIALIZED_SETTING, False):
                return
        except (HTTPException, httpx.HTTPError):
            pass
        await asyncio.sleep(min(0.25 * 2**attempt, 5.0))


# -------------------------------------------------------------------- reading


async def summary(instance: str, configured: bool, key: str = "") -> dict[str, Any]:
    type_id = integration_type(instance) or instance
    spec = INTEGRATION_TYPES[type_id]
    config = stored_config(instance) if configured else {}
    discovery: dict[str, Any] = {
        "ok": False,
        "status": None,
        "message": "Add this integration to discover its models.",
    }
    models: list[dict[str, Any]] = []
    if configured:
        try:
            models = await discover_models(instance)
            discovery = {"ok": True, "status": 200, "message": f"Discovered {len(models)} models."}
        except (httpx.HTTPError, ValueError, KeyError) as exc:
            discovery = _discovery_failure(spec, key, exc)
    prefix = integration_prefix(spec, integration_context(spec, config))
    return {
        "instance": instance,
        "type": type_id,
        "name": instance_label(instance) if configured else str(spec["name"]),
        "description": str(spec["description"]),
        "configured": configured,
        "multiple": bool(spec.get("multiple")),
        "model_count": len(models),
        "model_match": f"{prefix}/*" if prefix else "*",
        "credential": credential_state(key, spec.get("auth", {})),
        "fields": integration_fields(spec),
        "config": config,
        "discovery": discovery,
    }


async def payload() -> dict[str, Any]:
    """What is configured, what could be, and whether this gateway may be written to."""
    mode, models, _ = await fetch_resources()
    instances = sorted(
        instance
        for identifier in resource_map(models)
        if identifier.startswith(RESOURCE_PREFIX)
        and integration_type(instance := identifier.removeprefix(RESOURCE_PREFIX))
    )
    configured = await asyncio.gather(
        *(summary(instance, True, existing_credential(instance, models)) for instance in instances)
    )
    taken = {item["type"] for item in configured}
    available = await asyncio.gather(
        *(
            summary(type_id, False)
            for type_id, spec in INTEGRATION_TYPES.items()
            if spec.get("multiple") or type_id not in taken
        )
    )
    return {
        "integrations": list(configured),
        "available": list(available),
        "storage_mode": mode,
        "writable": mode == "hybrid",
    }


# -------------------------------------------------------------------- writing


async def save(target: str, body: dict[str, Any], config: dict[str, str]) -> str:
    """`target` is a type when adding, or an existing instance when reconfiguring."""
    type_id = integration_type(target) or target
    spec = integration_spec(type_id)
    instance = str(render(spec.get("instance", type_id), integration_context(spec, config)))
    if not INSTANCE_PATTERN.fullmatch(instance) or integration_type(instance) != type_id:
        raise HTTPException(status_code=400, detail="invalid integration name")

    await ensure_defaults()
    mode, models, routes = await fetch_resources()
    require_writable(mode)
    try:
        await write(type_id, instance, config, models, routes)
    except httpx.HTTPError as exc:
        if resource_id(instance) not in resource_map(models):
            # Never leave a route behind that the discovery half could not be written for.
            with contextlib.suppress(httpx.HTTPError):
                await gateway.delete_config_resource("llm.model", resource_id(instance))
        key = credential(spec, str(body.get("api_key") or ""), existing_credential(instance, models))
        raise gateway_problem(exc, key) from exc

    db.set_setting(INITIALIZED_SETTING, True)
    forget_catalog()
    return instance


async def remove(instance: str) -> None:
    if not integration_type(instance):
        raise HTTPException(status_code=404, detail="unknown model integration")
    mode, models, routes = await fetch_resources()
    require_writable(mode)
    try:
        if resource_id(instance) in resource_map(models):
            await gateway.delete_config_resource("llm.model", resource_id(instance))
        if discovery_resource_id(instance) in resource_map(routes):
            await gateway.delete_config_resource("traffic.route", discovery_resource_id(instance))
    except httpx.HTTPError as exc:
        raise gateway_problem(exc) from exc
    forget_config(instance)
    forget_catalog()


async def test(instance: str) -> dict[str, Any]:
    """Discover, then make one real generation, so auth and routing are both proven."""
    type_id = integration_type(instance)
    if not type_id:
        raise HTTPException(status_code=404, detail="unknown model integration")
    spec = INTEGRATION_TYPES[type_id]
    _, models, _ = await fetch_resources()
    if resource_id(instance) not in resource_map(models):
        raise HTTPException(status_code=409, detail=f"add {spec['name']} first")
    key = existing_credential(instance, models)
    try:
        discovered = await discover_models(instance)
    except (httpx.HTTPError, ValueError, KeyError) as exc:
        return _discovery_failure(spec, key, exc)
    if not discovered:
        return {"ok": False, "status": None, "message": "No chat models were discovered."}

    default_model = str(runtime("default_model") or "")
    model = next((item for item in discovered if item["id"] == default_model), discovered[0])
    try:
        return await gateway.test_model(model["id"], str(spec["name"]), credential_hint(spec, key))
    except httpx.HTTPError as exc:
        raise gateway_problem(exc) from exc
