"""The real gateway client redacts config and closes discovery sessions."""

import asyncio
import json

import httpx
import pytest
from nautionette_backend.clients.agentgateway import GatewayClient, MCPHTTPError, _rpc_result


async def test_config_reports_stdio_metadata_but_never_env_values(http):
    http["http://gateway.test/api/config/effective"] = lambda _: httpx.Response(
        200,
        json={
            "mcp": {
                "targets": [
                    {
                        "name": "brave",
                        "stdio": {
                            "cmd": "npx",
                            "args": ["package"],
                            "env": {"BRAVE_API_KEY": "hidden"},
                            "clear_env": True,
                        },
                    }
                ]
            },
        },
    )
    result = await GatewayClient("http://gateway.test").config()
    assert "hidden" not in json.dumps(result)
    assert result["targets"] == [
        {
            "name": "brave",
            "host": "",
            "transport": "stdio",
            "command": "npx",
            "args": ["package"],
            "env": {"BRAVE_API_KEY": None},
        }
    ]


@pytest.mark.parametrize("failure", ["", "rpc", "http", "malformed", "timeout"])
async def test_mcp_sessions_are_closed_after_success_or_failure(http, failure):
    requests = []

    def respond(request):
        if request.method == "DELETE":
            requests.append("DELETE")
            assert request.headers["mcp-session-id"] == "session-1"
            return httpx.Response(200)
        body = json.loads(request.content)
        requests.append(body["method"])
        if body["method"] == "initialize":
            return httpx.Response(
                200,
                headers={"mcp-session-id": "session-1"},
                json={
                    "result": {"protocolVersion": "2025-06-18"},
                },
            )
        assert request.headers["mcp-protocol-version"] == "2025-06-18"
        if body["method"] == "notifications/initialized":
            return httpx.Response(202)
        if failure == "timeout":
            raise httpx.ReadTimeout("slow")
        if failure == "http":
            return httpx.Response(500)
        if failure == "rpc":
            return httpx.Response(200, json={"error": {"code": -32603, "message": "private"}})
        if failure == "malformed":
            return httpx.Response(200, json={"result": {}})
        return httpx.Response(200, json={"result": {"tools": [{"name": "search"}]}})

    http["http://gateway.test/mcp"] = respond
    client = GatewayClient("http://gateway.test")
    if failure:
        with pytest.raises((httpx.HTTPError, ValueError)):
            await client.mcp_tools("http://gateway.test/mcp")
    else:
        assert await client.mcp_tools("http://gateway.test/mcp") == [{"name": "search", "description": ""}]
    assert requests == ["initialize", "notifications/initialized", "tools/list", "DELETE"]


@pytest.mark.parametrize("failure", ["", "http", "rpc", "timeout"])
async def test_gateway_health_checks_tools_not_just_model_routes(http, monkeypatch, failure):
    http["http://gateway.test/v1/models"] = lambda _: httpx.Response(200, json={"data": []})
    client = GatewayClient("http://gateway.test")

    async def tools(url, *, timeout):
        assert url == "http://gateway.test/mcp"
        assert timeout <= 5
        if failure == "http":
            raise MCPHTTPError(
                httpx.Response(
                    401,
                    text="private expired credential details",
                    request=httpx.Request("POST", "http://gateway.test/mcp"),
                ),
                "initialize",
            )
        if failure == "rpc":
            raise ValueError("private upstream RPC failure")
        if failure == "timeout":
            raise TimeoutError("private upstream timeout")
        return [{"name": "healthy_read"}]

    monkeypatch.setattr(client, "mcp_tools", tools)
    result = await client.health()
    assert result["status"] == ("degraded" if failure else "ok")
    assert result["code"] == 200
    assert result["mcp"] == ({"status": "unavailable"} if failure else {"status": "ok", "tool_count": 1})
    assert "private" not in json.dumps(result)


async def test_gateway_health_bounds_the_entire_tool_handshake(http, monkeypatch):
    http["http://gateway.test/v1/models"] = lambda _: httpx.Response(200, json={"data": []})
    client = GatewayClient("http://gateway.test")
    real_timeout = asyncio.timeout

    def bounded(seconds):
        assert seconds == 5
        return real_timeout(0.01)

    async def stalled(*_args, **_kwargs):
        await asyncio.sleep(10)

    monkeypatch.setattr(client, "mcp_tools", stalled)
    monkeypatch.setattr("nautionette_backend.clients.agentgateway.asyncio.timeout", bounded)
    assert (await client.health())["status"] == "degraded"


async def test_mcp_tool_listing_follows_pagination(http):
    cursors = []

    def respond(request):
        body = json.loads(request.content)
        if body["method"] == "initialize":
            return httpx.Response(200, json={"result": {"protocolVersion": "2025-06-18"}})
        if body["method"] == "notifications/initialized":
            return httpx.Response(202)
        cursor = body["params"].get("cursor")
        cursors.append(cursor)
        return httpx.Response(
            200,
            json={
                "result": {
                    "tools": [{"name": "second" if cursor else "first"}],
                    **({} if cursor else {"nextCursor": "page-2"}),
                }
            },
        )

    http["http://gateway.test/mcp"] = respond
    tools = await GatewayClient().mcp_tools("http://gateway.test/mcp")
    assert [tool["name"] for tool in tools] == ["first", "second"]
    assert cursors == [None, "page-2"]


@pytest.mark.parametrize(
    "message,reason",
    [
        ("unknown resource", "authorization_denied"),
        ("session header is required for non-initialize requests", "missing_session_header"),
        ("unsupported MCP protocol version: secret", "unsupported_protocol"),
        ("private provider body with secret", "unclassified"),
    ],
)
def test_mcp_http_error_has_safe_stage_reason_and_numeric_code_only(message, reason):
    response = httpx.Response(
        400,
        json={"error": {"code": -32602, "message": message}},
        request=httpx.Request(
            "POST", "https://gateway.test/probe?secret=hidden", headers={"Authorization": "Bearer secret"}
        ),
    )
    error = MCPHTTPError(response, "initialize")
    assert error.stage == "initialize" and error.reason == reason and error.rpc_code == -32602
    assert "secret" not in str(error) and "hidden" not in str(error)
    assert "private provider body" not in str(error)


def test_sse_listing_skips_notifications_and_rejects_rpc_errors():
    response = httpx.Response(
        200,
        headers={"content-type": "text/event-stream"},
        text=('data: {"method":"notifications/progress"}\n\ndata: {"result":{"tools":[]}}\n\n'),
    )
    assert _rpc_result(response) == {"tools": []}
    response = httpx.Response(
        200, headers={"content-type": "text/event-stream"}, text=('data: {"error":{"message":"secret"}}\n\n')
    )
    with pytest.raises(ValueError, match="MCP request failed"):
        _rpc_result(response)
