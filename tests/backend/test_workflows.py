"""Workflows, drafts and the settings the app keeps beside them."""

from __future__ import annotations

import pytest


@pytest.fixture
def digest(backend):
    backend.authoring.add_workflow(
        "url_digest",
        manifest={
            "schema": 1,
            "name": "url_digest",
            "timeout_minutes": 12,
            "inputs": {
                "type": "object",
                "properties": {"url": {"type": "string"}},
                "required": ["url"],
            },
        },
    )
    return backend


def test_listing_merges_schedules_and_local_settings(client, digest):
    digest.temporal.schedule_specs["url_digest"] = {"cron": "0 8 * * *", "input": {}, "paused": False}
    workflow = client.get("/api/workflows").json()["workflows"][0]
    assert workflow["schedule"]["cron"] == "0 8 * * *"
    assert workflow["settings"] == {
        "name": "url_digest",
        "disabled": False,
        "chat_mode": "same",
        "chat_id": None,
    }


def test_a_temporal_that_is_down_does_not_take_the_list_with_it(client, digest, live, monkeypatch):
    digest.temporal.schedule_specs["url_digest"] = {"cron": "0 8 * * *", "input": {}, "paused": False}

    async def refuse():
        raise RuntimeError("temporal is unreachable")

    monkeypatch.setattr(live.temporal, "schedules", refuse)
    workflow = client.get("/api/workflows").json()["workflows"][0]
    assert workflow["schedule"] is None


def test_one_workflow_carries_its_run_history(client, digest):
    client.post("/api/workflows/url_digest/run", json={"input": {"url": "https://example.com"}})
    workflow = client.get("/api/workflows/url_digest").json()
    assert len(workflow["runs"]) == 1
    assert workflow["runs"][0]["trigger"] == "manual"


def test_a_workflow_can_be_disabled(client, digest):
    updated = client.patch("/api/workflows/url_digest/settings", json={"disabled": True}).json()
    assert updated["disabled"] is True
    response = client.post("/api/workflows/url_digest/run", json={})
    assert response.status_code == 409
    assert response.json()["detail"] == "url_digest is disabled"


def test_the_chat_mode_is_one_of_two_words(client, digest):
    assert client.patch("/api/workflows/url_digest/settings", json={"chat_mode": "new"}).status_code == 200
    response = client.patch("/api/workflows/url_digest/settings", json={"chat_mode": "wherever"})
    assert response.status_code == 400


def test_deleting_a_workflow_forgets_its_settings(client, digest):
    client.patch("/api/workflows/url_digest/settings", json={"disabled": True})
    assert client.delete("/api/workflows/url_digest").json() == {"name": "url_digest", "deleted": True}
    assert digest.db.query("SELECT * FROM workflow_settings") == []


def test_validation_is_handed_straight_to_the_authoring_service(client, digest):
    digest.authoring.validation = {"valid": False, "errors": ["no MANIFEST"]}
    assert client.post("/api/workflows/validate", json={"name": "x", "code": ""}).json() == {
        "valid": False,
        "errors": ["no MANIFEST"],
    }


# -------------------------------------------------------------------- schedules


def test_scheduling_needs_a_cron_expression(client, digest):
    response = client.post("/api/workflows/url_digest/schedule", json={"cron": "  "})
    assert response.status_code == 400
    assert "cron is required" in response.json()["detail"]


def test_a_schedule_can_be_set_and_taken_away(client, digest):
    result = client.post(
        "/api/workflows/url_digest/schedule", json={"cron": "0 8 * * *", "input": {"url": "https://a"}}
    ).json()
    assert result == {"schedule_id": "schedule-url_digest", "cron": "0 8 * * *", "paused": False}
    assert digest.temporal.schedule_specs["url_digest"]["input"] == {"url": "https://a"}

    assert client.delete("/api/workflows/url_digest/schedule").json() == {"ok": True}
    assert digest.temporal.schedule_specs == {}


# ----------------------------------------------------------------------- drafts


def test_a_draft_can_be_read_approved_or_thrown_away(client, digest):
    digest.authoring.drafts["new_flow"] = {"name": "new_flow", "code": "MANIFEST = {}\n", "diff": "+++"}
    assert client.get("/api/drafts").json()["drafts"][0]["name"] == "new_flow"
    assert client.get("/api/drafts/new_flow").json()["code"] == "MANIFEST = {}\n"

    published = client.post("/api/drafts/new_flow/approve").json()
    assert published["published"] is True
    assert "worker_restart" in published
    assert "new_flow" in digest.authoring.workflows

    digest.authoring.drafts["other"] = {"name": "other", "code": "", "diff": ""}
    assert client.delete("/api/drafts/other").json() == {"name": "other", "discarded": True}


def test_approving_reports_a_worker_that_would_not_restart(client, digest):
    digest.broker.restart_error = RuntimeError("docker is unreachable")
    digest.authoring.drafts["new_flow"] = {"name": "new_flow", "code": "", "diff": ""}
    published = client.post("/api/drafts/new_flow/approve").json()
    assert published["worker_restart"] == {"ok": False, "error": "docker is unreachable"}
