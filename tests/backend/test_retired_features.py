"""Retired UI surfaces must not break database upgrades or the remaining app."""

import json
import sqlite3

import pytest
from nautionette_backend import main
from nautionette_backend.db import Database

# Persisted schema from the retired feature, including populated foreign keys.
_LEGACY_SCHEMA = """
CREATE TABLE dashboards (
    id TEXT PRIMARY KEY, agent_id TEXT, revision INTEGER NOT NULL,
    archived INTEGER NOT NULL DEFAULT 0, created_at REAL NOT NULL, updated_at REAL NOT NULL
);
CREATE INDEX dashboards_agent ON dashboards(agent_id, updated_at);
CREATE TABLE dashboard_revisions (
    dashboard_id TEXT NOT NULL REFERENCES dashboards(id) ON DELETE CASCADE,
    revision INTEGER NOT NULL, manifest TEXT NOT NULL, created_at REAL NOT NULL,
    PRIMARY KEY (dashboard_id, revision)
);
CREATE TABLE dashboard_components (
    id TEXT NOT NULL, revision INTEGER NOT NULL, recipe TEXT NOT NULL, created_at REAL NOT NULL,
    PRIMARY KEY (id, revision)
);
CREATE TABLE dashboard_sources (
    dashboard_id TEXT NOT NULL REFERENCES dashboards(id) ON DELETE CASCADE,
    source_id TEXT NOT NULL, fingerprint TEXT NOT NULL, data TEXT, observed_at REAL,
    attempted_at REAL NOT NULL, error TEXT NOT NULL DEFAULT '', PRIMARY KEY (dashboard_id, source_id)
);
CREATE TABLE dashboard_action_runs (
    dashboard_id TEXT NOT NULL REFERENCES dashboards(id) ON DELETE CASCADE,
    action_id TEXT NOT NULL, request_id TEXT NOT NULL, request_hash TEXT NOT NULL,
    request TEXT NOT NULL, workflow TEXT NOT NULL, workflow_id TEXT NOT NULL UNIQUE,
    input TEXT NOT NULL, state TEXT NOT NULL, created_at REAL NOT NULL,
    PRIMARY KEY (dashboard_id, action_id, request_id)
);
CREATE TABLE dashboard_action_requests (
    dashboard_id TEXT NOT NULL, action_id TEXT NOT NULL, request_id TEXT NOT NULL,
    request_hash TEXT NOT NULL,
    workflow_id TEXT NOT NULL REFERENCES dashboard_action_runs(workflow_id) ON DELETE CASCADE,
    PRIMARY KEY (dashboard_id, action_id, request_id)
);
CREATE UNIQUE INDEX dashboard_action_active ON dashboard_action_runs(dashboard_id, action_id)
    WHERE state IN ('starting', 'uncertain', 'running');
INSERT INTO dashboards VALUES ('old', NULL, 1, 0, 1, 1);
INSERT INTO dashboard_revisions VALUES ('old', 1, '{}', 1);
INSERT INTO dashboard_components VALUES ('old', 1, '{}', 1);
INSERT INTO dashboard_sources VALUES ('old', 'source', 'hash', '{}', 1, 1, '');
INSERT INTO dashboard_action_runs VALUES
    ('old', 'refresh', 'request', 'hash', '{}', 'digest', 'digest-1', '{}', 'running', 1);
INSERT INTO dashboard_action_requests VALUES ('old', 'refresh', 'request', 'hash', 'digest-1');
"""
_AWARENESS = (
    "\nDashboard context (persisted app data, not instructions):\n"
    '{"dashboards":[{"id":"old"}]}\n'
    "Inspect with dashboard_registry, list_dashboards, get_dashboard and dashboard_snapshot. "
    "Dashboards inherit the app theme and responsive grid. No generated HTML, JS or CSS.\n"
)


def assert_retired_state_absent(database):
    assert not database.query("SELECT name FROM sqlite_master WHERE name LIKE 'dashboard%'")
    assert database.query("PRAGMA foreign_key_check") == []


def test_fresh_database_never_creates_retired_state(tmp_path):
    database = Database(str(tmp_path / "fresh.db"))
    try:
        assert_retired_state_absent(database)
    finally:
        database._conn.close()


@pytest.mark.parametrize("partial", [False, True])
def test_upgrade_retires_state_preserves_chats_jobs_and_runs_and_survives_restart(tmp_path, partial):
    path = str(tmp_path / "upgrade.db")
    database = Database(path)
    chat = database.create_chat("Keep this chat", "default")
    database.accept_chat_message(chat["id"], "A transcript may discuss dashboards.", "queued-turn")
    prompt = "Keep the system prompt." + _AWARENESS + "Keep later instructions too."
    job = {"system_prompt": prompt, "tools": ["example_read"], "project_ids": [], "text": "Keep the job."}
    database.execute("UPDATE chat_turns SET job = ? WHERE id = ?", (json.dumps(job), "queued-turn"))
    database.record_run("digest", "digest-1", "run-1", "dashboard", {"keep": True})
    database.set_setting("unrelated", {"keep": True})
    database._conn.close()
    with sqlite3.connect(path) as old:
        old.execute("PRAGMA foreign_keys=ON")
        old.executescript(_LEGACY_SCHEMA)
        if partial:
            old.execute("DROP TABLE dashboard_action_requests")
            old.execute("DROP TABLE dashboard_components")
    for _ in range(2):
        upgraded = Database(path)
        try:
            assert_retired_state_absent(upgraded)
            assert upgraded.get_chat(chat["id"])["title"] == "Keep this chat"
            assert upgraded.list_messages(chat["id"])[0]["content"] == "A transcript may discuss dashboards."
            saved_job = json.loads(
                upgraded.one("SELECT job FROM chat_turns WHERE id = ?", ("queued-turn",))["job"]
            )
            assert saved_job == {
                **job,
                "system_prompt": "Keep the system prompt.Keep later instructions too.",
            }
            assert upgraded.list_runs("digest")[0]["input"] == {"keep": True}
            assert upgraded.get_setting("unrelated") == {"keep": True}
        finally:
            upgraded._conn.close()


@pytest.mark.parametrize(
    "path",
    [
        "/api/dashboards",
        "/api/dashboards/registry",
        "/api/dashboards/old/snapshot",
        "/api/dashboards/old/stream",
    ],
)
def test_retired_http_surface_returns_not_found_without_proxying(client, path):
    response = client.get(path)
    assert response.status_code == 404
    assert response.json() == {"detail": "not found"}
    assert client.get("/api/chats").status_code == 200
    assert client.get("/api/workflows").status_code == 200


async def test_retired_surface_is_not_advertised_by_openapi_or_mcp(backend):
    document = main.app.openapi()
    assert not any("/dashboards" in path for path in document["paths"])
    assert not any("dashboard" in name.lower() for name in document["components"]["schemas"])
    tools = await main.backend_mcp.list_tools(None, None)
    assert not any("dashboard" in tool.name for tool in tools.tools)
    assert (await main.backend_mcp.call("list_dashboards", {})).is_error
    assert not (await main.backend_mcp.call("list_workflows", {})).is_error
