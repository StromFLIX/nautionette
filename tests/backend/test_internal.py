"""The internal API: what the worker and the authoring server are allowed to ask for."""

from __future__ import annotations

import pytest


def test_an_activity_gets_one_object_back(client, internal_headers, broker):
    broker.events = [
        {"type": "delta", "text": "part "},
        {"type": "tool", "name": "http_fetch"},
        {"type": "result", "ok": True, "text": "", "output": {"summary": "done"}, "usage": {"tokens": 9}},
    ]
    result = client.post(
        "/internal/agent/call",
        headers=internal_headers,
        json={"prompt": "summarise", "output_schema": {"type": "object"}, "run_id": "wf-1"},
    ).json()
    assert result == {
        "ok": True,
        "text": "part ",
        "output": {"summary": "done"},
        "error": None,
        "tools": ["http_fetch"],
        "usage": {"tokens": 9},
    }
    assert broker.jobs[0]["mode"] == "activity"
    assert broker.jobs[0]["run_id"] == "wf-1"


def test_an_agent_that_never_produced_a_result_is_not_reported_as_one(client, internal_headers, broker):
    broker.events = [{"type": "error", "message": "container exited 1"}]
    result = client.post(
        "/internal/agent/call", headers=internal_headers, json={"prompt": "hi"}
    ).json()
    assert result["ok"] is False
    assert result["error"] == "container exited 1"


def test_a_worker_event_reaches_the_clients(client, internal_headers, db):
    client.post(
        "/internal/events",
        headers=internal_headers,
        json={"scope": "worker", "kind": "worker.loaded", "payload": {"loaded": 2}},
    )
    assert db.recent_events()[-1]["kind"] == "worker.loaded"
    assert client.get("/api/events/recent").json()["events"][-1]["loaded"] == 2


def test_restarting_the_worker_is_reported_never_fatal(client, internal_headers, broker):
    assert client.post("/internal/worker/restart", headers=internal_headers).json()["ok"] is True
    assert broker.restarts == 1

    broker.restart_error = RuntimeError("docker is unreachable")
    result = client.post("/internal/worker/restart", headers=internal_headers).json()
    assert result == {"ok": False, "error": "docker is unreachable"}


@pytest.fixture
def one_run(client, backend):
    backend.authoring.add_workflow("url_digest", manifest={"schema": 1, "name": "url_digest"})
    started = client.post("/api/workflows/url_digest/run", json={"input": {"url": "https://a"}}).json()
    return started["workflow_id"]


def test_an_authoring_agent_can_read_the_run_history(client, internal_headers, one_run):
    runs = client.get("/internal/runs", headers=internal_headers).json()["runs"]
    assert runs == [
        {
            "workflow": "url_digest",
            "workflow_id": one_run,
            "status": "running",
            "trigger": "manual",
            "started": "2026-01-01T00:00:00+00:00",
            "closed": None,
        }
    ]


def test_one_run_reads_back_with_its_timeline(client, internal_headers, one_run, backend):
    backend.temporal.histories[one_run] = [{"id": 1, "event": "workflow.started", "input": {"url": "https://a"}}]
    detail = client.get(f"/internal/runs/{one_run}", headers=internal_headers).json()
    assert detail["input"] == {"url": "https://a"}
    assert detail["events"][0]["event"] == "workflow.started"
    assert "note" not in detail


def test_an_empty_timeline_says_so_rather_than_looking_like_nothing_happened(
    client, internal_headers, one_run
):
    detail = client.get(f"/internal/runs/{one_run}", headers=internal_headers).json()
    assert detail["events"] == []
    assert "Temporal has no history" in detail["note"]


def test_a_run_nobody_has_heard_of_is_a_404(client, internal_headers):
    assert client.get("/internal/runs/never-existed", headers=internal_headers).status_code == 404
