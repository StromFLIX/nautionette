"""Model integrations: the provider routes the app writes into agentgateway."""

from __future__ import annotations

import pytest
from nautionette_backend.integrations.registry import INTEGRATION_TYPES

from .fakes import http_error

MODEL_KIND = "llm.model"
ROUTE_KIND = "traffic.route"


def _resource(gateway, kind, resource_id):
    return gateway.resources.get(kind, {}).get(resource_id)


@pytest.fixture
def openai_catalog(backend):
    backend.gateway.provider_payloads["openai"] = {"data": [{"id": "gpt-4o", "name": "GPT-4o"}]}
    return backend


# --------------------------------------------------------------------- listing


def test_nothing_configured_means_everything_is_on_offer(client):
    payload = client.get("/api/model-integrations").json()
    assert payload["integrations"] == []
    assert payload["storage_mode"] == "hybrid"
    assert payload["writable"] is True
    assert {item["type"] for item in payload["available"]} == set(INTEGRATION_TYPES)


def test_a_single_instance_type_disappears_once_it_is_configured(client, openai_catalog):
    client.put("/api/model-integrations/openai", json={"api_key": "sk-test"})
    payload = client.get("/api/model-integrations").json()
    assert [item["instance"] for item in payload["integrations"]] == ["openai"]
    assert "openai" not in {item["type"] for item in payload["available"]}
    # Custom may be added over and over, so it never leaves the list.
    assert "custom" in {item["type"] for item in payload["available"]}


def test_a_key_authenticated_provider_always_offers_an_api_key_field(client):
    payload = client.get("/api/model-integrations").json()
    openai = next(item for item in payload["available"] if item["type"] == "openai")
    api_key = next(field for field in openai["fields"] if field["key"] == "api_key")
    assert api_key["kind"] == "secret"
    assert api_key["optional"] is True


# ---------------------------------------------------------------------- writing


def test_adding_a_provider_writes_a_model_route_and_a_discovery_route(client, openai_catalog):
    gateway = openai_catalog.gateway
    response = client.put("/api/model-integrations/openai", json={"api_key": "sk-test"})
    assert response.status_code == 200

    model = _resource(gateway, MODEL_KIND, "nautionette-integration-openai")
    assert model == {
        "id": "nautionette-integration-openai",
        "name": "openai/*",
        "provider": "openAI",
        "params": {"apiKey": "sk-test"},
        "transformation": {"model": 'llmRequest.model.stripPrefix("openai/")'},
    }
    route = _resource(gateway, ROUTE_KIND, "nautionette-integration-openai-discovery")
    assert route["matches"] == [
        {"path": {"exact": "/_nautionette/integrations/openai/models"}, "method": "GET"}
    ]
    assert route["policies"]["urlRewrite"] == {"path": {"full": "/v1/models"}}
    assert route["backends"] == [
        {"host": "api.openai.com:443", "policies": {"backendAuth": {"key": {"value": "sk-test"}}}}
    ]


def test_a_provider_with_a_header_credential_says_where_the_key_goes(client, backend):
    backend.gateway.provider_payloads["anthropic"] = {"data": []}
    client.put("/api/model-integrations/anthropic", json={"api_key": "sk-ant"})
    route = _resource(backend.gateway, ROUTE_KIND, "nautionette-integration-anthropic-discovery")
    assert route["backends"][0]["policies"]["backendAuth"]["key"]["location"] == {
        "header": {"name": "x-api-key"}
    }
    assert route["policies"]["requestHeaderModifier"]["set"]["anthropic-version"] == "2023-06-01"


def test_no_key_falls_back_to_the_variable_on_agentgateway(client, openai_catalog):
    client.put("/api/model-integrations/openai", json={})
    model = _resource(openai_catalog.gateway, MODEL_KIND, "nautionette-integration-openai")
    assert model["params"] == {"apiKey": "$OPENAI_API_KEY"}


def test_a_provider_with_its_own_credential_sends_nothing_and_lets_the_gateway_decide(client, backend):
    backend.gateway.provider_payloads["copilot"] = {"data": []}
    client.put("/api/model-integrations/copilot", json={})
    model = _resource(backend.gateway, MODEL_KIND, "nautionette-integration-copilot")
    assert "params" not in model
    route = _resource(backend.gateway, ROUTE_KIND, "nautionette-integration-copilot-discovery")
    assert route["backends"][0]["policies"] == {"backendAuth": "copilot"}


def test_reconfiguring_without_retyping_the_key_keeps_the_stored_one(client, openai_catalog):
    client.put("/api/model-integrations/openai", json={"api_key": "sk-first"})
    client.put("/api/model-integrations/openai", json={})
    model = _resource(openai_catalog.gateway, MODEL_KIND, "nautionette-integration-openai")
    assert model["params"] == {"apiKey": "sk-first"}


def test_the_key_never_comes_back_out_of_the_app(client, openai_catalog):
    client.put("/api/model-integrations/openai", json={"api_key": "sk-secret"})
    body = client.get("/api/model-integrations").text
    assert "sk-secret" not in body
    configured = client.get("/api/model-integrations").json()["integrations"][0]
    assert configured["credential"] == {"mode": "stored", "variable": ""}
    # Only the visible fields are kept here; the key stays with agentgateway.
    assert configured["config"] == {}


def test_the_declared_fields_are_kept_so_the_form_can_be_reopened(client, backend):
    backend.gateway.provider_payloads["copilot"] = {"data": []}
    client.put("/api/model-integrations/copilot", json={"integration_id": "my-app"})
    configured = client.get("/api/model-integrations").json()["integrations"][0]
    assert configured["config"]["integration_id"] == "my-app"


# ----------------------------------------------------------- several of a kind


def test_a_custom_provider_is_named_after_its_slug(client, backend):
    backend.gateway.provider_payloads["custom-mylab"] = {"data": [{"id": "m", "name": "M"}]}
    payload = client.put(
        "/api/model-integrations/custom",
        json={"slug": "mylab", "base_url": "https://api.example.com/v1", "api_key": "sk"},
    ).json()
    assert [item["instance"] for item in payload["integrations"]] == ["custom-mylab"]
    assert payload["integrations"][0]["name"] == "Custom: mylab"

    model = _resource(backend.gateway, MODEL_KIND, "nautionette-integration-custom-mylab")
    assert model["name"] == "mylab/*"
    assert model["params"]["baseUrl"] == "https://api.example.com/v1"
    route = _resource(backend.gateway, ROUTE_KIND, "nautionette-integration-custom-mylab-discovery")
    assert route["backends"][0]["host"] == "api.example.com:443"
    assert route["policies"]["urlRewrite"] == {"path": {"full": "/v1/models"}}


def test_two_custom_providers_live_side_by_side(client, backend):
    for slug in ("one", "two"):
        backend.gateway.provider_payloads[f"custom-{slug}"] = {"data": []}
        client.put(
            "/api/model-integrations/custom",
            json={"slug": slug, "base_url": f"https://{slug}.example.com/v1"},
        )
    payload = client.get("/api/model-integrations").json()
    assert sorted(item["instance"] for item in payload["integrations"]) == ["custom-one", "custom-two"]


# ------------------------------------------------------------------ validation


def test_a_missing_required_field_is_refused(client):
    response = client.put("/api/model-integrations/custom", json={"base_url": "https://a.example.com"})
    assert response.status_code == 400
    assert response.json()["detail"] == "invalid value for Name"


def test_an_unknown_integration_is_a_404(client):
    assert client.put("/api/model-integrations/nope", json={}).status_code == 404
    assert client.delete("/api/model-integrations/nope").status_code == 404
    assert client.post("/api/model-integrations/nope/test").status_code == 404


@pytest.mark.parametrize(
    "base_url",
    [
        "https://127.0.0.1/v1",
        "https://10.0.0.5/v1",
        "https://agentgateway/v1",
        "https://api.internal/v1",
        "http://api.example.com/v1",
    ],
)
def test_an_endpoint_the_gateway_should_not_be_pointed_at_is_refused(client, base_url):
    response = client.put(
        "/api/model-integrations/custom", json={"slug": "lab", "base_url": base_url}
    )
    assert response.status_code == 400


def test_a_read_only_gateway_cannot_be_written_to(client, backend):
    backend.gateway.storage_mode = "static"
    response = client.put("/api/model-integrations/openai", json={})
    assert response.status_code == 409
    assert "hybrid" in response.json()["detail"]


def test_a_gateway_that_cannot_read_the_variable_says_so(client, backend):
    backend.gateway.fail_kinds[MODEL_KIND] = http_error(
        400, "failed to look key 'OPENAI_API_KEY' up: environment variable not found"
    )
    response = client.put("/api/model-integrations/openai", json={})
    assert response.status_code == 400
    assert "OPENAI_API_KEY" in response.json()["detail"]


def test_a_half_written_integration_is_rolled_back(client, backend):
    backend.gateway.fail_kinds[ROUTE_KIND] = http_error(500, "nope")
    assert client.put("/api/model-integrations/openai", json={"api_key": "sk"}).status_code == 502
    # The model route must not survive without the discovery route beside it.
    assert (MODEL_KIND, "nautionette-integration-openai") in backend.gateway.deletes


# ------------------------------------------------------------------- removing


def test_removing_an_integration_takes_both_of_its_resources(client, openai_catalog):
    gateway = openai_catalog.gateway
    client.put("/api/model-integrations/openai", json={"api_key": "sk"})
    payload = client.delete("/api/model-integrations/openai").json()
    assert payload["integrations"] == []
    assert _resource(gateway, MODEL_KIND, "nautionette-integration-openai") is None
    assert _resource(gateway, ROUTE_KIND, "nautionette-integration-openai-discovery") is None
    assert openai_catalog.db.get_setting("model_integration:openai") is None


def test_removing_the_integration_behind_the_default_model_picks_another(client, backend):
    backend.gateway.provider_payloads["openai"] = {"data": [{"id": "gpt-4o", "name": "GPT-4o"}]}
    backend.gateway.provider_payloads["groq"] = {"data": [{"id": "llama", "name": "Llama"}]}
    client.put("/api/model-integrations/openai", json={"api_key": "sk"})
    client.put("/api/model-integrations/groq", json={"api_key": "gsk"})
    client.put("/api/settings", json={"default_model": "openai/gpt-4o"})

    payload = client.delete("/api/model-integrations/openai").json()
    assert payload["default_reset"] is True
    assert payload["default_model"] == "groq/llama"


def test_removing_the_last_integration_leaves_no_default_behind(client, openai_catalog):
    client.put("/api/model-integrations/openai", json={"api_key": "sk"})
    client.put("/api/settings", json={"default_model": "openai/gpt-4o"})
    payload = client.delete("/api/model-integrations/openai").json()
    assert payload["default_model"] == "openai/gpt-4o-mini"  # back to the environment


# --------------------------------------------------------------------- testing


def test_testing_an_integration_reports_the_model_it_reached(client, openai_catalog):
    client.put("/api/model-integrations/openai", json={"api_key": "sk"})
    result = client.post("/api/model-integrations/openai/test").json()
    assert result["ok"] is True
    assert result["model"] == "openai/gpt-4o"


def test_testing_an_integration_that_was_never_added_is_a_conflict(client, openai_catalog):
    response = client.post("/api/model-integrations/openai/test")
    assert response.status_code == 409
    assert response.json()["detail"] == "add OpenAI first"


def test_a_provider_that_serves_nothing_is_reported_not_celebrated(client, backend):
    backend.gateway.provider_payloads["openai"] = {"data": []}
    client.put("/api/model-integrations/openai", json={"api_key": "sk"})
    result = client.post("/api/model-integrations/openai/test").json()
    assert result == {"ok": False, "status": None, "message": "No chat models were discovered."}


def test_a_refused_key_becomes_one_actionable_sentence(client, backend, live, monkeypatch):
    backend.gateway.provider_payloads["openai"] = {"data": []}
    client.put("/api/model-integrations/openai", json={"api_key": "sk"})

    async def refuse(instance):
        raise http_error(401, "invalid api key")

    monkeypatch.setattr(live.gateway, "integration_models", refuse)
    result = client.post("/api/model-integrations/openai/test").json()
    assert result["ok"] is False
    assert result["status"] == 401
    assert result["message"] == "OpenAI rejected the stored API key."


# ------------------------------------------------------------------- discovery


def test_discovery_maps_a_provider_payload_through_its_declaration(client, backend):
    backend.gateway.provider_payloads["copilot"] = {
        "data": [
            {
                "id": "gpt-4o",
                "name": "GPT-4o",
                "vendor": "OpenAI",
                "model_picker_enabled": True,
                "capabilities": {"type": "chat", "limits": {"max_context_window_tokens": 128_000}},
            },
            {"id": "text-embed", "capabilities": {"type": "embeddings"}},
            {"id": "hidden", "model_picker_enabled": False, "capabilities": {"type": "chat"}},
        ]
    }
    client.put("/api/model-integrations/copilot", json={})
    integration = client.get("/api/model-integrations").json()["integrations"][0]
    assert integration["model_count"] == 1
    assert integration["discovery"] == {"ok": True, "status": 200, "message": "Discovered 1 models."}

    models = {model["id"]: model for model in client.get("/api/catalog").json()["models"]}
    assert models["copilot/gpt-4o"]["name"] == "OpenAI: GPT-4o"
    assert models["copilot/gpt-4o"]["context_length"] == 128_000


def test_a_provider_with_a_public_catalog_is_read_directly(client, backend):
    backend.model_catalog.payloads["https://openrouter.ai/api/v1/models"] = {
        "data": [{"id": "meta/llama", "name": "Llama", "context_length": 8_000}]
    }
    client.put("/api/model-integrations/openrouter", json={"api_key": "sk-or"})
    models = {model["id"]: model for model in client.get("/api/catalog").json()["models"]}
    # OpenRouter has no prefix of its own; the owner in the id is the vendor.
    assert models["meta/llama"]["provider"] == "meta"
    assert models["meta/llama"]["gateway"] == "OpenRouter"


# ------------------------------------------------------------------ bootstrap


async def test_the_default_integration_is_created_on_first_use(client, backend):
    backend.db.execute("DELETE FROM settings WHERE key = ?", ("model_integrations_initialized_v2",))
    backend.model_catalog.payloads["https://openrouter.ai/api/v1/models"] = {"data": []}
    client.get("/api/model-integrations")
    assert "nautionette-integration-openrouter" in backend.gateway.resources[MODEL_KIND]
    assert backend.db.get_setting("model_integrations_initialized_v2") is True


async def test_a_read_only_gateway_is_left_alone_at_startup(client, backend):
    backend.db.execute("DELETE FROM settings WHERE key = ?", ("model_integrations_initialized_v2",))
    backend.gateway.storage_mode = "static"
    client.get("/api/model-integrations")
    assert backend.gateway.resources.get(MODEL_KIND, {}) == {}
    assert backend.db.get_setting("model_integrations_initialized_v2") is None


async def test_per_model_copilot_resources_are_folded_into_the_integration(client, backend):
    backend.db.execute("DELETE FROM settings WHERE key = ?", ("model_integrations_initialized_v2",))
    backend.model_catalog.payloads["https://openrouter.ai/api/v1/models"] = {"data": []}
    backend.gateway.provider_payloads["copilot"] = {"data": []}
    backend.gateway.resources[MODEL_KIND] = {
        "nautionette-copilot-gpt-4o": {"id": "nautionette-copilot-gpt-4o", "name": "gpt-4o"}
    }
    client.get("/api/model-integrations")
    assert "nautionette-integration-copilot" in backend.gateway.resources[MODEL_KIND]
    assert "nautionette-copilot-gpt-4o" not in backend.gateway.resources[MODEL_KIND]
