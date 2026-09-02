"""What a chat can be pointed at: models, tools and where each one is served from."""

from __future__ import annotations

import pytest
from nautionette_backend import catalog, runtime

OPENAI_ROUTE = {
    "id": "nautionette-integration-openai",
    "name": "openai/*",
    "provider": "openAI",
    "transformation": {"model": 'llmRequest.model.stripPrefix("openai/")'},
}


@pytest.fixture
def stocked(backend):
    """One configured integration serving two models, plus one MCP server."""
    backend.gateway.resources["llm.model"] = {OPENAI_ROUTE["id"]: OPENAI_ROUTE}
    backend.gateway.provider_payloads["openai"] = {
        "data": [
            {"id": "gpt-4o", "name": "GPT-4o"},
            {"id": "gpt-4o-mini", "name": "GPT-4o mini"},
        ]
    }
    backend.gateway.resources["mcp.target"] = {
        "linear": {"name": "linear", "mcp": {"host": "https://mcp.linear.test/mcp"}}
    }
    backend.gateway.tools = {"": [{"name": "linear_search", "description": "Find an issue"}]}
    backend.db.set_setting("model_integration:openai", {"api_key": ""})
    return backend


def test_models_are_labelled_with_the_integration_that_serves_them(client, stocked):
    models = client.get("/api/catalog").json()["models"]
    assert [model["id"] for model in models] == ["openai/gpt-4o", "openai/gpt-4o-mini"]
    assert {model["gateway"] for model in models} == {"OpenAI"}
    assert {model["integration"] for model in models} == {"openai"}
    assert models[0]["name"] == "GPT-4o"


def test_a_model_the_gateway_lists_but_nobody_claims_is_still_offered(client, stocked):
    stocked.gateway.served_models = [{"id": "local/llama"}]
    models = {model["id"]: model for model in client.get("/api/catalog").json()["models"]}
    assert models["local/llama"]["gateway"] == "gateway"
    assert models["local/llama"]["integration"] is None
    assert models["local/llama"]["provider"] == "local"


def test_an_always_latest_alias_is_marked_not_treated_as_a_vendor(client, stocked):
    stocked.gateway.served_models = [{"id": "~openai/gpt-4o"}]
    models = {model["id"]: model for model in client.get("/api/catalog").json()["models"]}
    assert models["~openai/gpt-4o"]["alias"] is True
    assert models["openai/gpt-4o"]["alias"] is False


def test_tools_are_attributed_to_the_server_that_federated_them(client, stocked):
    payload = client.get("/api/catalog").json()
    assert payload["tools"] == [
        {"name": "linear_search", "description": "Find an issue", "server": "linear"}
    ]
    assert payload["tool_servers"] == [{"name": "linear", "host": "https://mcp.linear.test/mcp", "count": 1}]


def test_a_tool_no_server_claims_is_asked_for_by_name(client, stocked):
    stocked.gateway.tools = {
        "": [{"name": "search", "description": ""}],
        "https://mcp.linear.test/mcp": [{"name": "search", "description": ""}],
    }
    payload = client.get("/api/catalog").json()
    assert payload["tools"][0]["server"] == "linear"


def test_a_tool_nobody_owns_lands_in_other(client, stocked):
    stocked.gateway.tools = {"": [{"name": "search", "description": ""}], "https://mcp.linear.test/mcp": []}
    payload = client.get("/api/catalog").json()
    assert payload["tools"][0]["server"] == "other"
    assert {server["name"] for server in payload["tool_servers"]} == {"linear", "other"}


def test_the_catalog_is_cached_and_refresh_bypasses_it(client, stocked):
    assert len(client.get("/api/catalog").json()["models"]) == 2
    stocked.gateway.provider_payloads["openai"]["data"].append({"id": "o3", "name": "o3"})
    assert len(client.get("/api/catalog").json()["models"]) == 2
    assert len(client.get("/api/catalog", params={"refresh": "true"}).json()["models"]) == 3


def test_a_gateway_that_cannot_be_reached_yields_an_empty_picker(client, stocked, live, monkeypatch):
    async def refuse(*_args, **_kwargs):
        raise RuntimeError("gateway is down")

    monkeypatch.setattr(live.gateway, "config", refuse)
    monkeypatch.setattr(live.gateway, "models", refuse)
    payload = client.get("/api/catalog").json()
    assert payload["models"] == []
    assert payload["agent_sets"] == [{"name": "default", "image": "pi-agent:test", "ready": True}]


def test_the_context_block_tells_a_client_how_to_do_the_same_arithmetic(client, stocked):
    context = client.get("/api/catalog").json()["context"]
    assert context == {
        "chars_per_token": runtime.CHARS_PER_TOKEN,
        "history_share": runtime.HISTORY_SHARE,
        "override": 0,
        "fallback": runtime.DEFAULT_HISTORY_CHARS,
    }


def test_building_the_catalog_records_each_model_window(client, backend):
    # Only a provider whose declaration says where the window lives can publish one.
    backend.model_catalog.payloads["https://openrouter.ai/api/v1/models"] = {
        "data": [{"id": "meta/llama", "name": "Llama", "context_length": 128_000}]
    }
    client.put("/api/model-integrations/openrouter", json={"api_key": "sk-or"})
    client.get("/api/catalog", params={"refresh": "true"})
    assert runtime.model_windows == {"meta/llama": 128_000}


# ------------------------------------------------------------------ route choice


def _config(*patterns):
    return {"model_routes": [{"name": name, "provider": "p", "id": name} for name in patterns]}


def test_an_exact_route_beats_a_wildcard():
    winner = catalog.route_for_model("openai/gpt-4o", _config("openai/*", "openai/gpt-4o"))
    assert winner["name"] == "openai/gpt-4o"


def test_the_longest_matching_wildcard_wins():
    winner = catalog.route_for_model("openai/gpt-4o", _config("*", "openai/*"))
    assert winner["name"] == "openai/*"


def test_a_model_no_route_matches_has_none():
    assert catalog.route_for_model("anthropic/claude", _config("openai/*")) is None


def test_a_route_without_a_provider_is_not_a_route():
    config = {"model_routes": [{"name": "openai/*", "provider": ""}]}
    assert catalog.route_for_model("openai/gpt-4o", config) is None
