"""Stdio launch settings use gateway routes, never backend subprocesses."""

import httpx
import pytest
from nautionette_backend import mcp_servers

from .fakes import http_error

LINEAR = "https://mcp.linear.test/mcp"
STDIO = {
    "transport": "stdio",
    "command": "npx",
    "args": ["-y", "@brave/brave-search-mcp-server", "--transport", "stdio"],
    "env": {"BRAVE_API_KEY": "brave-test-secret"},
}


@pytest.fixture
def stdio_ready(backend, live, monkeypatch):
    original = live.gateway.mcp_tools

    async def tools(url=None, extra=None, *, timeout=90):
        if url and "/_nautionette/mcp-probes/" in url:
            routes = backend.gateway.resources["traffic.route"]
            route = next(r for r in routes.values() if url.endswith(r["matches"][0]["path"]["exact"]))
            assert route["backends"][0]["mcp"]["targets"][0]["stdio"]["clear_env"] is True
            return [{"name": "search", "description": "Search"}]
        return await original(url, extra, timeout=timeout)

    monkeypatch.setattr(live.gateway, "mcp_tools", tools)
    return backend


def test_stdio_is_probed_in_isolation_then_persisted_without_returning_secrets(client, stdio_ready):
    response = client.put("/api/mcp-servers/brave", json=STDIO)
    assert response.status_code == 200
    assert "brave-test-secret" not in response.text
    target = stdio_ready.gateway.resources["mcp.target"]["brave"]
    assert target == {
        "name": "brave",
        "stdio": {
            "cmd": "npx",
            "args": STDIO["args"],
            "env": {**mcp_servers.STDIO_ENV, **STDIO["env"]},
            "clear_env": True,
        },
    }
    assert stdio_ready.gateway.resources["traffic.route"] == {}
    assert [kind for kind, _ in stdio_ready.gateway.writes] == ["traffic.route", "mcp.target"]
    server = response.json()["servers"][0]
    assert server["transport"] == "stdio"
    assert server["command"] == "npx"
    assert server["args"] == STDIO["args"]
    assert server["env"]["BRAVE_API_KEY"] is None
    assert client.post("/api/mcp-servers/brave/test").json()["ok"] is True
    assert stdio_ready.gateway.resources["traffic.route"] == {}
    assert client.delete("/api/mcp-servers/brave").json()["servers"] == []


def test_stdio_env_keeps_replaces_and_removes_values(client, stdio_ready):
    client.put("/api/mcp-servers/brave", json=STDIO)
    changes = {**STDIO, "env": {"BRAVE_API_KEY": None, "EMPTY": "", "LITERAL": "$NOT_EXPANDED"}}
    assert client.put("/api/mcp-servers/brave", json=changes).status_code == 200
    env = stdio_ready.gateway.resources["mcp.target"]["brave"]["stdio"]["env"]
    assert env["BRAVE_API_KEY"] == "brave-test-secret"
    assert env["EMPTY"] == ""
    assert env["LITERAL"] == "$$NOT_EXPANDED"  # escaped for gateway config expansion
    changes.pop("env")
    assert client.put("/api/mcp-servers/brave", json=changes).status_code == 200
    assert stdio_ready.gateway.resources["mcp.target"]["brave"]["stdio"]["env"] == env
    changes["env"] = {"BRAVE_API_KEY": "replacement"}
    assert client.put("/api/mcp-servers/brave", json=changes).status_code == 200
    assert stdio_ready.gateway.resources["mcp.target"]["brave"]["stdio"]["env"] == {
        **mcp_servers.STDIO_ENV,
        "BRAVE_API_KEY": "replacement",
    }
    changes["env"] = {}
    client.put("/api/mcp-servers/brave", json=changes)
    assert stdio_ready.gateway.resources["mcp.target"]["brave"]["stdio"]["env"] == mcp_servers.STDIO_ENV


@pytest.mark.parametrize(
    "change",
    [
        {"transport": "sse"},
        {"command": "npx -y package"},
        {"command": "../server"},
        {"args": "-y package"},
        {"args": [1]},
        {"args": ["bad\u0000arg"]},
        {"env": []},
        {"env": {"INVALID=NAME": "x"}},
        {"env": {"KEY": 123}},
        {"env": {"KEY": "bad\u0000value"}},
        {"env": {"MISSING": None}},
    ],
)
def test_invalid_stdio_configuration_is_never_written(client, backend, change):
    response = client.put("/api/mcp-servers/brave", json={**STDIO, **change})
    assert response.status_code == 400
    assert backend.gateway.writes == []


def test_failed_stdio_probe_preserves_existing_target_and_cleans_route(client, backend):
    backend.gateway.tools[LINEAR] = []
    client.put("/api/mcp-servers/linear", json={"url": LINEAR})
    previous = backend.gateway.resources["mcp.target"]["linear"]
    response = client.put("/api/mcp-servers/linear", json=STDIO)
    assert response.status_code == 400
    assert "brave-test-secret" not in response.text
    assert backend.gateway.resources["mcp.target"]["linear"] == previous
    assert backend.gateway.resources["traffic.route"] == {}


@pytest.mark.parametrize(
    "exception", [ValueError("secret-process-output"), httpx.ReadTimeout("secret"), TimeoutError("timeout")]
)
def test_stdio_probe_errors_are_sanitized_and_cleaned(client, backend, live, monkeypatch, exception):
    async def fail(*args, **kwargs):
        raise exception

    monkeypatch.setattr(live.gateway, "mcp_tools", fail)
    response = client.put("/api/mcp-servers/brave", json=STDIO)
    assert response.status_code == 400
    assert "secret" not in response.text
    assert backend.gateway.resources["traffic.route"] == {}
    assert backend.gateway.resources.get("mcp.target", {}) == {}


def test_stdio_cleanup_failure_is_not_reported_as_a_success(client, stdio_ready, live, monkeypatch):
    async def fail(*args):
        raise http_error(503, "secret")

    monkeypatch.setattr(live.gateway, "delete_config_resource", fail)
    response = client.put("/api/mcp-servers/brave", json=STDIO)
    assert response.status_code == 502
    assert "Could not remove" in response.json()["detail"]
    assert stdio_ready.gateway.resources.get("mcp.target", {}) == {}


def test_stdio_rejected_route_keeps_original_error_when_cleanup_gets_404(client, backend, live, monkeypatch):
    backend.gateway.fail_kinds["traffic.route"] = http_error(422, "secret")

    async def missing(*args):
        raise http_error(404)

    monkeypatch.setattr(live.gateway, "delete_config_resource", missing)
    response = client.put("/api/mcp-servers/brave", json=STDIO)
    assert response.status_code == 502
    assert "HTTP 422" in response.json()["detail"]
    assert "secret" not in response.text


def test_stdio_respects_file_ownership_storage_mode_and_auth(client, backend, anonymous):
    assert anonymous.put("/api/mcp-servers/brave", json=STDIO).status_code == 401
    backend.gateway.file_targets = [{"name": "brave", "host": "", "transport": "stdio"}]
    assert client.put("/api/mcp-servers/brave", json=STDIO).status_code == 409
    backend.gateway.storage_mode = "static"
    assert client.put("/api/mcp-servers/other", json=STDIO).status_code == 409
    assert backend.gateway.writes == []


def test_transport_switch_does_not_carry_credentials_to_other_transport(client, stdio_ready):
    stdio_ready.gateway.tools[LINEAR] = []
    client.put("/api/mcp-servers/brave", json={"url": LINEAR, "token": "http-token"})
    assert client.put("/api/mcp-servers/brave", json=STDIO).status_code == 200
    assert "policies" not in stdio_ready.gateway.resources["mcp.target"]["brave"]
    assert client.put("/api/mcp-servers/brave", json={"url": LINEAR}).status_code == 200
    assert stdio_ready.gateway.resources["mcp.target"]["brave"] == {
        "name": "brave",
        "mcp": {"host": LINEAR},
    }


def test_stdio_tools_have_counts_without_direct_http_fallback(client, stdio_ready):
    client.put("/api/mcp-servers/brave", json=STDIO)
    stdio_ready.gateway.tools[""] = [{"name": "search", "description": ""}]
    assert client.get("/api/mcp-servers").json()["servers"][0]["tool_count"] == 1
    stdio_ready.gateway.file_targets = [{"name": "workflows", "host": "http://workflow-mcp/mcp"}]
    stdio_ready.gateway.tools[""] = [{"name": "brave_search", "description": ""}]
    servers = client.get("/api/mcp-servers").json()["servers"]
    assert next(server for server in servers if server["name"] == "brave")["tool_count"] == 1


async def test_restart_recovers_abandoned_probes_but_not_active_or_unrelated_routes(backend, monkeypatch):
    abandoned = "nautionette-mcp-probe-old"
    active = "nautionette-mcp-probe-active"
    backend.gateway.resources["traffic.route"] = {key: {"name": key} for key in [abandoned, active, "models"]}
    monkeypatch.setattr(mcp_servers, "_active_probes", {active})
    await mcp_servers.recover_stdio_probes()
    assert set(backend.gateway.resources["traffic.route"]) == {active, "models"}
