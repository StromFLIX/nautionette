"""Title generation uses each provider's API without tools or an agent run."""

from __future__ import annotations

import json

import httpx
import pytest
from nautionette_backend.clients.agentgateway import GatewayClient


@pytest.mark.parametrize("model", ["copilot/gpt-4o", "openai/gpt-4o-mini"])
async def test_titles_use_the_selected_model_and_provider_api(http, model):
    use_responses = model.startswith("copilot/")
    endpoint = "responses" if use_responses else "chat/completions"

    def respond(request):
        body = json.loads(request.content)
        assert request.method == "POST"
        assert body["model"] == model
        assert body["stream"] is False
        assert "tools" not in body
        if use_responses:
            assert body["instructions"] == "Describe the task"
            assert body["input"] == "Please fix login"
            return httpx.Response(
                200,
                json={
                    "output": [
                        {"type": "reasoning", "summary": []},
                        {"type": "message", "content": [{"type": "output_text", "text": "Fix login"}]},
                    ]
                },
            )
        assert body["messages"] == [
            {"role": "system", "content": "Describe the task"},
            {"role": "user", "content": "Please fix login"},
        ]
        return httpx.Response(200, json={"choices": [{"message": {"content": "Fix login"}}]})

    http[f"http://gateway.test/v1/{endpoint}"] = respond
    assert (
        await GatewayClient("http://gateway.test").chat_title(model, "Describe the task", "Please fix login")
        == "Fix login"
    )


async def test_title_provider_errors_are_not_treated_as_a_title(http):
    http["http://gateway.test/v1/chat/completions"] = lambda request: httpx.Response(503)
    with pytest.raises(httpx.HTTPStatusError):
        await GatewayClient("http://gateway.test").chat_title("openai/gpt-4o-mini", "Title", "Ask")
