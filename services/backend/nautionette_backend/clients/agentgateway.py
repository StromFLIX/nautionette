"""agentgateway: the model routes, the MCP federation, and its own config store."""

from __future__ import annotations

import contextlib
import json
from typing import Any
from urllib.parse import quote

import httpx

from ..config import settings
from .http import shared, upstream_problem


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


def public_target(entry: dict[str, Any]) -> dict[str, Any]:
    """Expose editable launch metadata, but never environment values or HTTP auth."""
    if "stdio" in entry:
        stdio = entry["stdio"]
        return {
            "name": entry.get("name") or "mcp",
            "host": "",
            "transport": "stdio",
            "command": stdio.get("cmd", ""),
            "args": [arg.replace("$$", "$") for arg in stdio.get("args", [])],
            "env": {key: None for key in stdio.get("env", {})},
        }
    return {
        "name": entry.get("name") or "mcp",
        "host": _strip_userinfo(entry.get("mcp", {}).get("host", "")),
    }


def _rpc(request_id: int | None, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    message: dict[str, Any] = {"jsonrpc": "2.0", "method": method, "params": params or {}}
    if request_id is not None:
        message["id"] = request_id
    return message


def _rpc_result(response: httpx.Response) -> dict[str, Any]:
    """Streamable HTTP answers either as JSON or as a one-frame SSE body."""

    def result(payload: Any) -> dict[str, Any]:
        if not isinstance(payload, dict) or not isinstance(payload.get("result", {}), dict):
            raise ValueError("Invalid MCP response")
        if "error" in payload:
            raise ValueError("MCP request failed")
        return payload.get("result", {})

    text = response.text
    if response.headers.get("content-type", "").startswith("text/event-stream"):
        for line in text.splitlines():
            if line.startswith("data:"):
                payload = json.loads(line[5:].strip())
                parsed = result(payload)
                if "result" in payload:
                    return parsed
        return {}
    return result(json.loads(text))


class GatewayClient:
    def __init__(self, base_url: str | None = None) -> None:
        self.base_url = (base_url or settings.gateway_url).rstrip("/")

    async def health(self) -> dict[str, Any]:
        # agentgateway answers on /v1/models once a provider is configured.
        response = await shared().get(f"{self.base_url}/v1/models", timeout=5)
        return {
            "status": "ok" if response.status_code < 500 else "degraded",
            "code": response.status_code,
        }

    async def config(self) -> dict[str, Any]:
        """The gateway's own view of itself: which upstream, which MCP targets.

        Only the naming is taken. Anything that could carry a key stays here.
        """
        # The effective view includes resources layered in through hybrid
        # storage; /api/config contains only the file-owned baseline.
        response = await shared().get(f"{self.base_url}/api/config/effective", timeout=10)
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
        targets = [public_target(entry) for entry in (payload.get("mcp") or {}).get("targets", []) or []]
        return {
            "providers": providers,
            "wildcard_models": wildcard,
            "model_routes": model_routes,
            "targets": targets,
        }

    async def runtime(self) -> dict[str, Any]:
        response = await shared().get(f"{self.base_url}/api/runtime", timeout=10)
        response.raise_for_status()
        return response.json()

    async def config_resources(self, kind: str) -> list[dict[str, Any]]:
        response = await shared().get(f"{self.base_url}/api/config/resources/{kind}", timeout=10)
        response.raise_for_status()
        return response.json().get("resources", [])

    async def put_config_resources(self, kind: str, values: list[dict[str, Any]]) -> list[dict[str, Any]]:
        response = await shared().put(
            f"{self.base_url}/api/config/resources/{kind}",
            json={"resources": [{"value": value} for value in values]},
            timeout=20,
        )
        response.raise_for_status()
        return response.json().get("resources", [])

    async def delete_config_resource(self, kind: str, resource_id: str) -> None:
        encoded = quote(resource_id, safe="")
        response = await shared().delete(f"{self.base_url}/api/config/resources/{kind}/{encoded}", timeout=20)
        response.raise_for_status()

    async def integration_models(self, instance: str) -> dict[str, Any]:
        """Read a provider's own model list through the route configured for it."""
        encoded = quote(instance, safe="")
        response = await shared().get(
            f"{self.base_url}/_nautionette/integrations/{encoded}/models", timeout=20
        )
        response.raise_for_status()
        return response.json()

    async def test_model(self, model: str, name: str, credential: str) -> dict[str, Any]:
        """Make one small generation to prove an integration's auth and routing."""
        use_responses = model.startswith("copilot/")
        endpoint = "responses" if use_responses else "chat/completions"
        request = {
            "model": model,
            "stream": False,
            **(
                {"input": "Reply with OK.", "max_output_tokens": 32}
                if use_responses
                else {
                    "messages": [{"role": "user", "content": "Reply with OK."}],
                    "max_tokens": 32,
                }
            ),
        }
        response = await shared().post(
            f"{self.base_url}/v1/{endpoint}",
            json=request,
            timeout=90,
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

    async def chat_title(self, model: str, instructions: str, text: str) -> str:
        """One tool-free generation, without starting a coding-agent container."""
        use_responses = model.startswith("copilot/")
        endpoint = "responses" if use_responses else "chat/completions"
        request = {
            "model": model,
            "stream": False,
            **(
                {"instructions": instructions, "input": text, "max_output_tokens": 1024}
                if use_responses
                else {
                    "messages": [
                        {"role": "system", "content": instructions},
                        {"role": "user", "content": text},
                    ],
                    "max_completion_tokens": 1024,
                }
            ),
        }
        response = await shared().post(f"{self.base_url}/v1/{endpoint}", json=request, timeout=30)
        response.raise_for_status()
        payload = response.json()
        if use_responses:
            return "".join(
                part.get("text", "")
                for item in payload.get("output", [])
                if item.get("type") == "message"
                for part in item.get("content", [])
                if part.get("type") == "output_text"
            )
        choices = payload.get("choices") or []
        return (choices[0].get("message", {}).get("content") or "") if choices else ""

    async def models(self) -> list[dict[str, Any]]:
        """Whatever the provider behind the gateway is willing to serve."""
        response = await shared().get(f"{self.base_url}/v1/models", timeout=15)
        response.raise_for_status()
        payload = response.json()
        # Wildcard entries describe integration routes, not selectable models.
        return [item for item in payload.get("data", []) if item.get("id") and "*" not in item["id"]]

    async def mcp_tools(
        self, url: str | None = None, extra: dict[str, str] | None = None, *, timeout: float = 90
    ) -> list[dict[str, Any]]:
        """One handshake against an MCP endpoint, for the tool picker."""
        url = url or settings.mcp_url
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            **(extra or {}),
        }
        client = shared()
        handshake = await client.post(
            url,
            headers=headers,
            timeout=timeout,
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
        try:
            version = _rpc_result(handshake).get("protocolVersion")
            if not isinstance(version, str) or not version:
                raise ValueError("the endpoint did not answer as an MCP server")
            headers["MCP-Protocol-Version"] = version
            initialized = await client.post(
                url, headers=headers, timeout=15, json=_rpc(None, "notifications/initialized")
            )
            initialized.raise_for_status()
            tools = []
            cursor = None
            seen = set()
            for request_id in range(2, 102):
                listing = await client.post(
                    url,
                    headers=headers,
                    timeout=timeout,
                    json=_rpc(request_id, "tools/list", {"cursor": cursor} if cursor else {}),
                )
                listing.raise_for_status()
                body = _rpc_result(listing)
                if not isinstance(body.get("tools"), list) or any(
                    not isinstance(tool, dict)
                    or not isinstance(tool.get("name"), str)
                    or not isinstance(tool.get("description", ""), (str, type(None)))
                    for tool in body["tools"]
                ):
                    raise ValueError("MCP tools/list did not return tools")
                tools.extend(
                    {
                        "name": tool.get("name", "?"),
                        "description": (tool.get("description") or "").strip().split("\n")[0][:200],
                    }
                    for tool in body["tools"]
                )
                cursor = body.get("nextCursor")
                if not cursor:
                    return tools
                if not isinstance(cursor, str):
                    raise ValueError("Invalid MCP pagination cursor")
                if cursor in seen:
                    raise ValueError("MCP tools/list repeated a cursor")
                seen.add(cursor)
            raise ValueError("MCP tools/list exceeded the page limit")
        finally:
            # Catalog and test sessions must not leave stdio child processes alive.
            if session:
                with contextlib.suppress(httpx.HTTPError):
                    await client.delete(url, headers=headers, timeout=5)


gateway = GatewayClient()
