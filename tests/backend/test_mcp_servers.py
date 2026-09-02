"""MCP servers: the targets agentgateway federates onto one endpoint."""

from __future__ import annotations

import pytest

from .fakes import http_error

LINEAR = "https://mcp.linear.test/mcp"


@pytest.fixture
def reachable(backend):
    backend.gateway.tools = {"": [], LINEAR: [{"name": "search", "description": "Find an issue"}]}
    return backend


def test_a_server_from_the_gateway_config_file_cannot_be_edited(client, backend):
    backend.gateway.file_targets = [{"name": "workflows", "host": "http://workflow-mcp:8000/mcp"}]
    payload = client.get("/api/mcp-servers").json()
    assert payload["servers"] == [
        {
            "name": "workflows",
            "url": "http://workflow-mcp:8000/mcp",
            "managed": False,
            "credential": {"mode": "none", "variable": ""},
            "tool_count": 0,
        }
    ]
    assert payload["writable"] is True


def test_adding_a_server_probes_it_before_writing_it(client, reachable):
    response = client.put("/api/mcp-servers/linear", json={"url": LINEAR})
    assert response.status_code == 200
    assert reachable.gateway.resources["mcp.target"]["linear"] == {
        "name": "linear",
        "mcp": {"host": LINEAR},
    }
    server = response.json()["servers"][0]
    assert server["managed"] is True
    assert server["tool_count"] == 0  # the federated endpoint has not picked it up yet


def test_a_token_is_stored_with_the_target(client, reachable):
    client.put("/api/mcp-servers/linear", json={"url": LINEAR, "token": "lin_api_1"})
    target = reachable.gateway.resources["mcp.target"]["linear"]
    assert target["policies"] == {"backendAuth": {"key": {"value": "lin_api_1"}}}
    server = client.get("/api/mcp-servers").json()["servers"][0]
    assert server["credential"] == {"mode": "stored", "variable": ""}


def test_a_named_variable_is_reported_as_belonging_to_the_gateway(client, reachable):
    client.put("/api/mcp-servers/linear", json={"url": LINEAR, "token": "$LINEAR_TOKEN"})
    server = client.get("/api/mcp-servers").json()["servers"][0]
    assert server["credential"] == {"mode": "environment", "variable": "LINEAR_TOKEN"}


def test_changing_the_url_does_not_need_the_token_retyped(client, reachable):
    client.put("/api/mcp-servers/linear", json={"url": LINEAR, "token": "lin_api_1"})
    reachable.gateway.tools["https://mcp.linear.test/v2/mcp"] = []
    client.put("/api/mcp-servers/linear", json={"url": "https://mcp.linear.test/v2/mcp"})
    target = reachable.gateway.resources["mcp.target"]["linear"]
    assert target["mcp"]["host"] == "https://mcp.linear.test/v2/mcp"
    assert target["policies"]["backendAuth"]["key"]["value"] == "lin_api_1"


def test_a_server_that_cannot_answer_is_never_written(client, backend):
    response = client.put("/api/mcp-servers/linear", json={"url": LINEAR})
    assert response.status_code == 400
    assert response.json()["detail"] == "The server answered HTTP 404."
    assert backend.gateway.resources.get("mcp.target", {}) == {}


def test_a_server_asking_for_a_key_the_gateway_holds_is_not_a_verdict(client, backend, live, monkeypatch):
    async def refuse(url=None, extra=None):
        raise http_error(401, "unauthorized")

    monkeypatch.setattr(live.gateway, "mcp_tools", refuse)
    response = client.put("/api/mcp-servers/linear", json={"url": LINEAR, "token": "$LINEAR_TOKEN"})
    assert response.status_code == 200


def test_an_endpoint_that_does_not_speak_mcp_is_refused(client, backend, live, monkeypatch):
    async def not_mcp(url=None, extra=None):
        raise ValueError("the endpoint did not answer as an MCP server")

    monkeypatch.setattr(live.gateway, "mcp_tools", not_mcp)
    response = client.put("/api/mcp-servers/linear", json={"url": LINEAR})
    assert response.json()["detail"] == "That endpoint does not speak MCP."


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1:9000/mcp",
        "http://localhost:9000/mcp",
        "http://agentgateway.test:4000/mcp",
        "http://[::]:9000/mcp",
    ],
)
def test_a_target_may_not_point_back_inside_the_stack(client, backend, url):
    response = client.put("/api/mcp-servers/linear", json={"url": url})
    assert response.status_code == 400
    assert backend.gateway.resources.get("mcp.target", {}) == {}


def test_a_name_the_config_file_already_uses_is_a_conflict(client, reachable):
    reachable.gateway.file_targets = [{"name": "linear", "host": "http://baseline/mcp"}]
    response = client.put("/api/mcp-servers/linear", json={"url": LINEAR})
    assert response.status_code == 409
    assert response.json()["detail"] == "linear is defined in the gateway config"


def test_a_malformed_name_or_url_is_refused(client, reachable):
    assert client.put("/api/mcp-servers/Not Valid", json={"url": LINEAR}).status_code == 400
    assert client.put("/api/mcp-servers/linear", json={"url": "ftp://x"}).status_code == 400


def test_removing_a_server_takes_its_target(client, reachable):
    client.put("/api/mcp-servers/linear", json={"url": LINEAR})
    assert client.delete("/api/mcp-servers/linear").json()["servers"] == []
    assert reachable.gateway.resources["mcp.target"] == {}


def test_removing_a_server_the_app_does_not_own_is_a_404(client, backend):
    backend.gateway.file_targets = [{"name": "workflows", "host": "http://workflow-mcp:8000/mcp"}]
    assert client.delete("/api/mcp-servers/workflows").status_code == 404
    assert client.delete("/api/mcp-servers/nothing").status_code == 404


def test_testing_a_server_reports_what_it_answered(client, reachable):
    client.put("/api/mcp-servers/linear", json={"url": LINEAR})
    assert client.post("/api/mcp-servers/linear/test").json() == {
        "ok": True,
        "status": 200,
        "message": "Answered with 1 tools.",
    }


def test_testing_an_unknown_server_is_a_404(client, backend):
    assert client.post("/api/mcp-servers/nothing/test").status_code == 404


def test_a_read_only_gateway_refuses_both_writes(client, reachable):
    reachable.gateway.storage_mode = "static"
    assert client.put("/api/mcp-servers/linear", json={"url": LINEAR}).status_code == 409
    assert client.delete("/api/mcp-servers/linear").status_code == 409


def test_tool_counts_follow_the_federated_endpoint(client, reachable):
    client.put("/api/mcp-servers/linear", json={"url": LINEAR})
    reachable.gateway.tools[""] = [{"name": "linear_search", "description": ""}]
    assert client.get("/api/mcp-servers").json()["servers"][0]["tool_count"] == 1
