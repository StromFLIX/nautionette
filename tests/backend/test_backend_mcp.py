import json

import pytest
from fastapi.testclient import TestClient
from nautionette_backend import main

from ..conftest import INTERNAL_TOKEN


def decoded(result):
    return json.loads(result.content[0].text)


async def test_backend_tools_cover_diagnostics_and_controls_without_proxy_or_auth_arguments(backend):
    tools = await main.backend_mcp.list_tools(None, None)
    names = {tool.name for tool in tools.tools}
    assert {
        "system_status",
        "events_recent",
        "deploy_workflow",
        "run_workflow",
        "read_run",
        "restart_workers",
        "get_model_integrations",
        "put_mcp_server",
        "list_agents",
        "get_agent",
        "create_agent",
        "update_agent",
        "delete_agent",
    } <= names
    assert "internal_agent_call" not in names
    assert "events_stream" not in names
    assert not any("chat" in name for name in names)
    for tool in tools.tools:
        assert "token" not in tool.input_schema["properties"]
        assert "authorization" not in tool.input_schema["properties"]


async def test_backend_tool_executes_the_real_api_and_preserves_validation_errors(backend):
    result = await main.backend_mcp.call("system_status", {})
    assert result.is_error is False
    assert decoded(result)["components"]
    result = await main.backend_mcp.call("deploy_workflow", {"name": "demo"})
    assert result.is_error is True


async def test_saved_agents_use_the_same_config_semantics_through_mcp(backend):
    created = await main.backend_mcp.call(
        "create_agent", {"body": {"name": "Writer", "config": {"tools": []}}}
    )
    assert not created.is_error
    agent_id = decoded(created)["id"]
    configured = await main.backend_mcp.call("put_settings", {"body": {"default_agent_id": agent_id}})
    assert not configured.is_error
    listing = decoded(await main.backend_mcp.call("list_agents", {}))
    assert listing["chat_defaults"]["agent_id"] == agent_id
    assert listing["chat_defaults"]["tools"] == []
    invalid = await main.backend_mcp.call(
        "update_agent", {"agent_id": agent_id, "body": {"name": "Changed", "config": {"tools": "all"}}}
    )
    assert invalid.is_error
    saved = decoded(await main.backend_mcp.call("get_agent", {"agent_id": agent_id}))
    assert saved["name"] == "Writer" and saved["config"]["tools"] == []
    inherited = await main.backend_mcp.call("update_agent", {"agent_id": agent_id, "body": {"config": {}}})
    assert not inherited.is_error
    assert decoded(inherited)["resolved"]["tools"] is None
    assert not (await main.backend_mcp.call("delete_agent", {"agent_id": agent_id})).is_error
    assert decoded(await main.backend_mcp.call("list_agents", {}))["default_agent_id"] is None


async def test_schedule_tool_exposes_and_accepts_a_human_recurrence(backend):
    backend.authoring.add_workflow("demo", manifest={"timeout_minutes": 12})
    tools = await main.backend_mcp.list_tools(None, None)
    tool = next(item for item in tools.tools if item.name == "schedule_workflow")
    body = tool.input_schema["properties"]["body"]
    alternatives = {item["$ref"].rsplit("/", 1)[-1] for item in body["oneOf"]}
    assert alternatives == {
        "HourlySchedule",
        "DailySchedule",
        "WeeklySchedule",
        "MonthlySchedule",
    }
    assert set(body["discriminator"]["mapping"].values()) == {f"#/$defs/{name}" for name in alternatives}
    daily = tool.input_schema["$defs"]["DailySchedule"]
    assert {"frequency", "at", "timezone"} <= set(daily["required"])
    assert "cron" not in daily["properties"]

    result = await main.backend_mcp.call(
        "schedule_workflow",
        {
            "name": "demo",
            "body": {
                "frequency": "weekly",
                "at": "07:15",
                "days": ["tuesday", "thursday"],
                "timezone": "Europe/Berlin",
            },
        },
    )
    assert result.is_error is False
    assert decoded(result)["description"] == "Tue, Thu at 07:15"
    assert backend.temporal.schedule_specs["demo"]["input"] == {}


async def test_agent_can_run_and_read_results_without_an_approval(backend):
    deployed = await main.backend_mcp.call(
        "deploy_workflow", {"name": "demo", "body": {"code": "MANIFEST = {}"}}
    )
    assert not deployed.is_error
    assert decoded(deployed)["ready"] is True
    assert backend.authoring.drafts == {}
    assert backend.broker.restarts == 1
    started = await main.backend_mcp.call("run_workflow", {"name": "demo", "body": {"input": {"value": 3}}})
    assert not started.is_error
    workflow_id = decoded(started)["workflow_id"]
    assert backend.temporal.started[0]["input"] == {"value": 3}
    backend.temporal.histories[workflow_id] = [
        {"id": 1, "event": "activity.failed", "error": {"message": "bad input"}}
    ]
    detail = await main.backend_mcp.call("read_run", {"workflow_id": workflow_id})
    assert decoded(detail)["events"][0]["error"]["message"] == "bad input"


@pytest.mark.parametrize("name", ["../system", "..", "demo/../../settings"])
async def test_tool_cannot_turn_path_parameters_into_other_operations(backend, name):
    result = await main.backend_mcp.call("get_workflow", {"name": name})
    assert result.is_error


def test_mcp_endpoint_rejects_anonymous_requests(anonymous):
    assert (
        anonymous.post("/mcp/", json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"}).status_code == 401
    )


def test_real_mcp_transport_handshake_and_tool_call(backend):
    with TestClient(main.app) as client:
        client.headers.update(
            {"Authorization": f"Bearer {INTERNAL_TOKEN}", "Accept": "application/json, text/event-stream"}
        )
        response = client.post(
            "/mcp/",
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-03-26",
                    "capabilities": {},
                    "clientInfo": {"name": "test", "version": "1"},
                },
            },
        )
        assert response.status_code == 200
        assert response.json()["result"]["serverInfo"]["name"] == "nautionette-backend"
        listed = client.post("/mcp/", json={"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
        assert any(tool["name"] == "deploy_workflow" for tool in listed.json()["result"]["tools"])
        called = client.post(
            "/mcp/",
            json={
                "jsonrpc": "2.0",
                "id": 3,
                "method": "tools/call",
                "params": {"name": "restart_workers", "arguments": {}},
            },
        )
        assert called.status_code == 200
        assert not called.json()["result"]["isError"]
        assert backend.broker.restarts == 1
