"""Coordinate CLI-owned and hosted HTTPS MCP OAuth behind one lifecycle lock.

CLIs own their credentials; hosted connections use backend PKCE, sealed storage
and refresh. The desktop receives query/form_post callbacks through authenticated
APIs. Tokens, login URLs and codes are intentionally not agent-facing MCP tools.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import secrets
import sqlite3
import time
from dataclasses import dataclass

import httpx
from fastapi import HTTPException

from . import mcp_http_oauth, mcp_servers
from .clients import gateway
from .config import settings
from .db import db
from .events import bus
from .gateway_config import require_writable, resource_map
from .runtime import forget_catalog

WORKIQ_ARGS = ["-y", "@microsoft/workiq@1.0.1"]
TTL = 600
_lock = asyncio.Lock()


@dataclass
class Login:
    name: str
    config: dict
    auth: dict
    gateway_id: str
    previous: dict | None
    expires: float
    complete: bool = False


_sessions: dict[str, Login] = {}


def args(value, label: str) -> list[str]:
    if (
        not isinstance(value, list)
        or len(value) > 256
        or any(not isinstance(v, str) or len(v) > 8192 or "\0" in v for v in value)
    ):
        raise HTTPException(400, f"{label} must be a bounded JSON array of strings")
    return value


def profile(payload: dict, config: dict) -> dict:
    provider = payload.get("provider", "generic")
    if provider == "workiq":
        if config["command"] != "npx" or config["args"] != [*WORKIQ_ARGS, "mcp"]:
            raise HTTPException(400, "Use the pinned WorkIQ preset, or select the generic CLI adapter")
        if payload.get("accept_eula") is not True:
            raise HTTPException(400, "Accept the WorkIQ EULA explicitly before connecting this account")
        return {
            "provider": "workiq",
            "accept_eula": True,
            "login_args": [*WORKIQ_ARGS, "auth", "login"],
            "setup_args": [
                [*WORKIQ_ARGS, "config", "set", "disableBrokeredAuth=true", "language=en"],
                [*WORKIQ_ARGS, "accept-eula"],
            ],
        }
    if provider != "generic":
        raise HTTPException(400, "Unknown CLI OAuth adapter")
    setup = payload.get("setup_args", [])
    if not isinstance(setup, list) or len(setup) > 4:
        raise HTTPException(400, "Use at most four setup commands")
    return {
        "provider": provider,
        "login_args": args(payload.get("login_args"), "Login arguments"),
        "setup_args": [args(value, "Setup arguments") for value in setup],
    }


async def control(method: str, path: str, payload: dict | None = None) -> dict:
    try:
        return await gateway.cli_oauth(method, path, payload)
    except httpx.HTTPStatusError as exc:
        status = exc.response.status_code
        if status == 410:
            raise HTTPException(410, "CLI login expired or was lost on gateway restart; start again") from exc
        if status == 409:
            raise HTTPException(
                409, "A CLI login is already active, or the callback was already submitted"
            ) from exc
        raise HTTPException(
            502, "Gateway CLI OAuth refused the operation; check runtime compatibility and callback fields"
        ) from exc
    except (httpx.HTTPError, ValueError) as exc:
        raise HTTPException(
            502,
            "Gateway CLI OAuth is unavailable. Deploy the matching gateway image "
            "and configure authentication. A lost callback result will not be replayed.",
        ) from exc


async def capabilities() -> dict:
    try:
        if not settings.auth_enabled:
            raise HTTPException(403, "Configure APP_TOKEN before using CLI OAuth")
        async with asyncio.timeout(3):
            result = await control("GET", "/health")
        available = result.get("version") == 1 and result.get("available") is True
    except (HTTPException, TimeoutError):
        available = False
    return {
        "available": available,
        "desktop_bridge_version": 1,
        "http": {
            "available": settings.auth_enabled,
            "desktop_bridge_version": 1,
            "workiq": {
                "url": mcp_http_oauth.WORKIQ_URL,
                "client_id": mcp_http_oauth.WORKIQ_CLIENT,
                "redirect_uri": mcp_http_oauth.WORKIQ_REDIRECT,
            },
        },
        "message": "CLI-owned OAuth needs an updated Windows, macOS or Linux desktop app. "
        "Web/mobile clients cannot receive loopback callbacks."
        if available
        else "Deploy the matching agentgateway image with authenticated CLI OAuth control before connecting.",
        "workiq": {
            "command": "npx",
            "args": [*WORKIQ_ARGS, "mcp"],
            "provider": "workiq",
            "eula_url": "https://unpkg.com/@microsoft/workiq@1.0.1/EULA/"
            "Microsoft%20Work%20IQ%20CLI%20-%20Pre-Release%20or%20Evaluation%20Use%20Terms.docx",
        },
    }


async def start(name: str, payload: dict) -> dict:
    if not settings.auth_enabled:
        raise HTTPException(403, "Configure APP_TOKEN before using CLI OAuth")
    if payload.get("desktop_bridge_version") != 1:
        raise HTTPException(
            400,
            "Use an updated Nautionette desktop app; web, mobile and older desktop clients "
            "cannot receive CLI OAuth callbacks",
        )
    if payload.get("transport", "http") == "http":
        if any(
            key in payload for key in ["command", "args", "env", "login_args", "setup_args", "client_secret"]
        ):
            raise HTTPException(400, "Use hosted OAuth fields, not CLI commands or client secrets")
        async with _lock:
            await expire()
            if any(s.name == name and not s.complete for s in _sessions.values()) or any(
                s.name == name and not s.complete for s in mcp_http_oauth._sessions.values()
            ):
                raise HTTPException(409, "An OAuth login is already pending for this server")
            if len(_sessions) + len(mcp_http_oauth._sessions) >= 32:
                raise HTTPException(429, "Too many pending OAuth logins; cancel one or wait")
            return await mcp_http_oauth.start(name, payload)
    if payload.get("transport") != "stdio":
        raise HTTPException(400, "Unsupported OAuth transport")
    config = mcp_servers.normalise_config(name, payload)
    auth = profile(payload, config)
    async with _lock:
        await expire()
        mode, targets = await mcp_servers.fetch_resources()
        require_writable(mode)
        previous = resource_map(targets).get(name, {}).get("value")
        if previous is None:
            baseline = await gateway.config()
            if any(t["name"] == name for t in baseline.get("targets", [])):
                raise HTTPException(409, "That MCP name is owned by the gateway config")
        if any(s.name == name and not s.complete for s in _sessions.values()) or any(
            s.name == name and not s.complete for s in mcp_http_oauth._sessions.values()
        ):
            raise HTTPException(409, "A CLI login is already pending for this server")
        if len(_sessions) >= 32:
            raise HTTPException(429, "Too many pending CLI logins; cancel one or wait")
        # Isolate new OAuth targets' credentials/package caches. When reconnecting
        # preserve exactly the environment/HOME the gateway's MCP process uses.
        target = mcp_servers.stdio_value(name, config, previous or {})
        if "stdio" not in (previous or {}) and "HOME" not in config.get("env", {}):
            target["stdio"]["env"]["HOME"] = f"/data/mcp/oauth/{name}"
        config["env"] = dict(target["stdio"]["env"])
        request = {"command": config["command"], "env": config["env"], **auth}
        if len(json.dumps(request).encode()) > 65536:
            raise HTTPException(400, "CLI OAuth configuration exceeds 64 KiB")
        result = await control("POST", "/sessions", request)
        identifier = secrets.token_hex(32)
        _sessions[identifier] = Login(name, config, auth, result["id"], previous, time.monotonic() + TTL)
        return {"id": identifier, "status": "starting", "expires_in": TTL}


def session(identifier: str) -> Login:
    login = _sessions.get(identifier)
    if login is None or login.expires <= time.monotonic():
        raise HTTPException(410, "CLI login expired or backend restarted; start again")
    return login


async def poll(identifier: str) -> dict:
    async with _lock:
        if identifier.startswith("http-"):
            return await mcp_http_oauth.poll(identifier)
        login = session(identifier)
        if login.complete:
            return {"status": "complete", "message": "CLI login finished and the MCP handshake passed."}
        result = await control("GET", f"/sessions/{login.gateway_id}")
        if result.get("status") != "complete":
            return result
        # Do not overwrite a concurrent user/config-file edit while a browser
        # login was in progress. Authenticated account data remains CLI-owned.
        _, targets = await mcp_servers.fetch_resources()
        current = resource_map(targets).get(login.name, {}).get("value")
        expected = mcp_servers.stdio_value(login.name, login.config, login.previous or {})
        if current != login.previous and current != expected:
            raise HTTPException(409, "MCP configuration changed during login; no server was overwritten")
        if current != expected:
            await mcp_servers.save(login.name, login.config)
        db.execute(
            "INSERT INTO mcp_cli_oauth(name,config) VALUES (?,?) "
            "ON CONFLICT(name) DO UPDATE SET config=excluded.config",
            (login.name, json.dumps(login.auth)),
        )
        mcp_http_oauth.forget(login.name)
        login.complete = True
        login.config.clear()
        login.previous = None
        forget_catalog()
        bus.publish("mcp.server.changed", {"server": login.name, "configured": True})
        return {"status": "complete", "message": "CLI login finished and the MCP handshake passed."}


async def callback(identifier: str, payload: dict) -> dict:
    if identifier.startswith("http-"):
        async with _lock:
            return await mcp_http_oauth.callback(identifier, payload)
    login = session(identifier)
    # The helper validates state, response mode, one-shot capture and process-
    # owned loopback port. Never forward browser-supplied destinations/headers.
    if set(payload) - {"capture_id", "method", "fields"} or len(json.dumps(payload).encode()) > 65536:
        raise HTTPException(400, "Invalid CLI OAuth callback")
    return await control("POST", f"/sessions/{login.gateway_id}/callback", payload)


async def cancel(identifier: str) -> None:
    async with _lock:
        if identifier.startswith("http-"):
            await mcp_http_oauth.cancel(identifier)
            return
        login = _sessions.pop(identifier, None)
        if login and not login.complete:
            with contextlib.suppress(HTTPException):
                await control("DELETE", f"/sessions/{login.gateway_id}")


async def expire() -> None:
    await mcp_http_oauth.expire()
    for identifier in list(_sessions):
        login = _sessions[identifier]
        if login.expires <= time.monotonic():
            _sessions.pop(identifier, None)
            if not login.complete:
                with contextlib.suppress(HTTPException):
                    await control("DELETE", f"/sessions/{login.gateway_id}")


async def forget(name: str) -> None:
    mcp_http_oauth.forget(name)
    for identifier in list(_sessions):
        login = _sessions[identifier]
        if login.name == name:
            _sessions.pop(identifier, None)
            if not login.complete:
                with contextlib.suppress(HTTPException):
                    await control("DELETE", f"/sessions/{login.gateway_id}")
    db.execute("DELETE FROM mcp_cli_oauth WHERE name=?", (name,))


def public_connections() -> dict:
    return {
        **{
            row["name"]: {"mode": "cli", **json.loads(row["config"])}
            for row in db.query("SELECT * FROM mcp_cli_oauth")
        },
        **mcp_http_oauth.public_connections(),
    }


async def maintenance() -> None:
    # Hosted tokens refresh here; CLI token refresh remains the CLI's job.
    try:
        while True:
            try:
                async with _lock:
                    await expire()
                    await mcp_http_oauth.refresh_once()
            except (HTTPException, httpx.HTTPError, sqlite3.Error, OSError):
                logging.getLogger(__name__).warning(
                    "MCP OAuth maintenance unavailable; no credentials logged"
                )
            await asyncio.sleep(30)
    finally:
        for identifier in list(mcp_http_oauth._sessions):
            with contextlib.suppress(HTTPException):
                await mcp_http_oauth.cancel(identifier)
        for login in list(_sessions.values()):
            if not login.complete:
                with contextlib.suppress(HTTPException):
                    await control("DELETE", f"/sessions/{login.gateway_id}")
        _sessions.clear()
