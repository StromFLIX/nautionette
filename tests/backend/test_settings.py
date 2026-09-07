"""Runtime settings and the history budget derived from them."""

from __future__ import annotations

import pytest
from nautionette_backend import conversations, git_authorship, runtime

DEFAULTS = {
    "default_model": "openai/gpt-4o-mini",
    "default_agent_set": "default",
    "history_chars": 0,
    "git_authorship_mode": "automation",
    "git_human_name": "",
    "git_human_email": "",
    "git_automation_name": "Nautionette",
    "git_automation_email": "nautionette@users.noreply.github.com",
}


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


@pytest.mark.parametrize("mode", sorted(git_authorship.MODES))
def test_git_authorship_round_trip_and_reset(client, mode):
    response = client.put(
        "/api/settings",
        json={
            "git_authorship_mode": mode,
            "git_human_name": "  Jane Contributor  ",
            "git_human_email": " jane@users.noreply.github.com ",
            "git_automation_name": "Project Bot",
            "git_automation_email": "bot@example.test",
        },
    )
    assert response.status_code == 200
    saved = client.get("/api/settings").json()["settings"]
    assert saved["git_authorship_mode"] == mode
    assert saved["git_human_name"] == "Jane Contributor"
    assert saved["git_human_email"] == "jane@users.noreply.github.com"
    assert git_authorship.for_job()["automation_name"] == "Project Bot"
    cleared = client.put("/api/settings", json={key: None for key in git_authorship.DEFAULTS})
    assert cleared.status_code == 200
    assert cleared.json()["settings"] == DEFAULTS


@pytest.mark.parametrize(
    "invalid",
    [
        {"git_authorship_mode": "unknown"},
        {"git_authorship_mode": "human_author"},
        {"git_authorship_mode": "human_author_bot_coauthor"},
        {"git_authorship_mode": "bot_author_human_coauthor"},
        {"git_human_name": "Jane\nCo-authored-by: Other <other@example.test>"},
        {"git_human_email": "jane@example.test\r"},
        {"git_human_email": "missing-at-sign"},
        {"git_human_email": "Jane <jane@example.test>"},
        {"git_automation_name": " "},
        {"git_automation_name": "Bot <other>"},
        {"git_automation_name": "B" * 201},
        {"git_human_name": 123},
        {"git_automation_email": False},
    ],
)
def test_invalid_git_settings_are_rejected_before_any_setting_is_saved(client, invalid):
    response = client.put("/api/settings", json={"default_agent_set": "changed", **invalid})
    assert response.status_code == 422
    assert client.get("/api/settings").json()["settings"] == DEFAULTS


def test_partial_updates_cannot_clear_a_required_human_identity(client):
    client.put(
        "/api/settings",
        json={
            "git_authorship_mode": "human_author",
            "git_human_name": "Jane",
            "git_human_email": "jane@test.dev",
        },
    )
    assert client.put("/api/settings", json={"git_human_email": None}).status_code == 422
    assert client.put("/api/settings", json={"git_human_name": ""}).status_code == 422
    assert client.put("/api/settings", json={"history_chars": 5000}).status_code == 200
    assert client.get("/api/settings").json()["settings"]["git_human_name"] == "Jane"


async def test_project_turn_resolves_current_authorship_at_execution(backend, monkeypatch):
    chat_id = backend.db.create_chat("Git", "default")["id"]
    backend.db.accept_chat_message(chat_id, "Commit", "git-turn")
    backend.db.set_setting("git_authorship_mode", "human_author_bot_coauthor")
    backend.db.set_setting("git_human_name", "Jane")
    backend.db.set_setting("git_human_email", "jane@example.test")
    captured = []

    async def agent(job):
        captured.append(dict(job))
        yield {"type": "result", "ok": True, "text": "Done"}

    async def credentials(ids):
        return []

    monkeypatch.setattr(conversations, "stream_agent", agent)
    monkeypatch.setattr(conversations.projects, "prepare_worktrees", lambda *args: {})
    monkeypatch.setattr(conversations.projects, "agent_credentials", credentials)
    await conversations.run_turn(
        "git-turn",
        chat_id,
        {
            "prompt": "Commit",
            "project_ids": ["a" * 32],
            "git_authorship": {"authorship_mode": "automation"},
        },
    )
    assert captured[0]["git_authorship"]["authorship_mode"] == "human_author_bot_coauthor"
    assert captured[0]["git_authorship"]["human_email"] == "jane@example.test"
    assert "Do not amend or rewrite existing commits" in captured[0]["system_prompt"]
