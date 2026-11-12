"""MCP servers: the targets agentgateway federates onto one endpoint.

Every write is preceded by a handshake. HTTP targets are probed directly; stdio
commands are tested on an isolated gateway route before joining the federation.
"""

from __future__ import annotations

import asyncio
import contextlib
import ipaddress
import logging
import re
from typing import Any
from urllib.parse import urlsplit
from uuid import uuid4

import httpx
from fastapi import HTTPException
from opentelemetry import trace

from .catalog import tool_catalog
from .clients import gateway
from .clients.agentgateway import MCPHTTPError
from .config import settings
from .events import bus
from .fields import SECRET, SLUG, normalise
from .gateway_config import (
    attempt,
    credential_state,
    gateway_problem,
    require_writable,
    resource_map,
    storage_mode,
)
from .mcp_selection import logical_target, scoped_target
from .runtime import forget_catalog

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


STDIO_FIELDS = [
    FIELDS[0],
    {
        "key": "command",
        "label": "Command",
        "pattern": r"(?:/[A-Za-z0-9._/-]+|[A-Za-z0-9._-]+)",
        "placeholder": "npx",
        "help": "Executable inside agentgateway, not a shell command line.",
        "hint": "Use an executable name or absolute path; put arguments below.",
    },
]
# clear_env prevents accidental inheritance of gateway/provider credentials. These
# are runtime defaults, not user secrets. Package caches persist in the data volume.
STDIO_ENV = {"PATH": "/usr/local/bin:/usr/bin:/bin", "HOME": "/data/mcp"}
_PROBE_PREFIX = "nautionette-mcp-probe-"
_active_probes: set[str] = set()


async def recover_stdio_probes() -> None:
    """Remove probes left by an interrupted backend, without touching active tests."""
    for resource in await gateway.config_resources("traffic.route"):
        identifier = resource.get("id", "")
        if identifier.startswith(_PROBE_PREFIX) and identifier not in _active_probes:
            await gateway.delete_config_resource("traffic.route", identifier)


def normalise_config(name: str, payload: dict[str, Any]) -> dict[str, Any]:
    transport = payload.get("transport", "http")
    if transport not in ("http", "stdio"):
        raise HTTPException(status_code=400, detail="transport must be http or stdio")
    config: dict[str, Any] = normalise(
        STDIO_FIELDS if transport == "stdio" else FIELDS, {**payload, "name": name}
    )
    config["transport"] = transport
    if transport == "http":
        return config
    args = payload.get("args", [])
    if (
        not isinstance(args, list)
        or len(args) > 256
        or any(not isinstance(arg, str) or "\x00" in arg or len(arg) > 8192 for arg in args)
    ):
        raise HTTPException(status_code=400, detail="arguments must be an array of strings (no NUL bytes)")
    config["args"] = args
    if "env" in payload:
        env = payload["env"]
        if (
            not isinstance(env, dict)
            or len(env) > 128
            or any(
                not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,127}", key)
                or (
                    value is not None
                    and (not isinstance(value, str) or "\x00" in value or len(value) > 32768)
                )
                for key, value in env.items()
            )
        ):
            raise HTTPException(
                status_code=400, detail="environment must map variable names to strings or null"
            )
        config["env"] = env
    return config


def stdio_value(name: str, config: dict[str, Any], previous: dict[str, Any]) -> dict[str, Any]:
    old_env = (previous.get("stdio") or {}).get("env") or {}
    env = dict(STDIO_ENV)
    # Hybrid API resources execute stdio argv/env literally (unlike file-owned
    # config interpolation). Escaping $ here changes the child's real values.
    if "env" not in config:
        env.update(old_env)
    else:
        for key, value in config["env"].items():
            if value is None:
                if key not in old_env:
                    raise HTTPException(
                        status_code=400, detail=f"enter a value for environment variable {key}"
                    )
                env[key] = old_env[key]
            else:
                env[key] = value
    if len(env) > 128:
        raise HTTPException(
            status_code=400, detail="at most 128 environment variables, including HOME and PATH"
        )
    return {
        "name": name,
        "stdio": {
            "cmd": config["command"],
            "args": config["args"],
            "env": env,
            "clear_env": True,
        },
    }


async def probe_stdio(target: dict[str, Any]) -> dict[str, Any]:
    return await probe_target(target)


async def probe_http_gateway(name: str, url: str, credential: str) -> dict[str, Any]:
    return await probe_target(target_value(name, url, credential))


async def probe_target(target: dict[str, Any]) -> dict[str, Any]:
    """Check only this target in the gateway; never expose it to /mcp before validation.

    A random temporary route cannot affect /mcp. Delete its MCP session and then
    the route even on handshake failure. Do not return process output or secrets.
    """
    identifier = f"{_PROBE_PREFIX}{uuid4().hex}"
    path = f"/_nautionette/mcp-probes/{identifier}"
    route = {
        "name": identifier,
        "gateways": ["default"],
        "matches": [{"path": {"exact": path}}],
        "backends": [{"mcp": {"targets": [target]}}],
    }
    _active_probes.add(identifier)
    try:
        await gateway.put_config_resources("traffic.route", [route])
        try:
            async with asyncio.timeout(90):
                # PUT acknowledgement precedes the data-plane watch and MCP
                # initialization. Retry only bounded read-only initialization,
                # on this same route/token; never repeat PUT or OAuth exchange.
                initialization_retries = 0
                for attempt in range(50):
                    try:
                        with trace.get_tracer(__name__).start_as_current_span("mcp.probe") as span:
                            span.set_attribute("rpc.system", "mcp")
                            try:
                                tools = await gateway.mcp_tools(
                                    f"{settings.gateway_url.rstrip('/')}{path}", timeout=90
                                )
                            except MCPHTTPError as error:
                                span.set_attribute("rpc.method", error.stage)
                                span.set_attribute("http.status_code", error.response.status_code)
                                span.set_attribute("error.type", f"mcp.{error.reason}")
                                raise
                        break
                    except httpx.HTTPStatusError as error:
                        status = error.response.status_code
                        stage = getattr(error, "stage", "initialize")
                        if status == 404 and stage == "initialize" and attempt < 49:
                            await asyncio.sleep(0.1)
                            continue
                        if (
                            isinstance(error, MCPHTTPError)
                            and "stdio" not in target
                            and stage == "initialize"
                            and error.reason == "unclassified"
                            and status in {400, 408, 429, 500, 502, 503, 504}
                            and initialization_retries < 2
                            and attempt < 49
                        ):
                            await asyncio.sleep(0.5 * 2**initialization_retries)
                            initialization_retries += 1
                            continue
                        raise
            label = "Process" if "stdio" in target else "HTTPS server"
            return {
                "ok": True,
                "status": None,
                "message": f"{label} answered with {len(tools)} tools.",
                "tool_names": [tool["name"] for tool in tools],
            }
        except (httpx.HTTPError, ValueError, TimeoutError) as error:
            status = error.response.status_code if isinstance(error, httpx.HTTPStatusError) else None
            # Never log exception text: transport/protocol errors can contain
            # bearer headers, provider bodies or an OAuth callback URL.
            # Uvicorn's console handler remains present when the payload-free
            # OTLP handler suppresses ordinary application log text.
            logging.getLogger("uvicorn.error").warning(
                "MCP probe failed: transport=%s status=%s error_type=%s stage=%s reason=%s rpc_code=%s",
                "stdio" if "stdio" in target else "http",
                status,
                type(error).__name__,
                getattr(error, "stage", "unknown"),
                getattr(error, "reason", "unclassified"),
                getattr(error, "rpc_code", None),
            )
            return {
                "ok": False,
                "status": status,
                "stage": getattr(error, "stage", "unknown"),
                "reason": getattr(error, "reason", "unclassified"),
                "message": (
                    "The stdio process did not answer MCP. Check the command, arguments "
                    "and environment in agentgateway. First-time package downloads may need another attempt."
                )
                if "stdio" in target
                else "The HTTPS server did not answer authenticated MCP through agentgateway.",
            }
    except httpx.HTTPError as exc:
        raise gateway_problem(exc) from exc
    finally:
        try:
            await gateway.delete_config_resource("traffic.route", identifier)
        except httpx.HTTPError as exc:
            if not isinstance(exc, httpx.HTTPStatusError) or exc.response.status_code != 404:
                raise HTTPException(
                    status_code=502, detail="Could not remove the temporary MCP probe route."
                ) from exc
        finally:
            _active_probes.discard(identifier)


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
    # Selection is transport metadata, not an external edit to OAuth/CLI ownership.
    return storage_mode(runtime_state), [
        {**resource, "value": logical_target(resource["value"])} for resource in targets
    ]


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
    from .mcp_oauth import public_connections

    oauth = public_connections()
    mode, targets = await fetch_resources()
    config = await attempt(gateway.config(), {"targets": []})
    _, servers = await tool_catalog(config)
    counts = {server["name"]: server["count"] for server in servers}
    managed = resource_map(targets)
    return {
        "servers": [
            {
                "name": target["name"],
                "url": target.get("host", ""),
                "transport": target.get("transport", "http"),
                **(
                    {
                        "command": target.get("command", ""),
                        "args": target.get("args", []),
                        "env": target.get("env", {}),
                    }
                    if target.get("transport") == "stdio"
                    else {}
                ),
                # A file-owned target is the checked-in baseline; the app cannot touch it.
                "managed": target["name"] in managed,
                **({"startup_scoped": True} if target.get("selection_scoped") else {}),
                **({"oauth": oauth[target["name"]]} if target["name"] in oauth else {}),
                "credential": credential_state(stored_credential(target["name"], targets)),
                "tool_count": counts.get(target["name"], 0),
            }
            for target in config.get("targets") or []
        ],
        "oauth_connections": oauth,
        "fields": FIELDS,
        "stdio_fields": STDIO_FIELDS,
        "storage_mode": mode,
        "writable": mode == "hybrid",
    }


async def save(name: str, config: dict[str, Any]) -> None:
    """The path names the server; a name is never renamed, because tools are named after it."""
    if config["transport"] == "http":
        reachable_endpoint(config["url"])

    mode, targets = await fetch_resources()
    require_writable(mode)
    if name not in resource_map(targets):
        baseline = await attempt(gateway.config(), {"targets": []})
        if any(target["name"] == name for target in baseline.get("targets") or []):
            raise HTTPException(status_code=409, detail=f"{name} is defined in the gateway config")

    credential = ""
    if config["transport"] == "stdio":
        previous = resource_map(targets).get(name, {}).get("value", {})
        target = stdio_value(name, config, previous)
        verdict = await probe_stdio(target)
    else:
        credential = config["token"] or stored_credential(name, targets)
        target = target_value(name, config["url"], credential)
        verdict = await probe(config["url"], credential)
    if not verdict["ok"]:
        raise HTTPException(status_code=400, detail=verdict["message"])
    try:
        await gateway.put_config_resources("mcp.target", [target])
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
    if "stdio" in target:
        return await probe_stdio(target)
    return await probe(target["mcp"]["host"], stored_credential(name, targets))


async def reconcile_selection() -> None:
    """Decorate existing hybrid targets without reconstructing credentials or policies.

    Call under mcp_oauth._lock. Re-read each resource immediately before updating;
    gateway PUT has no compare-and-set, so external config writers must still be
    coordinated, just as for ordinary Settings writes. Missing headers keep their
    previous behavior, and custom conditions remain an outer AND.
    """
    if len((await gateway.config()).get("targets") or []) < 2:
        return
    for resource in await gateway.config_resources("mcp.target"):
        current = next(
            (item for item in await gateway.config_resources("mcp.target") if item["id"] == resource["id"]),
            None,
        )
        if current is None or current["id"] != current["value"].get("name"):
            # App management uses target-name resource IDs. Do not copy an
            # externally identified resource under a new ID while reconciling.
            continue
        value = current["value"]
        desired = scoped_target(value)
        if desired != value:
            await gateway.put_config_resources("mcp.target", [desired])


async def bootstrap_backend() -> None:
    from . import mcp_oauth

    credential = settings.internal_token or settings.app_token
    target = target_value("backend", settings.backend_mcp_url, credential)
    phase, last_status = "backend registration", None
    for retry in range(8):
        try:
            async with mcp_oauth._lock:
                mode, targets = await fetch_resources()
                require_writable(mode)
                await recover_stdio_probes()
                verdict = await probe(settings.backend_mcp_url, credential)
                if verdict["ok"]:
                    if resource_map(targets).get("backend", {}).get("value") != target:
                        await gateway.put_config_resources("mcp.target", [target])
                    phase = "target selection configuration (requires agentgateway v1.7.0+)"
                    await reconcile_selection()
                    forget_catalog()
                    bus.publish("mcp.server.changed", {"server": "backend", "configured": True})
                    return
        except HTTPException as error:
            last_status = error.status_code
        except httpx.HTTPError as error:
            last_status = error.response.status_code if isinstance(error, httpx.HTTPStatusError) else None
        await asyncio.sleep(min(0.25 * 2**retry, 5.0))
    logging.getLogger("uvicorn.error").warning(
        "MCP startup incomplete: phase=%s http_status=%s; inspect gateway compatibility/configuration",
        phase,
        last_status,
    )
