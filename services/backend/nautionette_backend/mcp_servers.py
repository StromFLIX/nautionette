"""MCP servers: the targets agentgateway federates onto one endpoint.

One target that cannot answer takes the whole endpoint down with it, so every
write is preceded by a handshake against the endpoint being written.
"""

from __future__ import annotations

import asyncio
import contextlib
import ipaddress
from typing import Any
from urllib.parse import urlsplit

import httpx
from fastapi import HTTPException

from .catalog import tool_catalog
from .clients import gateway
from .config import settings
from .fields import SECRET, SLUG
from .gateway_config import (
    attempt,
    credential_state,
    gateway_problem,
    require_writable,
    resource_map,
    storage_mode,
)

_URL = r"https?://[A-Za-z0-9.-]+(?::\d{1,5})?(?:/[^\s?#]*)?(?:\?[^\s#]*)?"

# A target's name is also the prefix agentgateway puts on every tool it federates.
FIELDS: list[dict[str, Any]] = [
    {
        "key": "name",
        "label": "Name",
        "pattern": SLUG,
        "placeholder": "linear",
        "help": "Also the tool prefix, so its tools arrive as linear_<tool>.",
        "hint": "Lower-case letters, digits and dashes, up to 24 characters.",
    },
    {
        "key": "url",
        "label": "Endpoint URL",
        "kind": "url",
        "pattern": _URL,
        "placeholder": "https://mcp.example.com/mcp",
        "help": "The streamable HTTP endpoint, exactly as an MCP client would be given it.",
        "hint": "An http:// or https:// URL. A query string is fine; spaces are not.",
    },
    {
        "key": "token",
        "label": "Access token",
        "kind": "secret",
        "optional": True,
        "pattern": SECRET,
        "placeholder": "sent as Authorization: Bearer",
        "help": (
            "agentgateway keeps it and it is never shown again. Leave it empty for an open "
            "server, or name a variable set on agentgateway as $MY_TOKEN."
        ),
        "hint": "The token itself, or $MY_TOKEN in capitals to name a variable.",
    },
]


def reachable_endpoint(url: str) -> None:
    """The gateway shares this network, so a target must not point back at its own door."""
    host = (urlsplit(url).hostname or "").lower()
    with contextlib.suppress(ValueError):
        address = ipaddress.ip_address(host)
        if address.is_loopback or address.is_link_local or address.is_unspecified:
            raise HTTPException(status_code=400, detail="that address is not a reachable server")
    if host in {"localhost", (urlsplit(settings.gateway_url).hostname or "").lower()}:
        raise HTTPException(status_code=400, detail="that address is agentgateway itself")


def target_value(name: str, url: str, credential: str) -> dict[str, Any]:
    value: dict[str, Any] = {"name": name, "mcp": {"host": url}}
    if credential:
        value["policies"] = {"backendAuth": {"key": {"value": credential}}}
    return value


def stored_credential(name: str, targets: list[dict[str, Any]]) -> str:
    """The token agentgateway already holds, so changing a URL never needs it retyped."""
    policies = (resource_map(targets).get(name, {}).get("value", {}).get("policies")) or {}
    key = (policies.get("backendAuth") or {}).get("key")
    return str((key.get("value") if isinstance(key, dict) else key) or "")


async def fetch_resources() -> tuple[str, list[dict[str, Any]]]:
    try:
        runtime_state, targets = await asyncio.gather(
            gateway.runtime(), gateway.config_resources("mcp.target")
        )
    except httpx.HTTPError as exc:
        raise gateway_problem(exc) from exc
    return storage_mode(runtime_state), targets


async def probe(url: str, credential: str) -> dict[str, Any]:
    """A handshake before anything is written: one bad target takes /mcp down with it."""
    held_by_gateway = credential.startswith("$")
    extra = {"Authorization": f"Bearer {credential}"} if credential and not held_by_gateway else {}
    try:
        tools = await gateway.mcp_tools(url, extra)
    except httpx.HTTPStatusError as exc:
        status = exc.response.status_code
        if status in {401, 403} and held_by_gateway:
            # Only agentgateway can read a key it holds, so a refusal here is no verdict.
            return {"ok": True, "status": status, "message": "The server asked for its token."}
        return {"ok": False, "status": status, "message": f"The server answered HTTP {status}."}
    except httpx.HTTPError:
        return {"ok": False, "status": None, "message": "The server could not be reached."}
    except ValueError:
        return {"ok": False, "status": None, "message": "That endpoint does not speak MCP."}
    return {"ok": True, "status": 200, "message": f"Answered with {len(tools)} tools."}


async def payload() -> dict[str, Any]:
    mode, targets = await fetch_resources()
    config = await attempt(gateway.config(), {"targets": []})
    _, servers = await tool_catalog(config)
    counts = {server["name"]: server["count"] for server in servers}
    managed = resource_map(targets)
    return {
        "servers": [
            {
                "name": target["name"],
                "url": target["host"],
                # A file-owned target is the checked-in baseline; the app cannot touch it.
                "managed": target["name"] in managed,
                "credential": credential_state(stored_credential(target["name"], targets)),
                "tool_count": counts.get(target["name"], 0),
            }
            for target in config.get("targets") or []
        ],
        "fields": FIELDS,
        "storage_mode": mode,
        "writable": mode == "hybrid",
    }


async def save(name: str, config: dict[str, str]) -> None:
    """The path names the server; a name is never renamed, because tools are named after it."""
    url = config["url"]
    reachable_endpoint(url)

    mode, targets = await fetch_resources()
    require_writable(mode)
    if name not in resource_map(targets):
        baseline = await attempt(gateway.config(), {"targets": []})
        if any(target["name"] == name for target in baseline.get("targets") or []):
            raise HTTPException(status_code=409, detail=f"{name} is defined in the gateway config")

    credential = config["token"] or stored_credential(name, targets)
    verdict = await probe(url, credential)
    if not verdict["ok"]:
        raise HTTPException(status_code=400, detail=verdict["message"])
    try:
        await gateway.put_config_resources("mcp.target", [target_value(name, url, credential)])
    except httpx.HTTPError as exc:
        raise gateway_problem(exc, credential) from exc


async def remove(name: str) -> None:
    mode, targets = await fetch_resources()
    require_writable(mode)
    if name not in resource_map(targets):
        raise HTTPException(status_code=404, detail="unknown MCP server")
    try:
        await gateway.delete_config_resource("mcp.target", name)
    except httpx.HTTPError as exc:
        raise gateway_problem(exc) from exc


async def test(name: str) -> dict[str, Any]:
    _, targets = await fetch_resources()
    target = resource_map(targets).get(name, {}).get("value")
    if not target:
        raise HTTPException(status_code=404, detail="unknown MCP server")
    return await probe(target["mcp"]["host"], stored_credential(name, targets))
