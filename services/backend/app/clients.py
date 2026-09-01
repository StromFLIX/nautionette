"""Clients for the three things the backend talks to: broker, authoring server, Temporal."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any

import httpx

from .config import settings


def _headers() -> dict[str, str]:
    if settings.internal_token:
        return {"X-Internal-Token": settings.internal_token}
    return {}


class BrokerClient:
    """The only door to Docker. Fixed verbs, nothing generic."""

    def __init__(self, base_url: str | None = None) -> None:
        self.base_url = (base_url or settings.broker_url).rstrip("/")

    async def health(self) -> dict[str, Any]:
        async with httpx.AsyncClient(timeout=5) as client:
            response = await client.get(f"{self.base_url}/healthz")
            response.raise_for_status()
            return response.json()

    async def agent_sets(self) -> list[dict[str, Any]]:
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.get(f"{self.base_url}/agent-sets", headers=_headers())
            response.raise_for_status()
            return response.json().get("agent_sets", [])

    async def run_agent(
        self, job: dict[str, Any], timeout: float = 900
    ) -> AsyncIterator[dict[str, Any]]:
        """One container per call. Yields NDJSON events until the container exits."""
        async with httpx.AsyncClient(timeout=httpx.Timeout(timeout, connect=10)) as client:
            async with client.stream(
                "POST", f"{self.base_url}/agent/run", json=job, headers=_headers()
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
        async with httpx.AsyncClient(timeout=120) as client:
            response = await client.post(f"{self.base_url}/worker/restart", headers=_headers())
            response.raise_for_status()
            return response.json()


class AuthoringClient:
    """REST side of workflow-mcp. The MCP side is what agents use."""

    def __init__(self, base_url: str | None = None) -> None:
        self.base_url = (base_url or settings.workflow_mcp_url).rstrip("/")

    async def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        async with httpx.AsyncClient(timeout=60) as client:
            response = await client.request(
                method, f"{self.base_url}{path}", headers=_headers(), **kwargs
            )
            if response.status_code >= 400:
                detail = response.text[:500]
                raise RuntimeError(
                    f"workflow-mcp {method} {path} failed ({response.status_code}): {detail}"
                )
            return response.json()

    async def health(self) -> dict[str, Any]:
        return await self._request("GET", "/healthz")

    async def list_workflows(self) -> list[dict[str, Any]]:
        return (await self._request("GET", "/api/workflows"))["workflows"]

    async def get_workflow(self, name: str) -> dict[str, Any]:
        return await self._request("GET", f"/api/workflows/{name}")

    async def list_drafts(self) -> list[dict[str, Any]]:
        return (await self._request("GET", "/api/drafts"))["drafts"]

    async def get_draft(self, name: str) -> dict[str, Any]:
        return await self._request("GET", f"/api/drafts/{name}")

    async def validate(self, name: str, code: str) -> dict[str, Any]:
        return await self._request("POST", "/api/validate", json={"name": name, "code": code})

    async def write_draft(self, name: str, code: str, message: str = "") -> dict[str, Any]:
        return await self._request(
            "POST", "/api/drafts", json={"name": name, "code": code, "message": message}
        )

    async def publish(self, name: str) -> dict[str, Any]:
        return await self._request("POST", f"/api/drafts/{name}/publish")

    async def discard(self, name: str) -> dict[str, Any]:
        return await self._request("DELETE", f"/api/drafts/{name}")

    async def delete_workflow(self, name: str) -> dict[str, Any]:
        return await self._request("DELETE", f"/api/workflows/{name}")


class GatewayClient:
    def __init__(self, base_url: str | None = None) -> None:
        self.base_url = (base_url or settings.gateway_url).rstrip("/")

    async def health(self) -> dict[str, Any]:
        async with httpx.AsyncClient(timeout=5) as client:
            # agentgateway answers on /v1/models once a provider is configured.
            response = await client.get(f"{self.base_url}/v1/models")
            return {
                "status": "ok" if response.status_code < 500 else "degraded",
                "code": response.status_code,
            }


broker = BrokerClient()
authoring = AuthoringClient()
gateway = GatewayClient()
