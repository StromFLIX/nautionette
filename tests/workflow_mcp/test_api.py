"""The REST face the backend calls, so approval stays a human action."""

from __future__ import annotations

import pytest
from nautionette_workflow_mcp import store

from ..conftest import json_body
from .conftest import GOOD_WORKFLOW


@pytest.fixture
def backend_up(http):
    http["http://backend:8080"] = json_body({"ok": True, "restarted": ["worker-1"]})
    return http


def test_health_says_what_is_on_the_volume(client, workflows):
    (workflows / "url_digest.py").write_text(GOOD_WORKFLOW)
    payload = client.get("/healthz").json()
    assert payload["status"] == "ok"
    assert payload["workflows"] == 1
    assert payload["drafts"] == 0


def test_the_manifest_schema_is_published(client):
    payload = client.get("/api/schema").json()
    assert payload["schema_version"] == 1
    assert payload["manifest_schema"]["required"] == ["schema", "name", "inputs", "outputs"]


def test_a_workflow_can_be_listed_and_read(client, workflows):
    (workflows / "url_digest.py").write_text(GOOD_WORKFLOW)
    assert [w["name"] for w in client.get("/api/workflows").json()["workflows"]] == ["url_digest"]
    assert client.get("/api/workflows/url_digest").json()["code"] == GOOD_WORKFLOW
    assert client.get("/api/workflows/nope").status_code == 404


def test_a_write_lands_in_a_draft_never_on_the_live_file(client, workflows):
    draft = client.post("/api/drafts", json={"name": "url_digest", "code": GOOD_WORKFLOW}).json()
    assert draft["is_new"] is True
    assert draft["validation"]["valid"] is True
    assert not (workflows / "url_digest.py").exists()


def test_a_draft_that_does_not_validate_is_still_saved_for_a_human_to_fix(client, workflows):
    draft = client.post("/api/drafts", json={"name": "url_digest", "code": "x = 1\n"}).json()
    assert draft["validation"]["valid"] is False
    assert "url_digest" in [entry["name"] for entry in store.list_drafts()]


def test_a_name_that_could_not_be_a_module_never_reaches_the_disk(client):
    assert client.post("/api/drafts", json={"name": "../escape", "code": "x"}).status_code == 400
    assert client.post("/api/validate", json={"name": "../escape", "code": "x"}).json() == {
        "valid": False,
        "errors": ["name must be lowercase letters, digits and underscores, 3-64 characters"],
        "warnings": [],
        "manifest": None,
        "steps": [],
    }


def test_approving_a_draft_publishes_it_and_asks_for_a_worker(client, workflows, backend_up):
    client.post("/api/drafts", json={"name": "url_digest", "code": GOOD_WORKFLOW})
    published = client.post("/api/drafts/url_digest/publish").json()
    assert published["published"] is True
    assert (workflows / "url_digest.py").read_text() == GOOD_WORKFLOW


def test_a_draft_that_does_not_validate_cannot_be_published(client, workflows):
    client.post("/api/drafts", json={"name": "url_digest", "code": "x = 1\n"})
    response = client.post("/api/drafts/url_digest/publish")
    assert response.status_code == 400
    assert "does not validate" in response.json()["detail"]
    assert not (workflows / "url_digest.py").exists()


def test_publishing_a_draft_nobody_wrote_is_a_404(client, workflows):
    assert client.post("/api/drafts/nope/publish").status_code == 404
    assert client.get("/api/drafts/nope").status_code == 404


def test_a_draft_can_be_thrown_away(client, workflows):
    client.post("/api/drafts", json={"name": "url_digest", "code": GOOD_WORKFLOW})
    assert client.delete("/api/drafts/url_digest").json() == {"name": "url_digest", "discarded": True}
    assert client.get("/api/drafts").json()["drafts"] == []


def test_deleting_a_workflow_reports_the_worker_restart_it_asked_for(client, workflows, backend_up):
    (workflows / "url_digest.py").write_text(GOOD_WORKFLOW)
    result = client.delete("/api/workflows/url_digest").json()
    assert result["deleted"] is True
    assert result["worker_restart"] == {"ok": True, "restarted": ["worker-1"]}


def test_a_backend_that_cannot_be_reached_does_not_fail_the_delete(client, workflows, http):
    (workflows / "url_digest.py").write_text(GOOD_WORKFLOW)
    result = client.delete("/api/workflows/url_digest").json()
    assert result["deleted"] is True
    assert result["worker_restart"]["ok"] is False
