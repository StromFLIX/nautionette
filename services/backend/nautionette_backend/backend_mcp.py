"""Authenticated MCP tools backed by the app's existing HTTP operations."""

from __future__ import annotations

import json
from contextlib import asynccontextmanager
from typing import Any
from urllib.parse import quote

import httpx
import jsonschema
from fastapi import APIRouter, FastAPI
from fastapi.routing import APIRoute
from mcp import types
from mcp.server.lowlevel import Server
from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
from mcp.server.transport_security import TransportSecuritySettings
from starlette.applications import Starlette
from starlette.datastructures import Headers
from starlette.responses import JSONResponse
from starlette.routing import Route

from .config import settings
from .events import bus
from .security import token_matches

_PREFIXES = (
    "/api/workflows",
    "/api/runs",
    "/api/drafts",
    "/api/system",
    "/api/settings",
    "/api/model-integrations",
    "/api/mcp-servers",
)
_EXACT = {"/api/catalog", "/api/events/recent"}

INSTRUCTIONS = (
    "Operate and diagnose Nautionette directly. Do not ask for routine approval or permission. "
    "Deploy validated workflows, run them with relevant inputs, inspect run history and results, "
    "and repair and redeploy failures. Use system_status and events_recent to diagnose services; "
    "inspect settings and integration schemas before changing their configuration. "
    "The body argument is the HTTP request body. Tools enforce the same validation as the app. "
    "A deployment is ready only when ready=true; inspect worker_restart on failure. "
    "Never claim a run succeeded until its results confirm it. Do not expose credentials."
)


def _schema(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: item.replace("#/components/schemas/", "#/$defs/") if key == "$ref" else _schema(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_schema(item) for item in value]
    return value


class BackendMCP:
    def __init__(self, app: FastAPI, routers: tuple[APIRouter, ...]) -> None:
        self.app = app
        self.operations: dict[str, dict[str, Any]] = {}
        self.tools: list[types.Tool] = []
        document = app.openapi()
        definitions = _schema(document.get("components", {}).get("schemas", {}))
        for route in (route for router in routers for route in router.routes):
            if not isinstance(route, APIRoute) or not (
                route.path in _EXACT
                or any(route.path == prefix or route.path.startswith(prefix + "/") for prefix in _PREFIXES)
            ):
                continue
            for method in sorted(route.methods or []):
                operation = document["paths"][route.path].get(method.lower())
                if not operation:
                    continue
                parameters = [
                    parameter
                    for parameter in operation.get("parameters", [])
                    if parameter["in"] in {"path", "query"} and parameter["name"] != "token"
                ]
                properties = {item["name"]: _schema(item["schema"]) for item in parameters}
                required = [item["name"] for item in parameters if item.get("required")]
                body = operation.get("requestBody", {})
                if content := body.get("content", {}).get("application/json"):
                    properties["body"] = _schema(content["schema"])
                    if body.get("required"):
                        required.append("body")
                schema = {
                    "type": "object",
                    "properties": properties,
                    "required": required,
                    "additionalProperties": False,
                    "$defs": definitions,
                }
                name = route.name
                if name in self.operations:
                    raise ValueError(f"Duplicate backend MCP tool: {name}")
                self.operations[name] = {
                    "method": method,
                    "path": route.path,
                    "parameters": parameters,
                    "schema": schema,
                }
                self.tools.append(
                    types.Tool(
                        name=name,
                        description=(
                            f"{operation.get('description') or operation.get('summary', name)}"
                            f"\n{method} {route.path}"
                        ),
                        input_schema=schema,
                        annotations=types.ToolAnnotations(read_only_hint=method == "GET"),
                    )
                )
        self.server = Server(
            "nautionette-backend",
            instructions=INSTRUCTIONS,
            on_list_tools=self.list_tools,
            on_call_tool=self.call_tool,
        )
        self.manager: StreamableHTTPSessionManager | None = None

        @asynccontextmanager
        async def lifespan(_: Starlette):
            self.manager = StreamableHTTPSessionManager(
                self.server,
                json_response=True,
                stateless=True,
                security_settings=TransportSecuritySettings(enable_dns_rebinding_protection=False),
            )
            try:
                async with self.manager.run():
                    yield
            finally:
                self.manager = None

        self.http_app = Starlette(
            routes=[Route("/", self, methods=["GET", "POST", "DELETE"])],
            lifespan=lifespan,
        )

    async def __call__(self, scope, receive, send) -> None:
        authorization = Headers(scope=scope).get("authorization", "")
        supplied = authorization[7:].strip() if authorization.lower().startswith("bearer ") else ""
        expected = [value for value in (settings.internal_token, settings.app_token) if value]
        if expected and not any(token_matches(supplied, value) for value in expected):
            await JSONResponse({"detail": "unauthorized"}, status_code=401)(scope, receive, send)
            return
        if self.manager is None:
            await JSONResponse({"detail": "MCP is starting"}, status_code=503)(scope, receive, send)
            return
        await self.manager.handle_request(scope, receive, send)

    async def list_tools(self, context, params) -> types.ListToolsResult:
        return types.ListToolsResult(tools=self.tools)

    async def call_tool(self, context, params: types.CallToolRequestParams) -> types.CallToolResult:
        return await self.call(params.name, params.arguments or {})

    async def call(self, name: str, arguments: dict[str, Any]) -> types.CallToolResult:
        operation = self.operations.get(name)
        if operation is None:
            return self.result({"error": "Unknown backend operation"}, error=True)
        try:
            jsonschema.validate(arguments, operation["schema"])
        except jsonschema.ValidationError as exc:
            return self.result({"error": exc.message}, error=True)
        path = operation["path"]
        query: dict[str, Any] = {}
        for parameter in operation["parameters"]:
            key = parameter["name"]
            if key not in arguments or arguments[key] is None:
                continue
            if parameter["in"] == "path":
                value = str(arguments[key])
                if value in {".", ".."} or any(character in value for character in ("/", "\\", "\x00")):
                    return self.result({"error": "Path parameters must be single path segments"}, error=True)
                path = path.replace("{" + key + "}", quote(value, safe=""))
            else:
                query[key] = arguments[key]
        headers = {"Authorization": f"Bearer {settings.app_token}"} if settings.app_token else {}
        request = httpx.Request(
            operation["method"],
            f"http://backend-mcp{path}",
            params=query,
            headers=headers,
            json=arguments.get("body"),
        )
        async with httpx.ASGITransport(app=self.app, raise_app_exceptions=False) as transport:
            response = await transport.handle_async_request(request)
            await response.aread()
        if operation["method"] != "GET":
            bus.publish("agent.backend_operation", {"tool": name, "status": response.status_code})
        try:
            payload = response.json()
        except ValueError:
            payload = {"status": response.status_code, "message": response.text[:1000]}
        return self.result(payload, error=response.status_code >= 400)

    @staticmethod
    def result(payload: Any, error: bool = False) -> types.CallToolResult:
        return types.CallToolResult(
            content=[types.TextContent(type="text", text=json.dumps(payload, default=str))],
            is_error=error,
        )
