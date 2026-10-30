"""Exercise the real MCP and Temporal transports, not only service fakes."""

import json
from types import SimpleNamespace

import httpx
import pytest
from nautionette_backend.clients.agentgateway import GatewayClient
from nautionette_backend.clients.temporal_server import TemporalGateway
from nautionette_backend.config import settings
from temporalio.common import WorkflowIDReusePolicy
from temporalio.exceptions import WorkflowAlreadyStartedError


@pytest.mark.parametrize("mode", ["structured", "text", "sse", "error", "oversized"])
async def test_dashboard_read_transport_is_bounded_and_closes_sessions(http, mode):
    seen = []

    def respond(request):
        if request.method == "DELETE":
            seen.append("DELETE")
            assert request.headers["mcp-session-id"] == "dashboard-session"
            return httpx.Response(200)
        body = json.loads(request.content)
        seen.append(body["method"])
        if body["method"] == "initialize":
            return httpx.Response(
                200,
                headers={"mcp-session-id": "dashboard-session"},
                json={
                    "result": {"protocolVersion": "2025-06-18"},
                },
            )
        assert request.headers["mcp-protocol-version"] == "2025-06-18"
        if body["method"] == "notifications/initialized":
            return httpx.Response(202)
        assert body["params"] == {"name": "example_read", "arguments": {"limit": 2}}
        result = {"structuredContent": {"count": 2}}
        if mode == "text":
            result = {"content": [{"type": "text", "text": '{"count":2}'}]}
        if mode == "error":
            result = {"isError": True, "content": [{"type": "text", "text": "private failure"}]}
        if mode == "oversized":
            result = {"content": [{"type": "text", "text": "x" * 524288}]}
        if mode == "sse":
            return httpx.Response(
                200,
                headers={"content-type": "text/event-stream"},
                text=(
                    'data: {"method":"notifications/progress"}\n\n'
                    + "data: "
                    + json.dumps({"result": result})
                    + "\n\n"
                ),
            )
        return httpx.Response(200, json={"result": result})

    http[settings.mcp_url] = respond
    if mode in {"error", "oversized"}:
        with pytest.raises(ValueError) as error:
            await GatewayClient().call_read_tool("example_read", {"limit": 2})
        assert "private failure" not in str(error.value)
    else:
        assert await GatewayClient().call_read_tool("example_read", {"limit": 2}) == {"count": 2}
    assert seen == ["initialize", "notifications/initialized", "tools/call", "DELETE"]


async def test_read_tool_discovery_preserves_explicit_annotations_and_input_schema(http):
    def respond(request):
        body = json.loads(request.content)
        if body["method"] == "initialize":
            return httpx.Response(200, json={"result": {"protocolVersion": "2025-06-18"}})
        if body["method"] == "notifications/initialized":
            return httpx.Response(202)
        return httpx.Response(
            200,
            json={
                "result": {
                    "tools": [
                        {
                            "name": "read",
                            "annotations": {"readOnlyHint": True},
                            "inputSchema": {"type": "object", "additionalProperties": False},
                        }
                    ]
                }
            },
        )

    http[settings.mcp_url] = respond
    tool = (await GatewayClient().mcp_tools(detailed=True))[0]
    assert tool["annotations"] == {"readOnlyHint": True}
    assert tool["inputSchema"]["additionalProperties"] is False
    assert set((await GatewayClient().mcp_tools())[0]) == {"name", "description"}


@pytest.mark.parametrize("duplicate", [False, True])
async def test_temporal_dashboard_request_forbids_reuse_even_after_completion(monkeypatch, duplicate):
    gateway = TemporalGateway()
    recorded = []

    class Client:
        async def start_workflow(self, workflow, payload, **kwargs):
            recorded.append(kwargs)
            if duplicate:
                raise WorkflowAlreadyStartedError(kwargs["id"], workflow, run_id="original-run")
            return SimpleNamespace(id=kwargs["id"], result_run_id="original-run")

    async def connect():
        return Client()

    async def describe(workflow_id):
        return {"workflow_id": workflow_id, "run_id": "original-run", "status": "COMPLETED"}

    monkeypatch.setattr(gateway, "client", connect)
    monkeypatch.setattr(gateway, "describe", describe)
    result = await gateway.start_once("test", "stable-request", {"value": 2})
    assert recorded[0]["id_reuse_policy"] == WorkflowIDReusePolicy.REJECT_DUPLICATE
    assert recorded[0]["id"] == "stable-request"
    assert result == {"workflow_id": "stable-request", "run_id": "original-run", "reused": duplicate}
