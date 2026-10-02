"""Short-lived GitHub device authorization; durable credentials belong to agentgateway."""

from __future__ import annotations

import asyncio
import math
import secrets
import time
from dataclasses import dataclass, field
from typing import Any, Literal

import httpx
from fastapi import HTTPException
from pydantic import BaseModel, Field, StrictInt, StrictStr, ValidationError

from ..clients.http import shared
from ..events import bus
from ..fields import normalise
from ..gateway_config import require_writable
from .registry import integration_spec
from .resources import fetch_resources
from .service import save

# Public OAuth client identifier used by Copilot's device authorization flow, not a secret.
CLIENT_ID = "Iv1.b507a08c87ecfe98"
_sessions: dict[str, Login] = {}
_start_lock = asyncio.Lock()


class DeviceCode(BaseModel):
    device_code: StrictStr = Field(min_length=1, max_length=1024)
    user_code: StrictStr = Field(min_length=1, max_length=64)
    verification_uri: Literal["https://github.com/login/device", "https://github.com/login/device/"]
    expires_in: StrictInt = Field(gt=0, le=3600)
    interval: StrictInt = Field(default=5, gt=0, le=3600)


@dataclass
class Login:
    config: dict[str, str]
    device_code: str = field(repr=False)
    expires_at: float
    interval: int
    next_poll: float
    token: str = field(default="", repr=False)
    complete: bool = False
    lock: asyncio.Lock = field(default_factory=asyncio.Lock, repr=False)


async def _post(path: str, data: dict[str, str]) -> dict[str, Any]:
    try:
        response = await shared().post(
            f"https://github.com/login/{path}",
            data={"client_id": CLIENT_ID, **data},
            headers={"Accept": "application/json", "User-Agent": "Nautionette"},
            timeout=20,
        )
        response.raise_for_status()
    except httpx.HTTPError as exc:
        raise HTTPException(502, "Could not reach GitHub for Copilot sign-in. Try again.") from exc
    try:
        payload = response.json()
        if not isinstance(payload, dict):
            raise ValueError("expected an object")
        return payload
    except ValueError as exc:
        raise HTTPException(502, "GitHub returned an invalid Copilot sign-in response. Try again.") from exc


def _session(identifier: str) -> Login:
    login = _sessions.get(identifier)
    if login is None or login.expires_at <= time.monotonic():
        _sessions.pop(identifier, None)
        raise HTTPException(410, "Copilot sign-in expired or was cancelled. Sign in again.")
    return login


async def start(body: dict[str, Any]) -> dict[str, Any]:
    config = normalise(integration_spec("copilot").get("fields", []), body)
    mode, _, _ = await fetch_resources()
    require_writable(mode)
    async with _start_lock:
        for identifier, login in list(_sessions.items()):
            if login.expires_at <= time.monotonic():
                _sessions.pop(identifier, None)
        if len(_sessions) >= 8:
            raise HTTPException(
                429, "Too many Copilot sign-ins in progress. Cancel one or wait for it to expire."
            )
        payload = await _post("device/code", {"scope": "read:user"})
        if payload.get("error"):
            raise HTTPException(502, "GitHub could not start Copilot sign-in. Try again later.")
        try:
            device = DeviceCode.model_validate(payload)
        except ValidationError as exc:
            raise HTTPException(502, "GitHub returned an invalid Copilot device code. Try again.") from exc
        identifier = secrets.token_urlsafe(32)
        now = time.monotonic()
        _sessions[identifier] = Login(
            config=config,
            device_code=device.device_code,
            expires_at=now + device.expires_in,
            interval=device.interval,
            next_poll=now + device.interval,
        )
        asyncio.get_running_loop().call_later(device.expires_in, _sessions.pop, identifier, None)
        return {
            "id": identifier,
            "user_code": device.user_code,
            "verification_uri": device.verification_uri,
            "expires_in": device.expires_in,
            "interval": device.interval,
        }


async def poll(identifier: str) -> dict[str, Any]:
    login = _session(identifier)
    async with login.lock:
        _session(identifier)
        if login.complete:
            return {"status": "complete"}
        remaining = login.next_poll - time.monotonic()
        if remaining > 0:
            return {"status": "pending", "interval": math.ceil(remaining)}
        if not login.token:
            login.next_poll = time.monotonic() + login.interval
            payload = await _post(
                "oauth/access_token",
                {
                    "device_code": login.device_code,
                    "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
                },
            )
            _session(identifier)
            error = payload.get("error")
            if error in ("authorization_pending", "slow_down"):
                if error == "slow_down":
                    interval = payload.get("interval")
                    login.interval = max(
                        login.interval + 5, interval if type(interval) is int and interval > 0 else 0
                    )
                login.next_poll = time.monotonic() + login.interval
                return {"status": "pending", "interval": login.interval}
            if error:
                _sessions.pop(identifier, None)
                messages = {
                    "access_denied": "GitHub sign-in was denied. Sign in again when you are ready.",
                    "expired_token": "The GitHub device code expired. Sign in again.",
                }
                raise HTTPException(
                    400, messages.get(str(error), "GitHub refused Copilot sign-in. Sign in again.")
                )
            token = payload.get("access_token")
            if (
                not isinstance(token, str)
                or not token
                or len(token) > 8192
                or not token.isascii()
                or any(char.isspace() or ord(char) < 32 or ord(char) == 127 for char in token)
                or token.startswith("$")
                or str(payload.get("token_type", "")).lower() != "bearer"
            ):
                _sessions.pop(identifier, None)
                raise HTTPException(502, "GitHub returned an invalid Copilot credential. Sign in again.")
            login.token = token
        config = {**login.config, "api_key": login.token}
        await save("copilot", config, dict(config))
        login.token = ""
        login.device_code = ""
        login.complete = True
        bus.publish("model.integration.changed", {"integration": "copilot", "configured": True})
        return {"status": "complete"}


async def cancel(identifier: str) -> None:
    login = _sessions.pop(identifier, None)
    if login is not None:
        async with login.lock:
            login.token = ""
            login.device_code = ""
