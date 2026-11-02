"""Workflow accounting survives failure, retries, restarts and Temporal expiry."""

import json

import pytest
from nautionette_backend import run_statistics as accounting
from nautionette_backend.db import Database


def usage(input=10, output=3, cacheRead=2, cacheWrite=1):
    return dict(input=input, output=output, cacheRead=cacheRead, cacheWrite=cacheWrite)


def test_usage_is_summed_across_tool_loop_not_last_response():
    stats = accounting.AgentStatistics()
    stats.observe({"type": "usage", "usage": usage()})
    stats.observe({"type": "usage", "usage": usage(input=20, output=5)})
    stats.observe({"type": "result", "usage": usage(input=20, output=5)})
    result = stats.snapshot()
    assert result["tokens"] == usage(input=30, output=8, cacheRead=4, cacheWrite=2)
    assert result["model_requests"] == result["usage_reports"] == 2
    assert result["usage_complete"] is True


@pytest.mark.parametrize("missing", [None, {}, usage(0, 0, 0, 0), usage(input=-1), usage(input=True)])
def test_unknown_or_malformed_usage_never_invents_zero_tokens(missing):
    stats = accounting.AgentStatistics()
    stats.observe({"type": "usage", "usage": missing})
    assert stats.snapshot()["tokens"] is None
    assert stats.snapshot()["model_requests"] == 1
    assert stats.snapshot()["usage_complete"] is False
    stats.observe({"type": "usage", "usage": usage()})
    assert stats.snapshot()["tokens"] == usage()
    assert stats.snapshot()["usage_complete"] is False


def test_older_images_provide_only_a_partial_lower_bound():
    stats = accounting.AgentStatistics()
    stats.observe({"type": "result", "usage": usage()})
    assert stats.snapshot()["tokens"] == usage()
    assert stats.snapshot()["model_requests"] == 0
    assert stats.snapshot()["usage_complete"] is False


def test_legacy_context_events_preserve_full_usage_without_double_counting_cache():
    stats = accounting.AgentStatistics()
    for _ in range(2):
        stats.observe(
            {
                "type": "usage",
                "context": {
                    "source": "provider",
                    "input_tokens": 13,
                    "output_tokens": 3,
                    "cache_read_tokens": 2,
                    "cache_write_tokens": 1,
                },
            }
        )
    stats.observe({"type": "result", "usage": usage()})
    result = stats.snapshot()
    assert result["tokens"] == usage(20, 6, 4, 2)
    assert result["model_requests"] == result["usage_reports"] == 2
    assert result["usage_complete"] is True


def test_last_response_fallback_remains_partial_after_unreported_usage_events():
    stats = accounting.AgentStatistics()
    stats.observe({"type": "usage", "context": None})
    stats.observe({"type": "usage", "context": None})
    stats.observe({"type": "result", "usage": usage()})
    assert stats.snapshot()["tokens"] == usage()
    assert stats.snapshot()["model_requests"] == 2
    assert stats.snapshot()["usage_complete"] is False


def test_tool_starts_and_finishes_are_one_call_with_explicit_provenance():
    stats = accounting.AgentStatistics()
    for event in [
        {"type": "tool", "id": "1", "name": "unprefixed", "source": "mcp", "args": {"secret": "omit"}},
        {"type": "tool_done", "id": "1", "name": "unprefixed", "error": True, "result": "private"},
        {"type": "tool_done", "id": "1", "name": "unprefixed", "error": True},
        {"type": "tool", "id": "2", "name": "bash", "source": "local"},
        {"type": "tool", "id": "3", "name": "looks_like_mcp_but_is_not"},
    ]:
        stats.observe(event)
    result = stats.snapshot()
    assert result["tools"] == [
        {"name": "bash", "source": "local", "calls": 1, "running": 1},
        {"name": "looks_like_mcp_but_is_not", "source": "extension", "calls": 1, "running": 1},
        {"name": "unprefixed", "source": "mcp", "calls": 1, "failed": 1},
    ]
    assert "secret" not in json.dumps(result)
    assert "private" not in json.dumps(result)


def test_all_attempts_are_accounted_and_reads_survive_database_reopen(db, monkeypatch):
    row = db.record_run("digest", "wf", "r1", "manual", {})
    stats = accounting.AgentStatistics()
    stats.observe({"type": "usage", "usage": usage()})
    stats.observe({"type": "tool", "id": "1", "name": "search", "source": "mcp"})
    stats.observe({"type": "tool_done", "id": "1", "name": "search"})
    accounting.record_call(
        "wf", "agent-1", "agent", "research", "failed", model="test/m1", statistics=stats.snapshot()
    )
    accounting.record_call(
        "wf", "agent-2", "agent", "default", "completed", model="test/m2", statistics=stats.snapshot()
    )
    accounting.record_call("wf", "mcp:attempt-1", "mcp", "search", "failed")
    accounting.record_call("wf", "mcp:attempt-2", "mcp", "search", "running")
    accounting.record_call("wf", "mcp:attempt-2", "mcp", "search", "completed")
    # Replayed observations and delayed starts don't double-count or regress status.
    accounting.record_call("wf", "mcp:attempt-2", "mcp", "search", "running")
    reopened = Database(db.path)
    try:
        monkeypatch.setattr(accounting, "db", reopened)
        result = accounting.statistics("wf", row)
    finally:
        reopened._conn.close()
    assert result["available"] is True
    assert result["coverage"] == "recorded"
    assert result["agent_calls"] == 2
    assert result["model_requests"] == 2
    assert result["tool_calls"] == result["mcp_calls"] == 4
    assert result["direct_mcp_calls"] == 2
    assert result["tokens"]["total"] == 32
    assert {agent["agent_set"] for agent in result["agents"]} == {"research", "default"}
    assert {agent["model"] for agent in result["agents"]} == {"test/m1", "test/m2"}
    direct = next(tool for tool in result["tools"] if tool["source"] == "workflow")
    assert direct["failed"] == direct["completed"] == 1
    assert direct.get("running", 0) == 0


def test_live_counts_become_interrupted_not_successful_when_run_ends(db):
    row = db.record_run("digest", "wf", "r1", "manual", {})
    stats = accounting.AgentStatistics()
    stats.observe({"type": "tool", "id": "1", "name": "search", "source": "mcp"})
    accounting.record_call("wf", "agent", "agent", "default", "running", statistics=stats.snapshot())
    accounting.record_call("wf", "mcp", "mcp", "search", "running")
    assert accounting.statistics("wf", row)["agents"][0]["running"] == 1
    row["status"] = "terminated"
    result = accounting.statistics("wf", row)
    assert result["agents"][0]["interrupted"] == 1
    assert all(tool["interrupted"] == 1 for tool in result["tools"])
    assert all(tool.get("completed", 0) == 0 for tool in result["tools"])


def test_legacy_and_zero_call_runs_are_distinguishable(db):
    row = db.record_run("digest", "wf", "r1", "manual", {})
    assert accounting.statistics("wf", row)["available"] is True
    assert accounting.statistics("wf", row)["agent_calls"] == 0
    row["statistics_version"] = 0
    assert accounting.statistics("wf", row)["available"] is False
    accounting.record_call("wf", "mcp", "mcp", "search", "completed")
    assert accounting.statistics("wf", row)["coverage"] == "partial"


def test_agent_activity_persists_usage_even_when_it_fails(client, internal_headers, broker, db):
    db.record_run("digest", "wf", "r1", "manual", {})
    broker.events = [
        {"type": "usage", "usage": usage()},
        {"type": "tool", "id": "1", "name": "search", "source": "mcp"},
        {"type": "tool_done", "id": "1", "name": "search", "error": True},
        {"type": "usage", "usage": usage()},
        {"type": "error", "message": "out of budget"},
    ]
    result = client.post(
        "/internal/agent/call",
        headers=internal_headers,
        json={
            "run_id": "wf",
            "prompt": "do not persist this prompt",
            "agent_set": "research",
        },
    ).json()
    assert result["ok"] is False
    # No Temporal execution remains, but both user and authoring-agent APIs have the statistics.
    for path in ["/api/runs/wf", "/api/runs/wf/history", "/internal/runs/wf"]:
        response = client.get(path, headers=internal_headers).json()
        assert response["history_status"] == "expired"
        stats = response["statistics"]
        assert stats["agent_calls"] == stats["mcp_calls"] == 1
        assert stats["agents"][0]["failed"] == 1
        assert stats["model_requests"] == 2
        assert stats["tokens"]["total"] == 32
    assert "do not persist" not in json.dumps(db.query("SELECT * FROM workflow_calls"))


def test_agent_transport_exception_preserves_observed_statistics(client, internal_headers, monkeypatch, db):
    from nautionette_backend.routers import internal

    async def broken(job, *, on_event):
        on_event({"type": "usage", "usage": usage()})
        raise RuntimeError("broker disconnected")

    monkeypatch.setattr(internal, "call_agent", broken)
    with pytest.raises(RuntimeError, match="disconnected"):
        client.post("/internal/agent/call", headers=internal_headers, json={"run_id": "wf"})
    result = accounting.statistics("wf")
    assert result["agents"][0]["interrupted"] == 1
    assert result["tokens"]["total"] == 16


def test_statistics_write_failure_does_not_repeat_an_agent_side_effect(
    client, internal_headers, broker, monkeypatch, caplog
):
    def unavailable(*args, **kwargs):
        raise RuntimeError("statistics storage unavailable")

    monkeypatch.setattr(accounting, "record_call", unavailable)
    broker.events = [
        {"type": "tool", "id": "1", "name": "create_issue", "source": "mcp"},
        {"type": "tool_done", "id": "1", "name": "create_issue"},
        {"type": "usage", "usage": usage()},
        {"type": "result", "ok": True, "text": "done"},
    ]
    response = client.post("/internal/agent/call", headers=internal_headers, json={"run_id": "wf"})
    assert response.status_code == 200
    assert response.json()["ok"] is True
    assert len(broker.jobs) == 1
    assert "Could not record agent statistics" in caplog.text


def test_mcp_observations_are_authenticated_bounded_and_idempotent(client, internal_headers, db):
    payload = {"call_id": "run:activity:1", "tool": "search", "status": "running"}
    assert client.post("/internal/runs/wf/mcp-calls", json=payload).status_code == 401
    for status in ["running", "completed", "running", "completed"]:
        response = client.post(
            "/internal/runs/wf/mcp-calls", headers=internal_headers, json={**payload, "status": status}
        )
        assert response.status_code == 200
    result = accounting.statistics("wf")
    assert result["mcp_calls"] == 1
    assert result["tools"][0]["completed"] == 1
    response = client.post(
        "/internal/runs/wf/mcp-calls", headers=internal_headers, json={**payload, "tool": "x" * 513}
    )
    assert response.status_code == 422


def test_migration_preserves_old_runs_without_claiming_recorded_statistics(tmp_path):
    path = str(tmp_path / "old.sqlite")
    old = Database(path)
    old.record_run("digest", "legacy", "r1", "manual", {"url": "https://example.com"})
    old.execute("ALTER TABLE runs DROP COLUMN statistics_version")
    old._conn.close()
    upgraded = Database(path)
    try:
        row = upgraded.one("SELECT * FROM runs WHERE workflow_id = 'legacy'")
        assert row["statistics_version"] == 0
        assert json.loads(row["input"]) == {"url": "https://example.com"}
        assert upgraded.record_run("digest", "new", "r2", "manual", {})["statistics_version"] == 1
    finally:
        upgraded._conn.close()


def test_live_run_detail_includes_statistics(client, backend, db):
    db.record_run("digest", "wf", "r1", "manual", {})
    backend.temporal.executions["wf"] = {"workflow_id": "wf", "run_id": "r1", "status": "RUNNING"}
    accounting.record_call("wf", "mcp", "mcp", "search", "running")
    detail = client.get("/api/runs/wf").json()
    assert detail["statistics"]["tools"][0]["running"] == 1
