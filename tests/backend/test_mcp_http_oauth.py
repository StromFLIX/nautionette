"""Hosted MCP OAuth contracts: discovery, PKCE, sealed tokens and durable refresh."""

import asyncio
import base64
import hashlib
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from fastapi import HTTPException
from nautionette_backend import mcp_http_oauth as oauth
from nautionette_backend import mcp_oauth
from nautionette_backend.clients.agentgateway import MCPHTTPError
from nautionette_backend.config import settings
from nautionette_backend.pi_package_secrets import unseal

BASE = "/api/mcp-oauth"
URL = "https://mcp.example.test/mcp"
ISSUER = "https://login.example.test"


@pytest.fixture
def hosted(backend, live, monkeypatch):
    monkeypatch.setattr(oauth, "_sessions", {})
    monkeypatch.setattr(mcp_oauth, "_sessions", {})
    monkeypatch.setattr(mcp_oauth, "_lock", asyncio.Lock())
    clock = [1000.0]
    monkeypatch.setattr(oauth, "time", SimpleNamespace(time=lambda: clock[0], monotonic=lambda: clock[0]))
    metadata = {
        "issuer": ISSUER,
        "authorization_endpoint": ISSUER + "/authorize",
        "token_endpoint": ISSUER + "/token",
        "response_types_supported": ["code"],
        "response_modes_supported": ["query", "form_post"],
        "code_challenge_methods_supported": ["S256"],
        "token_endpoint_auth_methods_supported": ["none"],
        "scopes_supported": ["read", "offline_access"],
    }
    resource = {"resource": URL, "authorization_servers": [ISSUER], "scopes_supported": ["read"]}
    token = {
        "access_token": "access-token-1",
        "refresh_token": "refresh-token-1",
        "token_type": "Bearer",
        "expires_in": 300,
    }
    calls = []

    async def fetch(url, data=None):
        if data is not None:
            calls.append(dict(data))
            return 200, {}, dict(token)
        if "oauth-protected-resource" in url:
            return 200, {}, resource.copy()
        return 200, {}, metadata.copy()

    async def public_url(url):
        oauth.https_url(url)
        return ["93.184.216.34"]

    monkeypatch.setattr(oauth, "fetch", fetch)
    monkeypatch.setattr(oauth, "public_url", public_url)
    backend.gateway.tools[URL] = [{"name": "read", "inputSchema": {"type": "object"}}]
    original_tools = live.gateway.mcp_tools

    async def tools(url=None, extra=None, *, timeout=90):
        if url and "/_nautionette/mcp-probes/" in url:
            route = next(iter(backend.gateway.resources["traffic.route"].values()))
            target = route["backends"][0]["mcp"]["targets"][0]
            assert "stdio" not in target
            assert target["policies"]["backendAuth"]["key"]["value"].startswith("access-token-")
            return [{"name": "read", "inputSchema": {"type": "object"}}]
        if url is None and "example" in backend.gateway.resources.get("mcp.target", {}):
            return [{"name": "read", "inputSchema": {"type": "object"}}]
        return await original_tools(url, extra, timeout=timeout)

    monkeypatch.setattr(live.gateway, "mcp_tools", tools)
    return SimpleNamespace(clock=clock, metadata=metadata, resource=resource, token=token, calls=calls)


def begin(client, **change):
    descriptor = {"url": URL, "client_id": "registered-public-client", **change}
    discovered = client.post(BASE + "/discover", json=descriptor)
    assert discovered.status_code == 200, discovered.text
    config = {
        **discovered.json(),
        "transport": "http",
        "desktop_bridge_version": 1,
        "share_account": True,
        "redirect_uri": "http://localhost:12798",
        "redirect_uri_template": "http://localhost:12798",
        **change,
    }
    started = client.post(BASE + "/start/example", json=config)
    assert started.status_code == 200, started.text
    identifier = started.json()["id"]
    auth = client.get(BASE + "/login/" + identifier).json()["authorization"]
    return identifier, auth, config


def forward(client, identifier, auth, **fields):
    return client.post(
        BASE + "/callback/" + identifier,
        json={
            "capture_id": auth["id"],
            "method": "POST" if auth["response_mode"] == "form_post" else "GET",
            "fields": {"state": auth["state"], "code": "single-use-code", **fields},
        },
    )


def connect(client, **change):
    identifier, auth, config = begin(client, **change)
    assert forward(client, identifier, auth).json() == {"status": "forwarded"}
    response = client.get(BASE + "/login/" + identifier)
    assert response.json()["status"] == "complete", response.text
    return identifier, auth


@pytest.mark.parametrize("mode", ["query", "form_post"])
def test_hosted_pkce_exchange_and_mcp_install_never_expose_credentials(client, backend, hosted, mode):
    identifier, auth = connect(client, response_mode=mode)
    params = parse_qs(urlsplit(auth["authorization_url"]).query)
    request = hosted.calls[0]
    expected = (
        base64.urlsafe_b64encode(hashlib.sha256(request["code_verifier"].encode()).digest())
        .rstrip(b"=")
        .decode()
    )
    assert params["code_challenge"] == [expected]
    assert request["redirect_uri"] == "http://localhost:12798"
    assert request["resource"] == URL
    assert "client_secret" not in request
    target = backend.gateway.resources["mcp.target"]["example"]
    assert target["mcp"]["host"] == URL and "stdio" not in target
    assert target["policies"]["backendAuth"]["key"]["value"] == "access-token-1"
    row = backend.db.one("SELECT * FROM mcp_http_oauth")
    assert "access-token-1" not in row["secret"] and "refresh-token-1" not in row["secret"]
    assert unseal(row["secret"], namespace=oauth.NAMESPACE)["tokens"]["refresh_token"] == "refresh-token-1"
    key = Path(settings.data_dir) / "mcp-oauth.key"
    assert key.stat().st_mode & 0o777 == 0o600
    public = client.get("/api/mcp-servers").text
    assert (
        "access-token-1" not in public and "refresh-token-1" not in public and "code_verifier" not in public
    )
    assert forward(client, identifier, auth).json() == {"status": "forwarded"}
    assert len(hosted.calls) == 1
    assert oauth._sessions[identifier].verifier == ""


@pytest.mark.parametrize("not_ready", ["missing_tools", "handshake_failed"])
@pytest.mark.asyncio
async def test_token_and_config_ack_do_not_release_gate_until_federation_ready(
    client, backend, live, hosted, monkeypatch, not_ready
):
    from nautionette_backend import chat_outputs, mcp_auth_wait

    monkeypatch.setattr(mcp_auth_wait, "time", oauth.time)
    chat = backend.db.create_chat("Waiting for OAuth", "default")
    message, _ = backend.db.accept_chat_message(chat["id"], "Use read", "oauth-ready-turn")
    identifier, auth, _ = begin(client)
    assert forward(client, identifier, auth).status_code == 200
    # Token exchange alone must not publish the connection or release demand.
    assert not backend.db.query("SELECT * FROM mcp_http_oauth")
    original = live.gateway.mcp_tools
    ready = False

    async def federated(url=None, *args, **kwargs):
        if url is None and not ready:
            if not_ready == "handshake_failed":
                raise httpx.ConnectError("Synthetic data-plane not ready")
            return [{"name": "unrelated_tool"}]
        return await original(url, *args, **kwargs)

    monkeypatch.setattr(live.gateway, "mcp_tools", federated)
    assert client.get(BASE + "/login/" + identifier).json()["status"] == "gateway_pending"
    assert backend.db.one("SELECT status FROM mcp_http_oauth")["status"] == "gateway_pending"
    assert not oauth._sessions[identifier].complete
    mcp_auth_wait.remember_tools([{"name": "read", "server": "example"}])
    with chat_outputs.publication_grant(chat["id"], message["id"]) as grant:
        demand = {**grant, "tool": "read", "tool_call_id": "read-1"}
        assert mcp_auth_wait.gate(demand)["status"] == "waiting"
        await oauth.refresh_once()  # Readback of saved config must not bypass readiness.
        assert mcp_auth_wait.gate(demand)["status"] == "waiting"
        assert not oauth._sessions[identifier].complete
        ready = True
        assert client.get(BASE + "/login/" + identifier).json()["status"] == "complete"
        assert mcp_auth_wait.gate(demand)["status"] == "ready"
    assert len(hosted.calls) == 1  # No authorization-code replay.


def test_multiserver_readiness_requires_this_servers_namespaced_tools(
    client, backend, live, hosted, monkeypatch
):
    identifier, auth, _ = begin(client)
    assert forward(client, identifier, auth).status_code == 200
    backend.gateway.resources["mcp.target"] = {
        "other": {"name": "other", "mcp": {"host": "https://other.test/mcp"}}
    }
    original = live.gateway.mcp_tools
    catalog = [{"name": "read"}]

    async def federated(url=None, *args, **kwargs):
        if url is None:
            return list(catalog)
        return await original(url, *args, **kwargs)

    monkeypatch.setattr(live.gateway, "mcp_tools", federated)
    # A raw name or another server's prefixed name is not proof of readiness.
    for names in [["read"], ["other_read"], ["read", "other_read"]]:
        catalog[:] = [{"name": name} for name in names]
        assert client.get(BASE + "/login/" + identifier).json()["status"] == "gateway_pending"
    catalog.append({"name": "example_read"})
    assert client.get(BASE + "/login/" + identifier).json()["status"] == "complete"
    assert len(hosted.calls) == 1


def test_readiness_wait_has_a_fixed_deadline_and_actionable_failure(
    client, backend, live, hosted, monkeypatch
):
    identifier, auth, _ = begin(client)
    assert forward(client, identifier, auth).status_code == 200
    original = live.gateway.mcp_tools
    probes = []

    async def warming(url=None, *args, **kwargs):
        if url is None:
            return []
        probes.append(url)
        return await original(url, *args, **kwargs)

    monkeypatch.setattr(live.gateway, "mcp_tools", warming)
    assert client.get(BASE + "/login/" + identifier).json()["status"] == "gateway_pending"
    hosted.clock[0] += oauth.READINESS_SECONDS - 1
    assert client.get(BASE + "/login/" + identifier).json()["status"] == "gateway_pending"
    hosted.clock[0] += 2
    result = client.get(BASE + "/login/" + identifier).json()
    assert result["status"] == "failed"
    assert "Sign-in succeeded" in result["message"] and "60 seconds" in result["message"]
    assert len(probes) == 1  # Polling does not recreate isolated sessions.
    assert len(hosted.calls) == 1
    assert not oauth._sessions[identifier].complete


def test_discovery_rejects_resource_issuer_confidential_and_weak_pkce(client, hosted):
    for mapping, key, value in [
        (hosted.resource, "resource", "https://other.test/mcp"),
        (hosted.metadata, "issuer", "https://other.test"),
        (hosted.metadata, "code_challenge_methods_supported", ["plain"]),
        (hosted.metadata, "token_endpoint_auth_methods_supported", ["client_secret_basic"]),
    ]:
        original = mapping[key]
        mapping[key] = value
        assert client.post(BASE + "/discover", json={"url": URL}).status_code == 400
        mapping[key] = original


def test_workiq_entra_public_client_compatibility_not_confidential_metadata_default(client, hosted):
    hosted.resource.update(
        resource=oauth.WORKIQ_URL,
        authorization_servers=["https://login.microsoftonline.com/organizations/v2.0"],
        scopes_supported=["fdcc1f02-fc51-4226-8753-f668596af7f7/WorkIQAgent.Ask"],
    )
    hosted.metadata.update(
        issuer="https://login.microsoftonline.com/{tenantid}/v2.0",
        authorization_endpoint="https://login.microsoftonline.com/organizations/oauth2/v2.0/authorize",
        token_endpoint="https://login.microsoftonline.com/organizations/oauth2/v2.0/token",
        token_endpoint_auth_methods_supported=["client_secret_post"],
    )
    del hosted.metadata["code_challenge_methods_supported"]
    profile = client.post(BASE + "/discover", json={"url": oauth.WORKIQ_URL}).json()
    assert profile["client_id"] == oauth.WORKIQ_CLIENT
    assert profile["redirect_uri"] == oauth.WORKIQ_REDIRECT
    assert "offline_access" in profile["scope"] and "openid" in profile["scope"]
    assert profile["use_resource"] is False
    assert profile["client_registration_verified"] is False


def test_authentication_client_and_shared_account_checks_precede_exchange(client, anonymous, hosted):
    assert anonymous.post(BASE + "/discover", json={"url": URL}).status_code == 401
    _, _, config = begin(client)
    client.delete(BASE + "/login/" + next(iter(oauth._sessions)))
    for change in [
        {"desktop_bridge_version": 0},
        {"share_account": False},
        {"redirect_uri": "https://localhost:12798"},
        {"discovery_fingerprint": "0" * 64},
        {"client_secret": "do-not-accept"},
    ]:
        assert client.post(BASE + "/start/example", json={**config, **change}).status_code in {400, 409}
    assert hosted.calls == []


def test_state_mode_issuer_replay_and_provider_errors_do_not_consume_twice(client, hosted):
    identifier, auth, _ = begin(client)
    assert forward(client, identifier, auth, state="wrong").status_code == 400
    assert forward(client, identifier, auth, iss="https://wrong.test").status_code == 400
    assert hosted.calls == []
    hosted.token.update(error="invalid_grant", error_description="private-provider-response")
    response = forward(client, identifier, auth)
    assert response.status_code == 400 and "private-provider-response" not in response.text
    assert forward(client, identifier, auth).status_code == 410
    assert len(hosted.calls) == 1


def test_uncertain_exchange_is_not_replayed(client, hosted, monkeypatch):
    identifier, auth, _ = begin(client)

    async def lost(*_args, **_kwargs):
        raise HTTPException(502, "Unknown request outcome")

    monkeypatch.setattr(oauth, "exchange", lost)
    assert forward(client, identifier, auth).status_code == 502
    assert forward(client, identifier, auth).status_code == 410


@pytest.mark.asyncio
async def test_refresh_rotation_is_sealed_before_gateway_and_survives_lost_update(
    client, backend, live, hosted, monkeypatch
):
    connect(client)
    hosted.clock[0] += 260
    hosted.token.update(access_token="access-token-2", refresh_token="refresh-token-2")
    original = live.gateway.put_config_resources

    async def fail(kind, values):
        row = backend.db.one("SELECT * FROM mcp_http_oauth")
        assert row["status"] == "gateway_pending"
        assert (
            unseal(row["secret"], namespace=oauth.NAMESPACE)["tokens"]["refresh_token"] == "refresh-token-2"
        )
        raise httpx.ConnectError("Gateway unavailable")

    monkeypatch.setattr(live.gateway, "put_config_resources", fail)
    await oauth.refresh_once()
    assert hosted.calls[-1]["refresh_token"] == "refresh-token-1"
    assert backend.db.one("SELECT status FROM mcp_http_oauth")["status"] == "gateway_pending"
    hosted.clock[0] += 31
    monkeypatch.setattr(live.gateway, "put_config_resources", original)
    await oauth.refresh_once()
    assert len(hosted.calls) == 2
    assert (
        backend.gateway.resources["mcp.target"]["example"]["policies"]["backendAuth"]["key"]["value"]
        == "access-token-2"
    )


@pytest.mark.asyncio
async def test_rotated_token_remains_pending_until_federated_handshake_succeeds(
    client, backend, live, hosted, monkeypatch
):
    connect(client)
    hosted.clock[0] += 260
    hosted.token.update(access_token="access-token-2", refresh_token="refresh-token-2")
    original = live.gateway.mcp_tools
    ready = False

    async def warming(url=None, *args, **kwargs):
        if url is None and not ready:
            raise httpx.ConnectError("Federation is still connecting")
        return await original(url, *args, **kwargs)

    monkeypatch.setattr(live.gateway, "mcp_tools", warming)
    await oauth.refresh_once()
    row = backend.db.one("SELECT * FROM mcp_http_oauth")
    assert row["status"] == "gateway_pending"
    assert unseal(row["secret"], namespace=oauth.NAMESPACE)["tokens"]["refresh_token"] == "refresh-token-2"
    await oauth.refresh_once()
    assert backend.db.one("SELECT status FROM mcp_http_oauth")["status"] == "gateway_pending"
    assert len(hosted.calls) == 2
    ready = True
    await oauth.refresh_once()
    assert backend.db.one("SELECT status FROM mcp_http_oauth")["status"] == "connected"
    assert len(hosted.calls) == 2  # Rotating refresh token was consumed only once.


@pytest.mark.parametrize("operation", ["connect", "refresh"])
@pytest.mark.parametrize("installed", ["published", "previous", "external"])
@pytest.mark.asyncio
async def test_expired_pending_credentials_are_removed_even_when_gateway_readiness_never_passes(
    client, backend, live, hosted, monkeypatch, operation, installed
):
    if operation == "refresh":
        connect(client)
        hosted.clock[0] += 260
        hosted.token.update(access_token="access-token-2", refresh_token="refresh-token-2")
    original = live.gateway.mcp_tools

    async def unavailable(url=None, *args, **kwargs):
        if url is None:
            raise httpx.ConnectError("Another upstream prevents federation readiness")
        return await original(url, *args, **kwargs)

    monkeypatch.setattr(live.gateway, "mcp_tools", unavailable)
    if operation == "connect":
        identifier, auth, _ = begin(client)
        forward(client, identifier, auth)
        assert client.get(BASE + "/login/" + identifier).json()["status"] == "gateway_pending"
    else:
        await oauth.refresh_once()
    row = backend.db.one("SELECT * FROM mcp_http_oauth")
    assert row["status"] == "gateway_pending"
    external = {"name": "example", "mcp": {"host": "https://external-edit.test/mcp"}}
    if installed == "previous":
        previous = unseal(row["secret"], namespace=oauth.NAMESPACE)["previous"]
        backend.gateway.resources["mcp.target"].pop("example")
        if previous is not None:
            backend.gateway.resources["mcp.target"]["example"] = previous
    elif installed == "external":
        backend.gateway.resources["mcp.target"]["example"] = external
    calls = len(hosted.calls)
    hosted.clock[0] += 301
    await oauth.refresh_once()
    row = backend.db.one("SELECT * FROM mcp_http_oauth")
    assert row["status"] == "reauth_required" and not row["secret"]
    if installed == "external":
        assert backend.gateway.resources["mcp.target"]["example"] == external
    else:
        assert "example" not in backend.gateway.resources["mcp.target"]
    # Keep one-click setup, but never replay the authorization code/rotating refresh token.
    assert oauth.public_connections()["example"]["url"] == URL
    # The expired login's in-memory session must not block an immediate relogin
    # until its longer browser-session TTL elapses.
    begin(client)
    await oauth.refresh_once()
    assert len(hosted.calls) == calls


@pytest.mark.asyncio
async def test_expired_cleanup_recovers_a_lost_delete_without_replaying_tokens(
    client, backend, live, hosted, monkeypatch
):
    hosted.token.pop("refresh_token")
    connect(client)
    hosted.clock[0] += 301
    original = live.gateway.delete_config_resource
    deletes = []

    async def lost(kind, name):
        deletes.append(name)
        await original(kind, name)
        raise httpx.ReadTimeout("DELETE succeeded but its response was lost")

    monkeypatch.setattr(live.gateway, "delete_config_resource", lost)
    await oauth.refresh_once()
    assert backend.db.one("SELECT secret FROM mcp_http_oauth")["secret"]
    assert "example" not in backend.gateway.resources["mcp.target"]
    hosted.clock[0] += 31
    await oauth.refresh_once()
    row = backend.db.one("SELECT * FROM mcp_http_oauth")
    assert row["status"] == "reauth_required" and not row["secret"]
    assert deletes == ["example"] and len(hosted.calls) == 1


@pytest.mark.asyncio
async def test_failed_or_crashed_refresh_requires_login_and_removes_only_owned_target(
    client, backend, hosted
):
    connect(client)
    hosted.clock[0] += 260
    hosted.token.update(error="invalid_grant")
    await oauth.refresh_once()
    hosted.clock[0] += 31
    await oauth.refresh_once()
    assert len(hosted.calls) == 2  # No refresh-token replay.
    assert "example" not in backend.gateway.resources["mcp.target"]
    row = backend.db.one("SELECT * FROM mcp_http_oauth")
    assert row["status"] == "reauth_required" and not row["secret"]
    assert client.delete("/api/mcp-servers/example").status_code == 200
    assert not backend.db.query("SELECT * FROM mcp_http_oauth")


@pytest.mark.asyncio
async def test_existing_token_expires_without_refresh_and_external_edits_detach(client, backend, hosted):
    hosted.token.pop("refresh_token")
    connect(client)
    hosted.clock[0] += 301
    await oauth.refresh_once()
    assert backend.db.one("SELECT status FROM mcp_http_oauth")["status"] == "reauth_required"
    assert not backend.gateway.resources["mcp.target"]
    hosted.token["refresh_token"] = "new-refresh"
    connect(client)
    backend.gateway.resources["mcp.target"]["example"] = {
        "name": "example",
        "mcp": {"host": "https://external-edit.test"},
    }
    hosted.clock[0] += 260
    await oauth.refresh_once()
    assert not backend.db.query("SELECT * FROM mcp_http_oauth")
    assert backend.gateway.resources["mcp.target"]["example"]["mcp"]["host"] == "https://external-edit.test"


def test_transient_gateway_initialize_reuses_route_and_exchanged_token(
    client, backend, live, hosted, monkeypatch
):
    identifier, auth, _ = begin(client)
    assert forward(client, identifier, auth).status_code == 200
    original = live.gateway.mcp_tools
    attempts = []

    async def transient(url=None, *args, **kwargs):
        if url is None:
            return await original(url, *args, **kwargs)
        route = next(iter(backend.gateway.resources["traffic.route"].values()))
        target = route["backends"][0]["mcp"]["targets"][0]
        attempts.append((url, target["policies"]["backendAuth"]["key"]["value"]))
        if len(attempts) < 3:
            raise MCPHTTPError(
                httpx.Response(
                    400,
                    json={"error": {"code": -32603, "message": "initialization busy"}},
                    request=httpx.Request("POST", url),
                ),
                "initialize",
            )
        return await original(url, *args, **kwargs)

    monkeypatch.setattr(live.gateway, "mcp_tools", transient)
    assert client.get(BASE + "/login/" + identifier).json()["status"] == "complete"
    assert len(attempts) == 3 and len(set(attempts)) == 1
    assert len(hosted.calls) == 1  # Single-use code exchange is never replayed.
    assert not backend.gateway.resources["traffic.route"]


@pytest.mark.parametrize(
    "stage,message",
    [
        ("initialize", "unknown resource"),
        ("initialize", "unsupported MCP protocol version: private"),
        ("initialized", "unclassified"),
        ("tools/list", "unclassified"),
    ],
)
def test_permanent_or_post_initialize_errors_do_not_retry(client, live, hosted, monkeypatch, stage, message):
    identifier, auth, _ = begin(client)
    forward(client, identifier, auth)
    attempts = []

    async def refused(url, *_args, **_kwargs):
        attempts.append(url)
        raise MCPHTTPError(
            httpx.Response(400, json={"error": {"message": message}}, request=httpx.Request("POST", url)),
            stage,
        )

    monkeypatch.setattr(live.gateway, "mcp_tools", refused)
    assert client.get(BASE + "/login/" + identifier).status_code == 400
    assert len(attempts) == 1 and len(hosted.calls) == 1


def test_reconnect_failure_retains_working_profile_and_manual_save_stops_refresh(
    client, backend, live, hosted, monkeypatch
):
    connect(client)
    old = backend.db.one("SELECT * FROM mcp_http_oauth")
    hosted.token.update(access_token="access-token-2", refresh_token="refresh-token-2")
    identifier, auth, _ = begin(client)
    forward(client, identifier, auth)

    original = live.gateway.mcp_tools

    async def refuse(*_args, **_kwargs):
        raise ValueError("Synthetic MCP handshake failure")

    monkeypatch.setattr(live.gateway, "mcp_tools", refuse)
    assert client.get(BASE + "/login/" + identifier).status_code == 400
    assert backend.db.one("SELECT * FROM mcp_http_oauth")["revision"] == old["revision"]
    client.delete(BASE + "/login/" + identifier)
    monkeypatch.setattr(live.gateway, "mcp_tools", original)
    assert (
        client.put("/api/mcp-servers/example", json={"url": URL, "token": "manual-token"}).status_code == 200
    )
    assert not backend.db.query("SELECT * FROM mcp_http_oauth")


@pytest.mark.asyncio
async def test_refresh_during_reconnect_updates_only_managed_snapshot(client, backend, hosted):
    connect(client)
    identifier, auth, _ = begin(client)
    hosted.clock[0] += 260
    hosted.token.update(access_token="access-token-2", refresh_token="refresh-token-2")
    await oauth.refresh_once()
    hosted.token.update(access_token="access-token-3", refresh_token="refresh-token-3")
    forward(client, identifier, auth)
    assert client.get(BASE + "/login/" + identifier).json()["status"] == "complete"
    assert (
        backend.gateway.resources["mcp.target"]["example"]["policies"]["backendAuth"]["key"]["value"]
        == "access-token-3"
    )


@pytest.mark.asyncio
async def test_cancelled_failed_publish_restores_old_refresh_credentials(
    client, backend, live, hosted, monkeypatch
):
    connect(client)
    old = backend.db.one("SELECT * FROM mcp_http_oauth")
    identifier, auth, _ = begin(client)
    hosted.token.update(access_token="access-token-2", refresh_token="refresh-token-2")
    forward(client, identifier, auth)
    original = live.gateway.put_config_resources

    async def fail(kind, values):
        if kind == "mcp.target":
            raise httpx.ConnectError("Unavailable")
        return await original(kind, values)

    monkeypatch.setattr(live.gateway, "put_config_resources", fail)
    assert client.get(BASE + "/login/" + identifier).status_code == 502
    assert client.delete(BASE + "/login/" + identifier).status_code == 204
    hosted.clock[0] += 61
    await oauth.refresh_once()
    row = backend.db.one("SELECT * FROM mcp_http_oauth")
    assert row["revision"] == old["revision"]
    assert unseal(row["secret"], namespace=oauth.NAMESPACE)["tokens"]["refresh_token"] == "refresh-token-1"


def test_lost_gateway_success_is_reconciled_without_replaying_code(
    client, backend, live, hosted, monkeypatch
):
    identifier, auth, _ = begin(client)
    forward(client, identifier, auth)
    original = live.gateway.put_config_resources

    async def lost(kind, values):
        await original(kind, values)
        if kind == "mcp.target":
            raise httpx.ConnectError("Lost response")

    monkeypatch.setattr(live.gateway, "put_config_resources", lost)
    assert client.get(BASE + "/login/" + identifier).status_code == 502
    monkeypatch.setattr(live.gateway, "put_config_resources", original)
    assert client.get(BASE + "/login/" + identifier).json()["status"] == "complete"
    assert len(hosted.calls) == 1


@pytest.mark.asyncio
async def test_credential_key_loss_does_not_mint_a_new_key_over_saved_credentials(client, backend, hosted):
    connect(client)
    key = Path(settings.data_dir) / "mcp-oauth.key"
    backup = key.read_bytes()
    try:
        key.unlink()
        hosted.clock[0] += 260
        await oauth.refresh_once()
        assert client.get("/api/mcp-servers").json()["servers"][0]["oauth"]["status"] == "storage_unavailable"
        descriptor = client.post(
            BASE + "/discover", json={"url": URL, "client_id": "registered-public-client"}
        ).json()
        response = client.post(
            BASE + "/start/other",
            json={
                **descriptor,
                "transport": "http",
                "desktop_bridge_version": 1,
                "share_account": True,
                "redirect_uri": "http://localhost:12798",
            },
        )
        assert response.status_code == 503 and not key.exists()
        assert len(hosted.calls) == 1
    finally:
        key.write_bytes(backup)
        key.chmod(0o600)


@pytest.mark.asyncio
async def test_pinned_https_no_ambient_credentials_or_redirects_and_bounded_body(http, monkeypatch):
    monkeypatch.setattr(oauth, "public_addresses", lambda _host: ["93.184.216.34"])

    def response(request):
        assert request.headers["host"] == "login.example.test"
        assert request.extensions["sni_hostname"] == "login.example.test"
        assert "authorization" not in request.headers
        return httpx.Response(302, headers={"Location": "https://private.test/"}, json={})

    http["https://93.184.216.34/token"] = response
    assert (await oauth.fetch(ISSUER + "/token", {"code": "synthetic"}))[0] == 302
    http["https://93.184.216.34/token"] = lambda _request: httpx.Response(200, content=b"x" * 65537)
    with pytest.raises(HTTPException, match="64 KiB"):
        await oauth.fetch(ISSUER + "/token")
