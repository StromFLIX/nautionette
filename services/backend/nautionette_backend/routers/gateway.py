"""Model integrations and MCP servers: what the app writes into agentgateway."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException
from pydantic import BaseModel, Field

from .. import integrations, mcp_auth_wait, mcp_http_oauth, mcp_oauth, mcp_oauth_setup, mcp_servers
from ..catalog import reset_default_model
from ..events import bus
from ..fields import normalise
from ..integrations import copilot_login
from ..integrations.registry import integration_fields, integration_spec, integration_type
from ..runtime import forget_catalog, remember_agent_result, runtime
from ..security import require_user

router = APIRouter(dependencies=[Depends(require_user)])


# --------------------------------------------------------- model integrations


@router.post("/api/model-integrations/copilot/login")
async def start_copilot_login(payload: dict[str, Any] = Body(default={})) -> dict[str, Any]:
    return await copilot_login.start(payload)


@router.post("/api/model-integrations/copilot/login/{identifier}")
async def poll_copilot_login(identifier: str) -> dict[str, Any]:
    return await copilot_login.poll(identifier)


@router.delete("/api/model-integrations/copilot/login/{identifier}", status_code=204)
async def cancel_copilot_login(identifier: str) -> None:
    await copilot_login.cancel(identifier)


@router.get("/api/model-integrations")
async def get_model_integrations() -> dict[str, Any]:
    await integrations.ensure_defaults()
    return await integrations.payload()


@router.put("/api/model-integrations/{target}")
async def put_model_integration(target: str, payload: dict[str, Any] = Body(default={})) -> dict[str, Any]:
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


# Interactive OAuth endpoints intentionally live outside /api/mcp-servers:
# codes/login URLs must never become agent-facing MCP tools or chat evidence.


@router.get("/api/mcp-oauth/capabilities")
async def mcp_oauth_capabilities() -> dict[str, Any]:
    return await mcp_oauth.capabilities()


@router.get("/api/mcp-oauth/status")
async def mcp_oauth_status() -> dict[str, Any]:
    return mcp_oauth.auth_status()


class OAuthGate(BaseModel):
    grant: str | None = Field(default=None, max_length=128)
    tool: str = Field(min_length=1, max_length=256)
    tool_call_id: str = Field(min_length=1, max_length=256)
    cancel: bool = False


class OAuthDismiss(BaseModel):
    requests: list[str] = Field(max_length=100)


@router.post("/api/mcp-oauth/gate")
async def mcp_oauth_gate(body: OAuthGate) -> dict:
    """Private pre-dispatch gate; never expose its capability to the model."""
    return mcp_auth_wait.gate(body.model_dump())


@router.post("/api/mcp-oauth/tools")
async def mcp_oauth_tools(body: dict[str, Any] = Body(default={})) -> dict:
    """Private bridge discovery, including saved tools awaiting authentication."""
    from ..catalog import EMPTY_CONFIG, tool_catalog
    from ..clients import gateway
    from ..db import db
    from ..gateway_config import attempt
    from ..mcp_selection import target_headers

    selected_targets = body.get("targets")
    try:
        target_headers(selected_targets)
    except ValueError as exc:
        raise HTTPException(422, "Invalid MCP target selection") from exc

    # Pi has already listed the live federation. Only a published hosted-OAuth
    # target needs a second discovery to learn its current schemas and gate owners.
    # Disconnected targets keep their saved schemas; this never caches authorization.
    connections = mcp_http_oauth.public_connections()
    tools = []
    if connections:
        config = await attempt(gateway.config(), None)
        changed = connections != mcp_http_oauth.public_connections()
        if changed:
            # Login/removal/refresh may have yielded a different owner revision
            # during config I/O. Use a fresh view and the original discovery path.
            config = await attempt(gateway.config(), None)
        if (
            config is None
            or changed
            or any(
                target["name"] in connections
                and (selected_targets is None or target["name"] in selected_targets)
                for target in config.get("targets") or []
            )
        ):
            # An unknown config is not evidence that OAuth targets are absent.
            tools, _ = await tool_catalog(
                config if config is not None else dict(EMPTY_CONFIG), selected_targets=selected_targets
            )
        else:
            tools = mcp_auth_wait.unavailable_tools()
    return {
        "tools": tools,
        "oauth_tools": [row["name"] for row in db.query("SELECT name FROM mcp_oauth_tools")],
    }


@router.post("/api/mcp-oauth/dismiss")
async def dismiss_mcp_oauth(body: OAuthDismiss) -> dict:
    mcp_auth_wait.reject(body.requests)
    return {"status": "failed"}


@router.post("/api/mcp-oauth/prepare/{name}")
async def prepare_mcp_oauth(name: str, payload: dict[str, Any] = Body(default={})) -> dict[str, Any]:
    return await mcp_oauth.prepare(name, payload)


@router.post("/api/mcp-oauth/connect/{name}")
async def connect_mcp_server(name: str, payload: dict[str, Any] = Body(default={})) -> dict[str, Any]:
    return await mcp_oauth.connect_server(name, payload)


@router.post("/api/mcp-oauth/discover")
async def discover_mcp_oauth(payload: dict[str, Any] = Body(default={})) -> dict[str, Any]:
    return await mcp_http_oauth.discover(payload)


@router.post("/api/mcp-oauth/start/{name}")
async def start_mcp_oauth(name: str, payload: dict[str, Any] = Body(default={})) -> dict[str, Any]:
    return await mcp_oauth.start(name, payload)


@router.get("/api/mcp-oauth/login/{identifier}")
async def poll_mcp_oauth(identifier: str) -> dict[str, Any]:
    return await mcp_oauth.poll(identifier)


@router.post("/api/mcp-oauth/callback/{identifier}")
async def callback_mcp_oauth(identifier: str, payload: dict[str, Any] = Body(default={})) -> dict[str, Any]:
    return await mcp_oauth.callback(identifier, payload)


@router.delete("/api/mcp-oauth/login/{identifier}", status_code=204)
async def cancel_mcp_oauth(identifier: str) -> None:
    await mcp_oauth.cancel(identifier)


# ------------------------------------------------------------------ mcp servers


@router.get("/api/mcp-servers")
async def get_mcp_servers() -> dict[str, Any]:
    return await mcp_servers.payload()


@router.put("/api/mcp-servers/{name}")
async def put_mcp_server(name: str, payload: dict[str, Any] = Body(default={})) -> dict[str, Any]:
    config = mcp_servers.normalise_config(name, payload)
    async with mcp_oauth._lock:
        await mcp_servers.save(name, config)
        await mcp_oauth.forget(name)
    forget_catalog()
    bus.publish("mcp.server.changed", {"server": name, "configured": True})
    return await mcp_servers.payload()


@router.delete("/api/mcp-servers/{name}")
async def delete_mcp_server(name: str) -> dict[str, Any]:
    async with mcp_oauth._lock:
        try:
            await mcp_servers.remove(name)
        except HTTPException as error:
            if error.status_code != 404 or name not in mcp_http_oauth.public_connections():
                raise
            # Expired OAuth targets are removed from federation but retain their
            # safe reconnect profile. Removing that saved profile is still valid.
        await mcp_oauth.forget(name)
        mcp_oauth_setup.forget(name)
    forget_catalog()
    bus.publish("mcp.server.changed", {"server": name, "configured": False})
    return await mcp_servers.payload()


@router.post("/api/mcp-servers/{name}/test")
async def test_mcp_server(name: str) -> dict[str, Any]:
    result = await mcp_servers.test(name)
    bus.publish("mcp.server.test", {"server": name, "ok": result["ok"]})
    return result
