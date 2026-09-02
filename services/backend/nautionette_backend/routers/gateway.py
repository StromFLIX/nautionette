"""Model integrations and MCP servers: what the app writes into agentgateway."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, Depends

from .. import integrations, mcp_servers
from ..catalog import reset_default_model
from ..events import bus
from ..fields import normalise
from ..integrations.registry import integration_fields, integration_spec, integration_type
from ..runtime import forget_catalog, remember_agent_result, runtime
from ..security import require_user

router = APIRouter(dependencies=[Depends(require_user)])


# --------------------------------------------------------- model integrations


@router.get("/api/model-integrations")
async def get_model_integrations() -> dict[str, Any]:
    await integrations.ensure_defaults()
    return await integrations.payload()


@router.put("/api/model-integrations/{target}")
async def put_model_integration(
    target: str, payload: dict[str, Any] = Body(default={})
) -> dict[str, Any]:
    """`target` is a type when adding, or an existing instance when reconfiguring."""
    spec = integration_spec(integration_type(target) or target)
    config = normalise(integration_fields(spec), payload)
    instance = await integrations.save(target, payload, config)
    bus.publish("model.integration.changed", {"integration": instance, "configured": True})
    return await integrations.payload()


@router.delete("/api/model-integrations/{instance}")
async def delete_model_integration(instance: str) -> dict[str, Any]:
    await integrations.remove(instance)
    default_reset = await reset_default_model(instance)
    bus.publish("model.integration.changed", {"integration": instance, "configured": False})
    return {
        **await integrations.payload(),
        "default_model": runtime("default_model"),
        "default_reset": default_reset,
    }


@router.post("/api/model-integrations/{instance}/test")
async def test_model_integration(instance: str) -> dict[str, Any]:
    result = await integrations.test(instance)
    remember_agent_result(bool(result["ok"]))
    bus.publish("model.integration.test", {"integration": instance, "ok": result["ok"]})
    return result


# ------------------------------------------------------------------ mcp servers


@router.get("/api/mcp-servers")
async def get_mcp_servers() -> dict[str, Any]:
    return await mcp_servers.payload()


@router.put("/api/mcp-servers/{name}")
async def put_mcp_server(name: str, payload: dict[str, Any] = Body(default={})) -> dict[str, Any]:
    config = normalise(mcp_servers.FIELDS, {**payload, "name": name})
    await mcp_servers.save(name, config)
    forget_catalog()
    bus.publish("mcp.server.changed", {"server": name, "configured": True})
    return await mcp_servers.payload()


@router.delete("/api/mcp-servers/{name}")
async def delete_mcp_server(name: str) -> dict[str, Any]:
    await mcp_servers.remove(name)
    forget_catalog()
    bus.publish("mcp.server.changed", {"server": name, "configured": False})
    return await mcp_servers.payload()


@router.post("/api/mcp-servers/{name}/test")
async def test_mcp_server(name: str) -> dict[str, Any]:
    result = await mcp_servers.test(name)
    bus.publish("mcp.server.test", {"server": name, "ok": result["ok"]})
    return result
