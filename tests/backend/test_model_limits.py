"""The winning route's token budgets survive discovery, durable jobs and steering."""

import asyncio
import json

import pytest
from nautionette_backend import conversations, runtime
from nautionette_backend.integrations.discovery import token_limit


@pytest.mark.parametrize("value", [None, False, True, 0, -1, 1.5, "128000", 2**53, [], {}])
def test_invalid_token_limits_are_unknown(value):
    assert token_limit({"limit": value}, ["limit"]) is None


def test_valid_token_limit_fallback():
    assert token_limit({"bad": 0, "nested": {"tokens": 128000}}, ["bad", "nested.tokens"]) == 128000


@pytest.fixture
def limits_catalog(client, backend):
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
                "id": "gpt-6.1-sol",
                "supported_endpoints": ["/responses"],
                "capabilities": {
                    "type": "chat",
                    "limits": {
                        "max_context_window_tokens": 1050000,
                        "max_prompt_tokens": 922000,
                        "max_output_tokens": 128000,
                    },
                },
            }
        ]
    }
    return client.get("/api/catalog").json()["models"][0]


def test_copilot_limits_are_advertised_not_guessed(limits_catalog):
    assert limits_catalog["context_length"] == 1050000
    assert limits_catalog["max_input_tokens"] == 922000
    assert limits_catalog["max_output_tokens"] == 128000


def test_openrouter_output_limit(client, backend):
    backend.model_catalog.payloads["https://openrouter.ai/api/v1/models"] = {
        "data": [
            {
                "id": "vendor/model",
                "context_length": 256000,
                "top_provider": {"max_completion_tokens": 64000},
            }
        ]
    }
    client.put("/api/model-integrations/openrouter", json={"api_key": "test"})
    model = client.get("/api/catalog", params={"refresh": "true"}).json()["models"][0]
    assert model["context_length"] == 256000
    assert model["max_output_tokens"] == 64000


def test_losing_integration_cannot_certify_limits(client, backend, limits_catalog):
    backend.gateway.resources["llm.model"]["override"] = {
        "id": "override",
        "name": "copilot/gpt-6.1-sol",
        "provider": "other",
    }
    runtime.forget_catalog()
    model = client.get("/api/catalog").json()["models"][0]
    assert model["context_length"] is None
    assert model["max_input_tokens"] is None
    assert model["max_output_tokens"] is None


def test_advertised_limits_are_pinned_into_durable_job(client, backend, limits_catalog):
    chat = client.post("/api/chats", json={"model": "copilot/gpt-6.1-sol"}).json()
    assert (
        client.post(
            f"/api/chats/{chat['id']}/messages", json={"text": "Continue", "message_id": "limits-turn"}
        ).status_code
        == 200
    )
    limits = {"context_window": 1050000, "max_input_tokens": 922000, "max_output_tokens": 128000}
    assert backend.broker.jobs[0]["model_limits"] == limits
    saved = json.loads(backend.db.one("SELECT job FROM chat_turns WHERE id = 'limits-turn'")["job"])
    assert saved["model_limits"] == limits


async def test_changed_limits_cannot_be_steered_into_an_old_process(backend, monkeypatch):
    chat = backend.db.create_chat("Limits", "default")
    job = {"model": "model", "model_limits": {"context_window": 200000, "max_output_tokens": 8192}}
    backend.db.accept_chat_message(chat["id"], "First", "first")
    backend.db.accept_chat_message(chat["id"], "Second", "second", queue=True)
    backend.db.execute(
        "UPDATE chat_turns SET job = ? WHERE id = 'second'",
        (json.dumps({**job, "prompt": "Second", "model_limits": {"max_output_tokens": 128000}}),),
    )
    commands = []

    async def control(*args):
        commands.append(args)
        return True

    monkeypatch.setattr(conversations.broker, "control_agent", control)
    finished = asyncio.Event()
    task = asyncio.create_task(conversations.control_turn("first", chat["id"], job, finished))
    await asyncio.sleep(0.25)
    finished.set()
    await task
    assert commands == []
