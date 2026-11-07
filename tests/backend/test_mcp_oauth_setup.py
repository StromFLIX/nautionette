"""One-action setup, cached retries/relogin and generic public-client registration."""
# ruff: noqa: F811 -- pytest fixtures are imported into this test module.

import json

import pytest
from fastapi import HTTPException
from nautionette_backend import mcp_http_oauth as oauth
from nautionette_backend import mcp_oauth_setup as setup

from .test_mcp_http_oauth import BASE, URL, begin, connect, hosted  # noqa: F401


def options(**changes):
    return {"url": URL, "client_id": "public-client", "redirect_uri": "http://localhost:12798", **changes}


def test_connect_discovers_once_and_every_retry_reuses_last_settings(client, hosted, monkeypatch):
    calls = []
    original = oauth.discover

    async def discover(config):
        calls.append(config.copy())
        return await original(config)

    monkeypatch.setattr(oauth, "discover", discover)
    response = client.post(
        BASE + "/connect/example", json={"transport": "http", "authentication": "oauth", **options()}
    )
    assert response.status_code == 200
    config = response.json()["config"]
    assert config["client_id"] == "public-client"
    assert len(calls) == 1
    # Connecting/retrying/relogging must not require another discovery click.
    prepared = client.post(BASE + "/prepare/example", json={}).json()
    assert prepared["redirect_uri"] == "http://localhost:12798"
    assert len(calls) == 1
    started = client.post(BASE + "/start/example", json={**prepared, "desktop_bridge_version": 1})
    assert started.status_code == 200
    assert len(calls) == 1
    validated_at = setup.saved("example", URL)["updated_at"]
    client.delete(BASE + "/login/" + started.json()["id"])
    assert client.post(BASE + "/prepare/example", json={}).status_code == 200
    assert len(calls) == 1
    assert setup.saved("example", URL)["updated_at"] == validated_at


def test_blank_overrides_do_not_replace_saved_client_scope_or_redirect(client, hosted):
    saved = client.post(BASE + "/prepare/example", json=options(scope="read custom")).json()
    retry = client.post(
        BASE + "/prepare/example", json={"client_id": "", "scope": "", "redirect_uri": ""}
    ).json()
    assert retry["client_id"] == saved["client_id"]
    assert retry["scope"] == saved["scope"]
    assert retry["redirect_uri"] == saved["redirect_uri"]


def test_metadata_failure_never_erases_known_setup(client, hosted, monkeypatch):
    client.post(BASE + "/prepare/example", json=options())
    original = setup.time.time
    monkeypatch.setattr(setup.time, "time", lambda: original() + setup.MAX_AGE + 1)

    async def failed(_config):
        raise HTTPException(502, "Temporary metadata outage")

    monkeypatch.setattr(oauth, "discover", failed)
    response = client.post(BASE + "/prepare/example", json={})
    assert response.status_code == 200 and response.json()["client_id"] == "public-client"


def test_expired_existing_account_migrates_to_saved_setup_without_configuration_form(client, backend, hosted):
    connect(client)
    cached = setup.saved("example", URL)
    assert cached["profile"]["client_id"] == "registered-public-client"
    # Simulate the credential cleanup done after expiry/revocation.
    backend.db.execute("UPDATE mcp_http_oauth SET secret='',status='reauth_required' WHERE name='example'")
    assert client.post(BASE + "/prepare/example", json={}).status_code == 200
    status = client.get(BASE + "/status").json()["connections"][0]
    assert status["name"] == "example" and status["status"] == "reauth_required"
    assert status["revision"]
    assert "access-token" not in json.dumps(status)


def test_unknown_provider_without_registration_gets_one_time_advanced_requirement(client, hosted):
    response = client.post(BASE + "/prepare/example", json={"url": URL})
    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "oauth_client_required"
    assert client.post(BASE + "/prepare/example", json={"client_id": "public-client"}).status_code == 200


def test_generic_dynamic_registration_is_saved_and_not_repeated_on_retry(client, hosted, monkeypatch):
    hosted.metadata["registration_endpoint"] = "https://login.example.test/register"
    original = oauth.fetch
    registrations = []

    async def fetch(url, data=None, *, json_body=None):
        if json_body is not None:
            registrations.append(json_body)
            return (
                201,
                {},
                {
                    "client_id": "registered-dynamic-client",
                    "redirect_uris": json_body["redirect_uris"],
                    "token_endpoint_auth_method": "none",
                },
            )
        return await original(url, data)

    monkeypatch.setattr(oauth, "fetch", fetch)
    config = client.post(BASE + "/prepare/example", json={"url": URL}).json()
    started = client.post(
        BASE + "/start/example",
        json={
            **config,
            "desktop_bridge_version": 1,
            "redirect_uri": "http://127.0.0.1:40101/callback",
            "redirect_uri_template": "http://127.0.0.1:0/callback",
        },
    )
    assert started.status_code == 200
    identifier = started.json()["id"]
    auth = client.get(BASE + "/login/" + identifier).json()["authorization"]
    assert "registered-dynamic-client" in auth["authorization_url"]
    assert len(registrations) == 1
    client.delete(BASE + "/login/" + identifier)
    again = client.post(BASE + "/prepare/example", json={}).json()
    assert again["client_id"] == "registered-dynamic-client"
    assert again["redirect_uri"] == "http://127.0.0.1:40101/callback"
    assert len(registrations) == 1


def test_uncertain_registration_is_fenced_not_replayed(client, hosted, monkeypatch):
    hosted.metadata["registration_endpoint"] = "https://login.example.test/register"
    original = oauth.fetch
    registrations = []

    async def fetch(url, data=None, *, json_body=None):
        if json_body is not None:
            registrations.append(1)
            raise HTTPException(502, "Lost registration result")
        return await original(url, data)

    monkeypatch.setattr(oauth, "fetch", fetch)
    config = client.post(BASE + "/prepare/example", json={"url": URL}).json()
    response = client.post(
        BASE + "/start/example",
        json={
            **config,
            "desktop_bridge_version": 1,
            "redirect_uri": "http://127.0.0.1:40101/callback",
            "redirect_uri_template": "http://127.0.0.1:0/callback",
        },
    )
    assert response.status_code == 502
    assert client.post(BASE + "/prepare/example", json={}).status_code == 400
    # A metadata refresh must not reopen a registration with a lost result.
    assert client.post(BASE + "/prepare/example", json={"refresh_metadata": True}).status_code == 400
    assert registrations == [1]


@pytest.mark.asyncio
async def test_only_metadata_reads_retry_transient_failures(monkeypatch):
    attempts = []

    async def public(_url):
        return ["93.184.216.34"]

    async def request(_parsed, _address, data, json_body):
        attempts.append(1)
        return (503, {}, {}) if len(attempts) == 1 else (200, {}, {})

    monkeypatch.setattr(oauth, "public_url", public)
    monkeypatch.setattr(oauth, "request_at", request)
    assert (await oauth.fetch("https://login.example.test/metadata"))[0] == 200
    assert len(attempts) == 2
    attempts.clear()
    assert (await oauth.fetch("https://login.example.test/token", {"code": "test"}))[0] == 503
    assert len(attempts) == 1
    attempts.clear()
    assert (await oauth.fetch("https://login.example.test/register", json_body={"client_name": "test"}))[
        0
    ] == 503
    assert len(attempts) == 1
