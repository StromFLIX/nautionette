"""Server-enforced capability adapters. A container never joins the gateway network.

MCP sessions are reused, but calls are never automatically retried. External HTTPS
connects to a prevalidated, pinned public IP with normal hostname/TLS verification.
Neither adapter forwards browser headers, environment credentials or redirects.
"""

from __future__ import annotations

import asyncio
import contextlib
import ipaddress
import json
import socket
import time
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlsplit

import httpx
import jsonschema
from fastapi import HTTPException

from .clients.agentgateway import _rpc, _rpc_result
from .clients.http import shared
from .config import settings

LIMIT = 65536


def subset(required: Any, supplied: Any) -> bool:
    if isinstance(required, dict):
        return isinstance(supplied, dict) and all(
            k in supplied and subset(v, supplied[k]) for k, v in required.items()
        )
    return type(required) is type(supplied) and required == supplied


def authorize(manifest: dict, request: dict) -> None:
    permissions = (manifest.get("backend") or {}).get("permissions", {})
    if request.get("kind") == "mcp":
        if set(request) - {"kind", "tool", "arguments", "request_id"} or not isinstance(
            request.get("arguments"), dict
        ):
            raise HTTPException(422, "Invalid MCP capability request")
        grants = permissions.get("mcp", [])
        if not any(
            g["tool"] == request.get("tool") and subset(g["arguments"], request["arguments"]) for g in grants
        ):
            raise HTTPException(403, "Tool or resource arguments are outside this app's grant")
    elif request.get("kind") == "http":
        if set(request) - {"kind", "url", "method", "body", "request_id"}:
            raise HTTPException(422, "Invalid HTTPS capability request")
        try:
            parsed = urlsplit(request.get("url", ""))
            origin = f"https://{parsed.hostname}"
            if (
                parsed.scheme != "https"
                or parsed.port not in (443, None)
                or parsed.username
                or parsed.password
                or parsed.fragment
                or "%" in parsed.path
                or ".." in parsed.path.split("/")
                or "\\" in request["url"]
                or any(ord(c) < 33 for c in request["url"])
            ):
                raise ValueError
        except (ValueError, TypeError, KeyError) as exc:
            raise HTTPException(422, "Invalid HTTPS URL") from exc
        if len(request["url"]) > 4096 or not any(
            g["origin"] == origin
            and request.get("method") in g["methods"]
            and (
                parsed.path == g["path_prefix"].rstrip("/")
                or parsed.path.startswith(g["path_prefix"].rstrip("/") + "/")
            )
            for g in permissions.get("http", [])
        ):
            raise HTTPException(403, "Destination, method or path is outside this app's grant")
    else:
        raise HTTPException(403, "Unknown mini-app capability")


def public_addresses(host: str) -> list[str]:
    addresses = sorted({item[4][0] for item in socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)})
    if not addresses or any(
        not ipaddress.ip_address(ip).is_global
        or ipaddress.ip_address(ip).is_multicast
        or ipaddress.ip_address(ip).is_reserved
        for ip in addresses
    ):
        raise HTTPException(403, "Private, loopback, metadata and special-purpose addresses are denied")
    return addresses


async def https(request: dict) -> dict:
    parsed = urlsplit(request["url"])
    # Total wall-clock deadline, including DNS, TLS, headers and slow/drip bodies.
    async with asyncio.timeout(20):
        addresses = await asyncio.to_thread(public_addresses, parsed.hostname)
        address = f"[{addresses[0]}]" if ":" in addresses[0] else addresses[0]
        path = (parsed.path or "/") + ("?" + parsed.query if parsed.query else "")
        async with httpx.AsyncClient(trust_env=False, follow_redirects=False, timeout=15) as client:
            async with client.stream(
                request["method"],
                f"https://{address}{path}",
                json=request.get("body"),
                headers={
                    "Host": parsed.hostname,
                    "Accept": "application/json",
                    "User-Agent": "Nautionette-miniapp/1",
                },
                # httpcore verifies TLS against the original hostname, not the pinned IP.
                extensions={"sni_hostname": parsed.hostname},
            ) as response:
                data = bytearray()
                async for block in response.aiter_bytes():
                    data.extend(block)
                    if len(data) > LIMIT:
                        raise HTTPException(413, "Upstream response exceeds 64 KiB")
                try:
                    value = json.loads(data)
                except ValueError:
                    value = data.decode("utf-8", "replace")
                return {"status": response.status_code, "body": value}


@dataclass
class Session:
    headers: dict = field(default_factory=dict)
    tools: dict = field(default_factory=dict)
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    used: float = field(default_factory=time.monotonic)

    async def rpc(self, method: str, params: dict | None = None, limit: int = LIMIT) -> dict:
        async with shared().stream(
            "POST",
            settings.mcp_url,
            headers=self.headers,
            json=_rpc(None if method.startswith("notifications/") else 1, method, params),
            timeout=20,
        ) as reply:
            if reply.status_code >= 400:
                self.tools = {}
                raise HTTPException(502, "Gateway session unavailable; no tool call was retried")
            if session := reply.headers.get("mcp-session-id"):
                self.headers["Mcp-Session-Id"] = session
            data = bytearray()
            async for chunk in reply.aiter_bytes():
                data.extend(chunk)
                if len(data) > limit:
                    raise HTTPException(413, "Gateway response exceeds the app response limit")
            if not data and method.startswith("notifications/"):
                return {}
            return _rpc_result(
                httpx.Response(
                    200, content=bytes(data), headers={"content-type": reply.headers.get("content-type", "")}
                )
            )

    async def initialize(self):
        self.headers = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream"}
        initialized = await self.rpc(
            "initialize",
            {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {"name": "nautionette-miniapp", "version": "1"},
            },
        )
        version = initialized.get("protocolVersion")
        if not isinstance(version, str):
            raise HTTPException(502, "Invalid gateway handshake")
        self.headers["MCP-Protocol-Version"] = version
        await self.rpc("notifications/initialized")
        tools, cursor, seen = {}, None, set()
        for _ in range(32):
            page = await self.rpc("tools/list", {"cursor": cursor} if cursor else {}, 4 * 1024 * 1024)
            if not isinstance(page.get("tools"), list):
                raise HTTPException(502, "Invalid gateway catalog")
            for tool in page["tools"]:
                tools[tool["name"]] = tool
            cursor = page.get("nextCursor")
            if not cursor:
                self.tools = tools
                return
            if not isinstance(cursor, str) or cursor in seen:
                break
            seen.add(cursor)
        raise HTTPException(502, "Gateway pagination exceeded the limit")


sessions: dict[str, Session] = {}


async def describe(app_id: str, request: dict) -> dict:
    session = sessions.setdefault(app_id, Session())
    async with session.lock:
        session.used = time.monotonic()
        if not session.tools:
            await session.initialize()
        tool = session.tools.get(request["tool"])
        if not tool:
            raise HTTPException(403, "Granted tool is no longer available")
        try:
            jsonschema.validate(request["arguments"], tool.get("inputSchema", {}))
        except jsonschema.ValidationError as exc:
            raise HTTPException(422, "Arguments do not match the upstream tool schema") from exc
        return tool


async def mcp(app_id: str, request: dict) -> dict:
    session = sessions[app_id]
    async with session.lock:
        session.used = time.monotonic()
        return await session.rpc("tools/call", {"name": request["tool"], "arguments": request["arguments"]})


async def close(app_id: str):
    session = sessions.pop(app_id, None)
    if session and "Mcp-Session-Id" in session.headers:
        async with session.lock:
            with contextlib.suppress(httpx.HTTPError):
                await shared().delete(settings.mcp_url, headers=session.headers, timeout=5)


async def reap():
    for app_id, session in list(sessions.items()):
        if not session.lock.locked() and time.monotonic() - session.used > 120:
            await close(app_id)
