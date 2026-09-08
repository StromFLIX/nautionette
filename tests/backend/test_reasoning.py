"""Capability discovery, persisted chat settings and durable execution agree."""

import asyncio
import json

import pytest
from nautionette_backend import conversations, runtime
from nautionette_backend.model_capabilities import model_capabilities
from nautionette_backend.reasoning import reasoning_metadata


@pytest.mark.parametrize(
    "item,kind,expected",
    [
        (
            {"capabilities": {"supports": {"reasoning_effort": ["max", "high", "low", "xhigh"]}}},
            "copilot",
            ["low", "high", "xhigh", "max"],
        ),
        (
            {"reasoning": {"supported_efforts": ["high", "medium", "low", "minimal"]}},
            "openrouter",
            ["minimal", "low", "medium", "high"],
        ),
        (
            {"reasoning": {"supported_efforts": ["none", "high", "bogus", "high", {}]}},
            "custom",
            ["none", "high"],
        ),
        ({"capabilities": {"supports": {"reasoning_effort": True}}}, "copilot", []),
        ({"reasoning": {"mandatory": True}}, "openrouter", []),
        ({"supported_parameters": ["reasoning", "reasoning_effort"]}, "openrouter", []),
        ({"capabilities": None, "reasoning": False}, "custom", []),
        ({}, "custom", []),
    ],
)
def test_only_advertised_levels_are_exposed(item, kind, expected):
    metadata = reasoning_metadata(item, kind)
    assert metadata["reasoning_efforts"] == expected
    assert metadata["reasoning_format"] == ("openrouter" if kind == "openrouter" else "openai")


def test_incompatible_route_disables_effort_controls():
    metadata = reasoning_metadata({"capabilities": {"supports": {"reasoning_effort": ["high"]}}}, "copilot")
    metadata["supported_endpoints"] = ["/v1/messages"]
    result = model_capabilities("copilot/claude", metadata)
    assert result["supports_reasoning"] is False
    assert result["reasoning_efforts"] == []


@pytest.fixture
def reasoning_catalog(client, backend):
    backend.gateway.resources["llm.model"] = {
        "nautionette-integration-copilot": {
            "id": "nautionette-integration-copilot",
            "name": "copilot/*",
            "provider": "copilot",
        }
    }
    backend.db.set_setting("model_integration:copilot", {})
    backend.gateway.provider_payloads["copilot"] = {
        "data": [
            {
                "id": name,
                "supported_endpoints": [endpoint],
                "capabilities": {
                    "type": "chat",
                    "supports": {"reasoning_effort": levels},
                },
            }
            for name, endpoint, levels in [
                ("gpt-6-astra", "/responses", ["low", "medium", "high", "xhigh", "max"]),
                ("gemini-3.8-flash", "/chat/completions", ["low", "medium", "high"]),
                ("plain", "/chat/completions", []),
            ]
        ]
    }
    return {m["id"]: m for m in client.get("/api/catalog").json()["models"]}


def test_discovered_catalog_carries_exact_efforts(reasoning_catalog):
    assert reasoning_catalog["copilot/gpt-6-astra"]["reasoning_efforts"] == [
        "low",
        "medium",
        "high",
        "xhigh",
        "max",
    ]
    assert reasoning_catalog["copilot/plain"]["supports_reasoning"] is False


def test_openrouter_discovery_carries_efforts(client, backend):
    backend.model_catalog.payloads["https://openrouter.ai/api/v1/models"] = {
        "data": [
            {
                "id": "openai/gpt-6-astra",
                "reasoning": {"supported_efforts": ["low", "high", "max"], "mandatory": True},
            }
        ]
    }
    client.put("/api/model-integrations/openrouter", json={"api_key": "sk-or"})
    model = client.get("/api/catalog", params={"refresh": "true"}).json()["models"][0]
    assert model["reasoning_format"] == "openrouter"
    assert model["reasoning_efforts"] == ["low", "high", "max"]


def test_effort_persists_and_is_pinned_in_job(client, backend, reasoning_catalog):
    chat = client.post("/api/chats", json={"model": "copilot/gpt-6-astra", "reasoning_effort": "max"}).json()
    url = f"/api/chats/{chat['id']}"
    assert chat["reasoning_effort"] == "max"
    assert client.get(url).json()["chat"]["reasoning_effort"] == "max"
    assert client.get("/api/chats").json()["chats"][0]["reasoning_effort"] == "max"
    # Catalog refresh after a restart must retain discovery and revalidate the choice.
    runtime.forget_catalog()
    assert (
        client.post(url + "/messages", json={"text": "Hi", "message_id": "reasoning-turn"}).status_code == 200
    )
    job = backend.broker.jobs[0]
    assert job["model"] == "copilot/gpt-6-astra"
    assert job["reasoning_effort"] == "max"
    assert job["model_reasoning"] == {
        "supported": True,
        "efforts": ["low", "medium", "high", "xhigh", "max"],
        "format": "openai",
    }
    saved = json.loads(backend.db.one("SELECT job FROM chat_turns WHERE id = 'reasoning-turn'")["job"])
    assert saved["reasoning_effort"] == "max"
    assert client.patch(url, json={"reasoning_effort": None}).json()["reasoning_effort"] is None
    assert client.post(url + "/messages", json={"text": "Default"}).status_code == 200
    assert backend.broker.jobs[-1]["reasoning_effort"] is None


@pytest.mark.parametrize("value", ["none", "minimal", "typo", "", False, 3, [], {}])
def test_invalid_effort_rejected_before_creating_or_updating(client, reasoning_catalog, value):
    payload = {"model": "copilot/gpt-6-astra", "reasoning_effort": value}
    assert client.post("/api/chats", json=payload).status_code == 422
    chat = client.post("/api/chats", json={"model": "copilot/gpt-6-astra"}).json()
    url = f"/api/chats/{chat['id']}"
    assert client.patch(url, json={"reasoning_effort": value}).status_code == 422
    assert client.get(url).json()["chat"]["reasoning_effort"] is None


def test_switching_model_resets_effort_or_validates_explicit_selection(client, reasoning_catalog):
    chat = client.post("/api/chats", json={"model": "copilot/gpt-6-astra", "reasoning_effort": "max"}).json()
    url = f"/api/chats/{chat['id']}"
    assert (
        client.patch(url, json={"model": "copilot/gemini-3.8-flash", "reasoning_effort": "max"}).status_code
        == 422
    )
    assert client.get(url).json()["chat"]["model"] == "copilot/gpt-6-astra"
    changed = client.patch(url, json={"model": "copilot/plain"}).json()
    assert changed["reasoning_effort"] is None
    assert client.patch(url, json={"reasoning_effort": "high"}).status_code == 422


def test_stale_capability_is_rejected_before_message_acceptance(client, backend, reasoning_catalog):
    chat = client.post("/api/chats", json={"model": "copilot/gpt-6-astra", "reasoning_effort": "max"}).json()
    backend.gateway.provider_payloads["copilot"]["data"][0]["capabilities"]["supports"][
        "reasoning_effort"
    ] = ["high"]
    runtime.forget_catalog()
    response = client.post(f"/api/chats/{chat['id']}/messages", json={"text": "Hi"})
    assert response.status_code == 422
    assert backend.db.list_messages(chat["id"]) == []
    assert backend.broker.jobs == []


async def test_different_effort_cannot_be_steered_into_current_run(backend, monkeypatch):
    chat = backend.db.create_chat("Test", "default")
    job = {"model": "copilot/gpt-6-astra", "reasoning_effort": "low"}
    backend.db.accept_chat_message(chat["id"], "First", "first")
    backend.db.accept_chat_message(chat["id"], "Second", "second", queue=True)
    backend.db.execute(
        "UPDATE chat_turns SET job = ? WHERE id = 'second'",
        (
            json.dumps(
                {
                    **job,
                    "reasoning_effort": "max",
                    "prompt": "Second",
                }
            ),
        ),
    )
    commands = []

    async def control(*args):
        commands.append(args)
        return True

    monkeypatch.setattr(conversations.broker, "control_agent", control)
    done = asyncio.Event()
    task = asyncio.create_task(conversations.control_turn("first", chat["id"], job, done))
    await asyncio.sleep(0.25)
    done.set()
    await task
    assert commands == []
    assert backend.db.one("SELECT state FROM chat_turns WHERE id = 'second'")["state"] == "queued"
