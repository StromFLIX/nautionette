"""The only door to Docker. Fixed verbs, nothing generic."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any

import httpx

from ..config import settings
from .http import internal_headers, shared


class BrokerClient:
    def __init__(self, base_url: str | None = None) -> None:
        self.base_url = (base_url or settings.broker_url).rstrip("/")

    async def health(self) -> dict[str, Any]:
        response = await shared().get(f"{self.base_url}/healthz", timeout=5)
        response.raise_for_status()
        return response.json()

    async def agent_sets(self) -> list[dict[str, Any]]:
        response = await shared().get(
            f"{self.base_url}/agent-sets", headers=internal_headers(), timeout=10
        )
        response.raise_for_status()
        return response.json().get("agent_sets", [])

    async def run_agent(
        self, job: dict[str, Any], timeout: float = 900
    ) -> AsyncIterator[dict[str, Any]]:
        """One container per call. Yields NDJSON events until the container exits."""
        async with shared().stream(
            "POST",
            f"{self.base_url}/agent/run",
            json=job,
            headers=internal_headers(),
            timeout=httpx.Timeout(timeout, connect=10),
        ) as response:
            if response.status_code >= 400:
                body = (await response.aread()).decode("utf-8", "replace")
                yield {
                    "type": "error",
                    "message": f"broker returned {response.status_code}: {body[:400]}",
                }
                return
            async for line in response.aiter_lines():
                line = line.strip()
                if not line:
                    continue
                try:
                    yield json.loads(line)
                except json.JSONDecodeError:
                    yield {"type": "log", "text": line}

    async def restart_worker(self) -> dict[str, Any]:
        response = await shared().post(
            f"{self.base_url}/worker/restart", headers=internal_headers(), timeout=120
        )
        response.raise_for_status()
        return response.json()


broker = BrokerClient()
