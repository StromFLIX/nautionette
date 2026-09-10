"""The real gateway client redacts config and closes discovery sessions."""

import json

import httpx
import pytest
from nautionette_backend.clients.agentgateway import GatewayClient, _rpc_result


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
