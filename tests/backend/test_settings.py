"""Runtime settings and the history budget derived from them."""

from __future__ import annotations

from nautionette_backend import runtime

DEFAULTS = {"default_model": "openai/gpt-4o-mini", "default_agent_set": "default", "history_chars": 0}


def test_defaults_come_from_the_environment(client):
    payload = client.get("/api/settings").json()
    assert payload["defaults"] == DEFAULTS
    assert payload["settings"] == DEFAULTS


def test_saving_a_value_overrides_the_environment(client):
    payload = client.put("/api/settings", json={"default_model": "anthropic/claude"}).json()
    assert payload["settings"]["default_model"] == "anthropic/claude"
    assert payload["defaults"]["default_model"] == "openai/gpt-4o-mini"
    assert client.get("/api/settings").json()["settings"]["default_model"] == "anthropic/claude"


def test_clearing_a_value_falls_back_to_the_environment(client):
    client.put("/api/settings", json={"default_model": "anthropic/claude"})
    payload = client.put("/api/settings", json={"default_model": None}).json()
    assert payload["settings"]["default_model"] == "openai/gpt-4o-mini"


def test_unknown_keys_are_ignored(client):
    client.put("/api/settings", json={"app_token": "stolen", "default_agent_set": "research"})
    payload = client.get("/api/settings").json()
    assert "app_token" not in payload["settings"]
    assert payload["settings"]["default_agent_set"] == "research"


def test_history_chars_is_clamped_to_a_usable_range(client):
    assert client.put("/api/settings", json={"history_chars": 5}).json()["settings"]["history_chars"] == 2_000
    assert (
        client.put("/api/settings", json={"history_chars": 9_000_000}).json()["settings"]["history_chars"]
        == 2_000_000
    )


def test_a_history_of_zero_or_less_means_work_it_out_from_the_model(client):
    assert client.put("/api/settings", json={"history_chars": 0}).json()["settings"]["history_chars"] == 0
    assert client.put("/api/settings", json={"history_chars": -1}).json()["settings"]["history_chars"] == 0


def test_saving_settings_drops_the_catalog_cache(client):
    runtime.cache_catalog({"stale": True})
    client.put("/api/settings", json={"default_agent_set": "default"})
    assert runtime.cached_catalog() is None


def test_the_budget_follows_the_model_window(db):
    runtime.model_windows.update({"big/model": 200_000})
    # Half the window, at four characters per token.
    assert runtime.history_budget("big/model") == 400_000


def test_an_unknown_model_falls_back_to_a_fixed_budget(db):
    assert runtime.history_budget("model/nobody-published") == runtime.DEFAULT_HISTORY_CHARS


def test_an_explicit_budget_beats_the_model_window(db):
    runtime.model_windows.update({"big/model": 200_000})
    db.set_setting("history_chars", 12_345)
    assert runtime.history_budget("big/model") == 12_345
