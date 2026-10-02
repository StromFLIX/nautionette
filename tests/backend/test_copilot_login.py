from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from urllib.parse import parse_qs

import httpx
import pytest
from fastapi import HTTPException
from nautionette_backend.integrations import copilot_login

from .fakes import http_error

BASE = "/api/model-integrations/copilot/login"
TOKEN_URL = "https://github.com/login/oauth/access_token"
DEVICE = {
    "device_code": "private-device-code",
    "user_code": "ABCD-1234",
    "verification_uri": "https://github.com/login/device",
    "expires_in": 900,
    "interval": 5,
}


@pytest.fixture
def oauth(http, monkeypatch):
    monkeypatch.setattr(copilot_login, "_sessions", {})
    clock = [1000.0]
    monkeypatch.setattr(copilot_login, "time", SimpleNamespace(monotonic=lambda: clock[0]))
    calls = []
    result = {"error": "authorization_pending"}

    def device(request):
        assert parse_qs(request.content.decode()) == {
            "client_id": [copilot_login.CLIENT_ID],
            "scope": ["read:user"],
        }
        return httpx.Response(200, json=DEVICE)

    def token(request):
        calls.append(parse_qs(request.content.decode()))
        return httpx.Response(200, json=result)

    http["https://github.com/login/device/code"] = device
    http[TOKEN_URL] = token
    return clock, calls, result


def begin(client):
    response = client.post(BASE, json={"integration_id": "my-cli"})
    assert response.status_code == 200
    assert "private-device-code" not in response.text
    return f"{BASE}/{response.json()['id']}"


def test_start_and_poll_respect_github_interval(client, oauth):
    clock, calls, result = oauth
    path = begin(client)
    assert client.post(path).json() == {"status": "pending", "interval": 5}
    assert not calls
    clock[0] += 5
    assert client.post(path).json()["status"] == "pending"
    assert calls == [
        {
            "client_id": [copilot_login.CLIENT_ID],
            "device_code": ["private-device-code"],
            "grant_type": ["urn:ietf:params:oauth:grant-type:device_code"],
        }
    ]
    result.update(error="slow_down", interval=15)
    clock[0] += 5
    assert client.post(path).json() == {"status": "pending", "interval": 15}
    clock[0] += 14
    assert client.post(path).json()["interval"] == 1
    assert len(calls) == 2


@pytest.mark.parametrize("existing", [False, True])
def test_authorization_adds_or_reconnects_without_exposing_tokens(client, backend, oauth, existing):
    clock, calls, result = oauth
    backend.gateway.provider_payloads["copilot"] = {"data": [{"id": "gpt-4o"}]}
    if existing:
        client.put("/api/model-integrations/copilot", json={"api_key": "gho_old"})
    path = begin(client)
    clock[0] += 5
    result.clear()
    result.update(access_token="gho_new", token_type="bearer")
    response = client.post(path)
    assert response.json() == {"status": "complete"}
    resources = backend.gateway.resources
    model = resources["llm.model"]["nautionette-integration-copilot"]
    route = resources["traffic.route"]["nautionette-integration-copilot-discovery"]
    assert model["params"]["apiKey"] == "gho_new"
    assert model["requestHeaders"]["set"]["Copilot-Integration-Id"] == "my-cli"
    assert route["backends"][0]["policies"]["backendAuth"]["key"]["value"] == "gho_new"
    assert "gho_new" not in response.text + client.get("/api/model-integrations").text
    assert "gho_new" not in json.dumps(backend.db.get_setting("model_integration:copilot"))
    login = next(iter(copilot_login._sessions.values()))
    assert not login.token and not login.device_code
    assert client.post(path).json() == {"status": "complete"}
    assert len(calls) == 1


@pytest.mark.parametrize("error", ["access_denied", "expired_token", "incorrect_client_credentials"])
def test_refusal_preserves_existing_credentials(client, backend, oauth, error):
    clock, _, result = oauth
    client.put("/api/model-integrations/copilot", json={"api_key": "gho_existing"})
    path = begin(client)
    clock[0] += 5
    result.update(error=error, error_description="private-upstream-detail")
    response = client.post(path)
    assert response.status_code == 400
    assert "private-upstream-detail" not in response.text
    assert client.post(path).status_code == 410
    assert backend.gateway.resources["llm.model"]["nautionette-integration-copilot"]["params"] == {
        "apiKey": "gho_existing"
    }


def test_cancel_and_expiry_never_exchange_a_token(client, oauth):
    clock, calls, _ = oauth
    path = begin(client)
    assert client.delete(path).status_code == 204
    assert client.delete(path).status_code == 204
    assert client.post(path).status_code == 410
    path = begin(client)
    clock[0] += 901
    assert client.post(path).status_code == 410
    assert not calls


def test_login_requires_user_authentication(anonymous, oauth):
    assert anonymous.post(BASE).status_code == 401
    assert anonymous.post(f"{BASE}/id").status_code == 401
    assert anonymous.delete(f"{BASE}/id").status_code == 401


@pytest.mark.parametrize(
    "change",
    [
        {"verification_uri": "https://attacker.example/login"},
        {"expires_in": -1},
        {"interval": 0},
        {"device_code": ""},
        {"user_code": None},
    ],
)
def test_invalid_device_response_is_not_returned(client, oauth, http, change):
    http["https://github.com/login/device/code"] = lambda _: httpx.Response(200, json={**DEVICE, **change})
    response = client.post(BASE)
    assert response.status_code == 502
    assert "private-device-code" not in response.text
    assert not copilot_login._sessions


def test_read_only_gateway_and_invalid_config_do_not_start_login(client, backend, oauth, http):
    http.clear()
    assert client.post(BASE, json={"integration_id": "invalid id"}).status_code == 400
    backend.gateway.storage_mode = "file"
    assert client.post(BASE).status_code == 409


def test_transient_error_is_sanitized_and_can_be_retried(client, oauth, http):
    clock, _, result = oauth
    path = begin(client)
    clock[0] += 5
    handler = http[TOKEN_URL]
    http[TOKEN_URL] = lambda _: httpx.Response(500, text="private-device-code")
    response = client.post(path)
    assert response.status_code == 502
    assert "private-device-code" not in response.text
    http[TOKEN_URL] = handler
    clock[0] += 5
    assert client.post(path).json()["status"] == "pending"


def test_gateway_write_failure_can_retry_without_exchanging_the_code_twice(client, live, oauth, monkeypatch):
    clock, calls, result = oauth
    path = begin(client)
    clock[0] += 5
    result.clear()
    result.update(access_token="gho_new", token_type="bearer")
    original = live.gateway.put_config_resources

    async def fail(*_args):
        raise http_error(503, "gho_new")

    monkeypatch.setattr(live.gateway, "put_config_resources", fail)
    response = client.post(path)
    assert response.status_code == 502
    assert "gho_new" not in response.text
    monkeypatch.setattr(live.gateway, "put_config_resources", original)
    clock[0] += 5
    assert client.post(path).json() == {"status": "complete"}
    assert len(calls) == 1


@pytest.mark.parametrize("token", ["", "$GH_COPILOT_TOKEN", "token\nvalue", "token\x7fvalue", None])
def test_invalid_token_is_never_saved(client, backend, oauth, token):
    clock, _, result = oauth
    path = begin(client)
    clock[0] += 5
    result.clear()
    result.update(access_token=token, token_type="bearer")
    assert client.post(path).status_code == 502
    assert "nautionette-integration-copilot" not in backend.gateway.resources.get("llm.model", {})


def test_pending_sessions_are_bounded_and_expired_slots_are_released(client, oauth):
    clock, _, _ = oauth
    for _ in range(8):
        begin(client)
    assert client.post(BASE).status_code == 429
    clock[0] += 901
    begin(client)
    assert len(copilot_login._sessions) == 1


async def test_cancellation_during_github_poll_does_not_save_the_token(backend, oauth, monkeypatch):
    clock, _, _ = oauth
    started = await copilot_login.start({})
    clock[0] += 5
    waiting = asyncio.Event()
    release = asyncio.Event()

    async def token(*_args):
        waiting.set()
        await release.wait()
        return {"access_token": "gho_cancelled", "token_type": "bearer"}

    monkeypatch.setattr(copilot_login, "_post", token)
    polling = asyncio.create_task(copilot_login.poll(started["id"]))
    await waiting.wait()
    cancelling = asyncio.create_task(copilot_login.cancel(started["id"]))
    await asyncio.sleep(0)
    release.set()
    with pytest.raises(HTTPException) as error:
        await polling
    assert error.value.status_code == 410
    await cancelling
    assert "nautionette-integration-copilot" not in backend.gateway.resources.get("llm.model", {})


async def test_concurrent_polls_exchange_and_save_only_once(backend, oauth, monkeypatch):
    clock, _, _ = oauth
    started = await copilot_login.start({})
    clock[0] += 5
    calls = []

    async def token(*_args):
        calls.append(True)
        await asyncio.sleep(0)
        return {"access_token": "gho_once", "token_type": "bearer"}

    monkeypatch.setattr(copilot_login, "_post", token)
    results = await asyncio.gather(*(copilot_login.poll(started["id"]) for _ in range(2)))
    assert results == [{"status": "complete"}, {"status": "complete"}]
    assert calls == [True]
    assert len(backend.gateway.writes) == 2
