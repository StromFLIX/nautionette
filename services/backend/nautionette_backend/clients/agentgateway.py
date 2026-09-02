"""agentgateway: the model routes, the MCP federation, and its own config store."""

from __future__ import annotations

import json
from typing import Any
from urllib.parse import quote

import httpx

from ..config import settings
from .http import upstream_problem


def _provider_name(provider: Any) -> str:
    """A model's provider is a name, a reference to one, or an inline definition."""
    if isinstance(provider, str):
        return provider
    if isinstance(provider, dict) and provider:
        return str(provider.get("reference") or next(iter(provider)))
    return ""


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
            # The effective view includes resources layered in through hybrid
            # storage; /api/config contains only the file-owned baseline.
            response = await client.get(f"{self.base_url}/api/config/effective")
            response.raise_for_status()
            payload = response.json()
        providers: list[str] = []
        wildcard = False
        model_routes: list[dict[str, str]] = []
        for entry in (payload.get("llm") or {}).get("models", []) or []:
            provider = _provider_name(entry.get("provider"))
            if provider:
                if provider not in providers:
                    providers.append(provider)
                if isinstance(entry.get("name"), str):
                    route = {"name": entry["name"], "provider": provider}
                    if isinstance(entry.get("id"), str):
                        route["id"] = entry["id"]
                    model_routes.append(route)
            if entry.get("name") == "*":
                wildcard = True
        targets = [
            {
                "name": entry.get("name") or "mcp",
                "host": _strip_userinfo(entry.get("mcp", {}).get("host", "")),
            }
            for entry in (payload.get("mcp") or {}).get("targets", []) or []
        ]
        return {
            "providers": providers,
            "wildcard_models": wildcard,
            "model_routes": model_routes,
            "targets": targets,
        }

    async def runtime(self) -> dict[str, Any]:
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.get(f"{self.base_url}/api/runtime")
            response.raise_for_status()
            return response.json()

    async def config_resources(self, kind: str) -> list[dict[str, Any]]:
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.get(f"{self.base_url}/api/config/resources/{kind}")
            response.raise_for_status()
            return response.json().get("resources", [])

    async def put_config_resources(
        self, kind: str, values: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        async with httpx.AsyncClient(timeout=20) as client:
            response = await client.put(
                f"{self.base_url}/api/config/resources/{kind}",
                json={"resources": [{"value": value} for value in values]},
            )
            response.raise_for_status()
            return response.json().get("resources", [])

    async def delete_config_resource(self, kind: str, resource_id: str) -> None:
        encoded = quote(resource_id, safe="")
        async with httpx.AsyncClient(timeout=20) as client:
            response = await client.delete(f"{self.base_url}/api/config/resources/{kind}/{encoded}")
            response.raise_for_status()

    async def integration_models(self, instance: str) -> dict[str, Any]:
        """Read a provider's own model list through the route configured for it."""
        encoded = quote(instance, safe="")
        async with httpx.AsyncClient(timeout=20) as client:
            response = await client.get(
                f"{self.base_url}/_nautionette/integrations/{encoded}/models"
            )
            response.raise_for_status()
            return response.json()

    async def test_model(self, model: str, name: str, credential: str) -> dict[str, Any]:
        """Make one small generation to prove an integration's auth and routing."""
        async with httpx.AsyncClient(timeout=90) as client:
            response = await client.post(
                f"{self.base_url}/v1/chat/completions",
                json={
                    "model": model,
                    "messages": [{"role": "user", "content": "Reply with OK."}],
                    "max_tokens": 32,
                    "stream": False,
                },
            )
        if response.status_code < 400:
            payload = response.json()
            return {
                "ok": True,
                "status": response.status_code,
                "model": payload.get("model") or model,
                "message": f"{name} answered through agentgateway.",
            }
        return {
            "ok": False,
            "status": response.status_code,
            "model": model,
            "message": upstream_problem(name, credential, response.status_code, response.text),
        }

    async def models(self) -> list[dict[str, Any]]:
        """Whatever the provider behind the gateway is willing to serve."""
        async with httpx.AsyncClient(timeout=15) as client:
            response = await client.get(f"{self.base_url}/v1/models")
            response.raise_for_status()
            payload = response.json()
        # Wildcard entries describe integration routes, not selectable models.
        return [item for item in payload.get("data", []) if item.get("id") and "*" not in item["id"]]

    async def mcp_tools(
        self, url: str | None = None, extra: dict[str, str] | None = None
    ) -> list[dict[str, Any]]:
        """One handshake against an MCP endpoint, for the tool picker."""
        url = url or settings.mcp_url
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            **(extra or {}),
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
            if not _rpc_result(handshake).get("protocolVersion"):
                raise ValueError("the endpoint did not answer as an MCP server")
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


gateway = GatewayClient()
