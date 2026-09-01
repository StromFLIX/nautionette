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

    async def run_agent(self, job: dict[str, Any], timeout: float = 900) -> AsyncIterator[dict[str, Any]]:
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
            response = await client.request(method, f"{self.base_url}{path}", headers=_headers(), **kwargs)
            if response.status_code >= 400:
                detail = response.text[:500]
                raise RuntimeError(f"workflow-mcp {method} {path} failed ({response.status_code}): {detail}")
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

    async def config(self) -> dict[str, Any]:
        """The gateway's own view of itself: which upstream, which MCP targets.

        Only the naming is taken. Anything that could carry a key stays here.
        """
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.get(f"{self.base_url}/api/config")
            response.raise_for_status()
            payload = response.json()
        providers: list[str] = []
        wildcard = False
        for entry in (payload.get("llm") or {}).get("models", []) or []:
            provider = entry.get("provider")
            if provider and provider not in providers:
                providers.append(provider)
            if entry.get("name") == "*":
                wildcard = True
        targets = [
            {
                "name": entry.get("name") or "mcp",
                "host": _strip_userinfo(entry.get("mcp", {}).get("host", "")),
            }
            for entry in (payload.get("mcp") or {}).get("targets", []) or []
        ]
        return {"providers": providers, "wildcard_models": wildcard, "targets": targets}

    async def models(self) -> list[dict[str, Any]]:
        """Whatever the provider behind the gateway is willing to serve."""
        async with httpx.AsyncClient(timeout=15) as client:
            response = await client.get(f"{self.base_url}/v1/models")
            response.raise_for_status()
            payload = response.json()
        # A "*" entry is the gateway saying "any model", not something to pick.
        return [item for item in payload.get("data", []) if item.get("id") not in {None, "", "*"}]

    async def mcp_tools(self, url: str | None = None) -> list[dict[str, Any]]:
        """One handshake against an MCP endpoint, for the tool picker."""
        url = url or settings.mcp_url
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        }
        async with httpx.AsyncClient(timeout=15) as client:
            handshake = await client.post(
                url,
                headers=headers,
                json=_rpc(
                    1,
                    "initialize",
                    {
                        "protocolVersion": "2025-06-18",
                        "capabilities": {},
                        "clientInfo": {"name": "nautionette", "version": settings.version},
                    },
                ),
            )
            handshake.raise_for_status()
            session = handshake.headers.get("mcp-session-id")
            if session:
                headers["Mcp-Session-Id"] = session
            await client.post(url, headers=headers, json=_rpc(None, "notifications/initialized"))
            listing = await client.post(url, headers=headers, json=_rpc(2, "tools/list"))
            listing.raise_for_status()
            body = _rpc_result(listing)
        return [
            {
                "name": tool.get("name", "?"),
                "description": (tool.get("description") or "").strip().split("\n")[0][:200],
            }
            for tool in body.get("tools", [])
        ]


class ModelCatalogClient:
    """The upstream list of models, for when the gateway is configured as "*"."""

    CATALOGS = {"openrouter": "https://openrouter.ai/api/v1/models"}

    async def models(self, provider: str) -> list[dict[str, Any]]:
        url = self.CATALOGS.get(provider)
        if not url:
            return []
        async with httpx.AsyncClient(timeout=25) as client:
            response = await client.get(url)
            response.raise_for_status()
            payload = response.json()
        out: list[dict[str, Any]] = []
        for item in payload.get("data", []):
            identifier = item.get("id")
            if not identifier:
                continue
            out.append(
                {
                    "id": identifier,
                    "name": item.get("name") or identifier,
                    "context_length": item.get("context_length"),
                    "created": item.get("created"),
                }
            )
        return out


def _strip_userinfo(url: str) -> str:
    """A host may carry credentials; the picker only ever needs the address."""
    if "@" not in url or "://" not in url:
        return url
    scheme, rest = url.split("://", 1)
    return f"{scheme}://{rest.rsplit('@', 1)[1]}"


def _rpc(request_id: int | None, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    message: dict[str, Any] = {"jsonrpc": "2.0", "method": method, "params": params or {}}
    if request_id is not None:
        message["id"] = request_id
    return message


def _rpc_result(response: httpx.Response) -> dict[str, Any]:
    """Streamable HTTP answers either as JSON or as a one-frame SSE body."""
    text = response.text
    if response.headers.get("content-type", "").startswith("text/event-stream"):
        for line in text.splitlines():
            if line.startswith("data:"):
                return json.loads(line[5:].strip()).get("result", {})
        return {}
    return json.loads(text).get("result", {})


broker = BrokerClient()
authoring = AuthoringClient()
gateway = GatewayClient()
model_catalog = ModelCatalogClient()
