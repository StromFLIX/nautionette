"""Backend orchestration: authentication, credential privacy, safe save/retry/cancel."""

import asyncio
import json
from types import SimpleNamespace

import httpx
import pytest
from nautionette_backend import mcp_oauth, mcp_servers

BASE = "/api/mcp-oauth"
CONFIG = {
    "transport": "stdio",
    "command": "npx",
    "args": ["-y", "trusted-test-mcp", "mcp"],
    "provider": "generic",
    "login_args": ["-y", "trusted-test-mcp", "login"],
    "setup_args": [],
    "env": {"SECRET": "$private-literal"},
    "desktop_bridge_version": 1,
}


@pytest.fixture
def oauth(backend, live, monkeypatch):
    monkeypatch.setattr(mcp_oauth, "_sessions", {})
    monkeypatch.setattr(mcp_oauth, "_lock", asyncio.Lock())
    calls, jobs, probes = [], {}, []
    fail = [None]

    async def control(method, path, payload=None):
        calls.append((method, path, payload))
        if fail[0]:
            raise fail[0]
        if path == "/health":
            return {"available": True, "version": 1}
        if path == "/sessions" and method == "POST":
            identifier = str(len(jobs) + 1)
            jobs[identifier] = {"status": "starting", "message": "Starting CLI"}
            return {"id": identifier}
        identifier = path.split("/")[2]
        if method == "DELETE":
            jobs[identifier]["status"] = "cancelled"
            return {"status": "cancelled"}
        if method == "POST":
            return {"status": "forwarded"}
        return jobs[identifier]

    async def probe(target):
        probes.append(target)
        return {"ok": True, "status": None, "message": "Synthetic MCP answered"}

    monkeypatch.setattr(live.gateway, "cli_oauth", control)
    monkeypatch.setattr(mcp_servers, "probe_stdio", probe)
    return SimpleNamespace(calls=calls, jobs=jobs, probes=probes, fail=fail)


def begin(client, payload=None, name="example"):
    response = client.post(BASE + "/start/" + name, json=payload or CONFIG)
    assert response.status_code == 200, response.text
    return response.json()["id"]


def test_web_mobile_and_old_desktop_are_rejected_before_cli_start(client, oauth):
    for version in [None, 0, "1", 2]:
        response = client.post(BASE + "/start/example", json={**CONFIG, "desktop_bridge_version": version})
        assert response.status_code == 400
        assert "desktop" in response.json()["detail"]
    assert not oauth.calls


def test_cli_oauth_is_not_enabled_through_backend_open_mode(client, oauth, monkeypatch):
    from nautionette_backend.config import settings

    monkeypatch.setattr(settings, "app_token", "")
    assert client.post(BASE + "/start/example", json=CONFIG).status_code == 403
    assert client.get(BASE + "/capabilities").json()["available"] is False
    assert not oauth.calls


def test_every_oauth_operation_requires_user_authentication(anonymous):
    for path, method in [
        ("/capabilities", "GET"),
        ("/start/example", "POST"),
        ("/login/unknown", "GET"),
        ("/callback/unknown", "POST"),
        ("/login/unknown", "DELETE"),
    ]:
        assert anonymous.request(method, BASE + path, json=CONFIG).status_code == 401


def test_capabilities_detect_gateway_compatibility_and_pin_workiq(client, oauth):
    capabilities = client.get(BASE + "/capabilities").json()
    assert capabilities["available"]
    assert capabilities["workiq"]["args"] == ["-y", "@microsoft/workiq@1.0.1", "mcp"]
    oauth.fail[0] = httpx.ConnectError("private-upstream-message")
    response = client.get(BASE + "/capabilities")
    assert not response.json()["available"]
    assert "private-upstream" not in response.text


def test_login_runs_in_gateway_and_only_saves_after_cli_success_and_mcp_probe(client, backend, oauth):
    identifier = begin(client)
    request = oauth.calls[0][2]
    assert request["env"]["HOME"] == "/data/mcp/oauth/example"
    assert request["env"]["SECRET"] == "$private-literal"
    assert request["login_args"] == CONFIG["login_args"]
    assert not backend.gateway.writes
    assert client.get(BASE + "/login/" + identifier).json()["status"] == "starting"
    oauth.jobs["1"] = {"status": "complete"}
    response = client.get(BASE + "/login/" + identifier)
    assert response.json()["status"] == "complete"
    assert len(oauth.probes) == 1
    target = backend.gateway.resources["mcp.target"]["example"]
    assert target["stdio"]["env"]["SECRET"] == "$private-literal"
    assert target["stdio"]["env"]["HOME"] == request["env"]["HOME"]
    public = client.get("/api/mcp-servers").text
    assert "private-literal" not in public
    assert json.loads(backend.db.one("SELECT * FROM mcp_cli_oauth")["config"]) == {
        "provider": "generic",
        "login_args": CONFIG["login_args"],
        "setup_args": [],
    }
    assert client.get(BASE + "/login/" + identifier).json()["status"] == "complete"
    assert len(oauth.probes) == 1
    assert not mcp_oauth._sessions[identifier].config


def test_failed_cli_or_probe_preserves_previous_mcp_config(client, backend, oauth, monkeypatch):
    identifier = begin(client)
    oauth.jobs["1"] = {"status": "failed", "message": "CLI failed"}
    assert client.get(BASE + "/login/" + identifier).json()["status"] == "failed"
    assert not backend.gateway.writes
    oauth.jobs["1"] = {"status": "complete"}

    async def failed(target):
        return {"ok": False, "message": "MCP not ready"}

    monkeypatch.setattr(mcp_servers, "probe_stdio", failed)
    assert client.get(BASE + "/login/" + identifier).status_code == 400
    assert not backend.gateway.resources.get("mcp.target")
    assert not backend.db.query("SELECT * FROM mcp_cli_oauth")


def test_workiq_requires_explicit_eula_and_selects_supported_browser_flow(client, oauth):
    config = {**CONFIG, "command": "npx", "args": [*mcp_oauth.WORKIQ_ARGS, "mcp"], "provider": "workiq"}
    assert client.post(BASE + "/start/example", json=config).status_code == 400
    assert not oauth.calls
    begin(client, {**config, "accept_eula": True})
    profile = oauth.calls[0][2]
    assert profile["setup_args"] == [
        [*mcp_oauth.WORKIQ_ARGS, "config", "set", "disableBrokeredAuth=true", "language=en"],
        [*mcp_oauth.WORKIQ_ARGS, "accept-eula"],
    ]
    assert profile["login_args"] == [*mcp_oauth.WORKIQ_ARGS, "auth", "login"]


def test_callback_forwards_only_fields_never_a_browser_supplied_destination(client, oauth):
    identifier = begin(client)
    callback = {
        "capture_id": "capture",
        "method": "POST",
        "fields": {"state": "state", "code": "single-use-secret"},
    }
    response = client.post(BASE + "/callback/" + identifier, json=callback)
    assert response.json() == {"status": "forwarded"}
    assert "single-use-secret" not in response.text
    assert oauth.calls[-1] == ("POST", "/sessions/1/callback", callback)
    response = client.post(
        BASE + "/callback/" + identifier, json={**callback, "url": "http://localhost/private"}
    )
    assert response.status_code == 400


def test_cancel_expiry_restart_and_concurrent_edits_are_not_silent_successes(client, backend, oauth):
    identifier = begin(client)
    assert client.post(BASE + "/start/example", json=CONFIG).status_code == 409
    assert client.delete(BASE + "/login/" + identifier).status_code == 204
    assert oauth.jobs["1"]["status"] == "cancelled"
    assert client.get(BASE + "/login/" + identifier).status_code == 410
    identifier = begin(client)
    mcp_oauth._sessions[identifier].expires = 0
    assert client.get(BASE + "/login/" + identifier).status_code == 410
    identifier = begin(client)
    oauth.jobs["3"] = {"status": "complete"}
    backend.gateway.resources["mcp.target"] = {
        "example": {"name": "example", "mcp": {"host": "https://changed.test/mcp"}}
    }
    assert client.get(BASE + "/login/" + identifier).status_code == 409
    assert not oauth.probes


def test_reconnect_keeps_exact_stored_home_and_literal_environment(client, backend, oauth):
    identifier = begin(client)
    oauth.jobs["1"] = {"status": "complete"}
    client.get(BASE + "/login/" + identifier)
    identifier = begin(client, {**CONFIG, "env": {"HOME": None, "SECRET": None}})
    request = oauth.calls[-1][2]
    assert request["env"]["HOME"] == "/data/mcp/oauth/example"
    assert request["env"]["SECRET"] == "$private-literal"
    oauth.jobs["2"] = {"status": "complete"}
    assert client.get(BASE + "/login/" + identifier).json()["status"] == "complete"


def test_remove_or_manual_save_cancels_pending_jobs_and_removes_auth_metadata(client, backend, oauth):
    identifier = begin(client)
    oauth.jobs["1"] = {"status": "complete"}
    client.get(BASE + "/login/" + identifier)
    begin(client)
    assert client.put("/api/mcp-servers/example", json={**CONFIG, "env": {}}).status_code == 200
    assert oauth.jobs["2"]["status"] == "cancelled"
    assert not backend.db.query("SELECT * FROM mcp_cli_oauth")
    begin(client)
    assert client.delete("/api/mcp-servers/example").status_code == 200
    assert oauth.jobs["3"]["status"] == "cancelled"


def test_save_lost_response_reconciles_expected_target_without_repeating_cli(
    client, backend, oauth, monkeypatch, live
):
    identifier = begin(client)
    oauth.jobs["1"] = {"status": "complete"}
    original = live.gateway.put_config_resources

    async def lost(kind, values):
        await original(kind, values)
        raise httpx.ConnectError("lost reply")

    monkeypatch.setattr(live.gateway, "put_config_resources", lost)
    assert client.get(BASE + "/login/" + identifier).status_code == 502
    monkeypatch.setattr(live.gateway, "put_config_resources", original)
    assert client.get(BASE + "/login/" + identifier).json()["status"] == "complete"
    assert sum(method == "POST" and path == "/sessions" for method, path, _ in oauth.calls) == 1


@pytest.mark.parametrize(
    "change",
    [
        {"transport": "http", "url": "https://mcp.test"},
        {"login_args": [1]},
        {"setup_args": [[]] * 5},
        {"provider": "unknown"},
        {"command": "npx --unsafe"},
    ],
)
def test_unsupported_or_invalid_input_does_not_run_commands(client, oauth, change):
    assert client.post(BASE + "/start/example", json={**CONFIG, **change}).status_code == 400
    assert not oauth.calls


def test_login_codes_urls_and_cli_control_are_not_agent_facing_tools(client):
    from nautionette_backend.main import backend_mcp

    assert not any("mcp_oauth" in tool.name for tool in backend_mcp.tools)
