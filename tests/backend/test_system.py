"""The status page and the event feed behind it."""

from __future__ import annotations

import pytest
from nautionette_backend.events import bus


def _component(payload, name):
    return next(item for item in payload["components"] if item["name"] == name)


def test_every_component_reports_in(client):
    payload = client.get("/api/system").json()
    assert payload["version"] == "test"
    assert payload["auth_enabled"] is True
    assert {item["name"] for item in payload["components"]} == {
        "temporal",
        "broker",
        "agentgateway",
        "workflow-mcp",
    }
    assert all(item["status"] == "ok" for item in payload["components"])
    assert payload["agent_sets"] == [{"name": "default", "image": "pi-agent:test", "ready": True}]


def test_a_component_that_raises_is_reported_not_fatal(client, live, monkeypatch: pytest.MonkeyPatch):
    async def refuse():
        raise RuntimeError("connection refused")

    monkeypatch.setattr(live.broker, "health", refuse)
    payload = client.get("/api/system").json()
    assert _component(payload, "broker") == {
        "name": "broker",
        "status": "down",
        "detail": "connection refused",
    }
    # A broker that is down means no agent sets, not a 500.
    assert payload["agent_sets"] == []


def test_temporal_being_down_carries_its_last_error(client, live, temporal, monkeypatch):
    temporal.up = False
    monkeypatch.setattr(live.temporal, "last_error", "temporal:7233 unreachable")
    assert _component(client.get("/api/system").json(), "temporal") == {
        "name": "temporal",
        "status": "down",
        "detail": "temporal:7233 unreachable",
    }


def test_a_model_answering_once_is_remembered(client, internal_headers):
    assert client.get("/api/system").json()["model_key_present"] is False
    client.post("/internal/agent/call", json={"prompt": "hi"}, headers=internal_headers)
    assert client.get("/api/system").json()["model_key_present"] is True


def test_recent_events_are_replayed_to_a_new_client(client):
    bus.publish("test.event", {"detail": "noted"})
    events = client.get("/api/events/recent").json()["events"]
    assert events[-1]["kind"] == "test.event"
    assert events[-1]["detail"] == "noted"
