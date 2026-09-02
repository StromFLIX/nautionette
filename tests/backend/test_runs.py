"""Starting runs, following them, and delivering what they produced."""

from __future__ import annotations

import pytest
from nautionette_backend import runs as runs_service

from ..conftest import APP_TOKEN


@pytest.fixture
def digest(backend):
    backend.authoring.add_workflow(
        "url_digest",
        title="URL digest",
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


def test_starting_a_run_records_it_and_honours_the_manifest_timeout(client, digest):
    result = client.post("/api/workflows/url_digest/run", json={"input": {"url": "https://a"}}).json()
    assert result["workflow"] == "url_digest"
    assert result["trigger"] == "manual"
    assert digest.temporal.started[0]["timeout"] == 12

    run = client.get("/api/runs").json()["runs"][0]
    assert (run["workflow"], run["trigger"], run["status"]) == ("url_digest", "manual", "running")
    assert run["input"] == {"url": "https://a"}


def test_input_is_checked_against_the_manifest_before_temporal_sees_it(client, digest):
    response = client.post("/api/workflows/url_digest/run", json={"input": {}})
    assert response.status_code == 400
    assert response.json()["detail"] == {"workflow": "url_digest", "input": ["'url' is a required property"]}
    assert digest.temporal.started == []


def test_the_live_status_wins_over_what_was_recorded(client, digest):
    started = client.post("/api/workflows/url_digest/run", json={"input": {"url": "https://a"}}).json()
    digest.temporal.executions[started["workflow_id"]].update(
        status="COMPLETED", close_time="2026-01-01T00:01:00+00:00"
    )
    run = client.get("/api/runs").json()["runs"][0]
    assert run["status"] == "completed"
    assert run["close_time"] == "2026-01-01T00:01:00+00:00"


def test_one_run_carries_the_result_temporal_still_holds(client, digest):
    started = client.post("/api/workflows/url_digest/run", json={"input": {"url": "https://a"}}).json()
    workflow_id = started["workflow_id"]
    digest.temporal.executions[workflow_id]["status"] = "COMPLETED"
    digest.temporal.results[workflow_id] = {"summary": "all quiet"}

    payload = client.get(f"/api/runs/{workflow_id}").json()
    assert payload["run"]["input"] == {"url": "https://a"}
    assert payload["temporal"]["result"] == {"summary": "all quiet"}


def test_a_run_can_be_asked_to_stop_or_made_to(client, digest):
    started = client.post("/api/workflows/url_digest/run", json={"input": {"url": "https://a"}}).json()
    workflow_id = started["workflow_id"]

    assert client.post(f"/api/runs/{workflow_id}/cancel").json() == {"ok": True}
    assert digest.temporal.cancelled == [workflow_id]

    assert client.post(f"/api/runs/{workflow_id}/terminate", json={"reason": "stuck"}).json() == {"ok": True}
    assert digest.temporal.terminated == [(workflow_id, "stuck")]
    assert digest.db.list_runs("url_digest")[0]["status"] == "terminated"


def test_runs_can_be_narrowed_to_one_workflow(client, digest):
    digest.authoring.add_workflow("hello_world", manifest={"schema": 1, "name": "hello_world"})
    client.post("/api/workflows/url_digest/run", json={"input": {"url": "https://a"}})
    client.post("/api/workflows/hello_world/run", json={})
    assert len(client.get("/api/runs").json()["runs"]) == 2
    narrowed = client.get("/api/runs", params={"workflow": "hello_world"}).json()["runs"]
    assert [run["workflow"] for run in narrowed] == ["hello_world"]


# -------------------------------------------------------------------- triggers


def test_a_webhook_starts_a_run(client, digest, anonymous):
    response = anonymous.post(
        "/api/triggers/url_digest",
        json={"url": "https://a"},
        headers={"Authorization": f"Bearer {APP_TOKEN}"},
    )
    assert response.status_code == 200
    assert response.json()["trigger"] == "webhook"


def test_a_webhook_needs_the_app_token(anonymous, digest):
    assert anonymous.post("/api/triggers/url_digest", json={"url": "https://a"}).status_code == 401
    assert (
        anonymous.post(
            "/api/triggers/url_digest", params={"token": APP_TOKEN}, json={"url": "https://a"}
        ).status_code
        == 200
    )


def test_a_webhook_body_that_is_not_an_object_is_wrapped(client, backend):
    backend.authoring.add_workflow("hello_world", manifest={"schema": 1, "name": "hello_world"})
    client.post("/api/triggers/hello_world", json=[1, 2, 3])
    assert backend.temporal.started[0]["input"] == {"payload": [1, 2, 3]}


def test_a_webhook_with_no_body_at_all_still_runs(client, backend):
    backend.authoring.add_workflow("hello_world", manifest={"schema": 1, "name": "hello_world"})
    response = client.post(
        "/api/triggers/hello_world", content=b"", headers={"Content-Type": "application/json"}
    )
    assert response.status_code == 200
    assert backend.temporal.started[0]["input"] == {}


# ------------------------------------------------------- what a run comes back as


@pytest.mark.parametrize(
    ("result", "expected"),
    [
        (None, ""),
        ("plain prose", "plain prose"),
        ({"summary": "one field is unwrapped"}, "one field is unwrapped"),
    ],
)
def test_a_readable_result_is_left_readable(result, expected):
    assert runs_service.render_result(result) == expected


def test_anything_shaped_arrives_as_json_you_can_quote():
    rendered = runs_service.render_result({"a": 1, "b": 2})
    assert rendered.startswith("```json\n{")
    assert '"a": 1' in rendered


async def test_a_finished_run_lands_in_its_own_chat(digest):
    digest.db.set_workflow_settings("url_digest", {"chat_mode": "same"})
    await runs_service.deliver_to_chat("url_digest", "url_digest-1", "completed", {"summary": "all quiet"})

    chat_id = digest.db.workflow_settings("url_digest")["chat_id"]
    assert chat_id
    message = digest.db.list_messages(chat_id)[0]
    assert message["content"] == "all quiet"
    assert message["meta"]["run"] == {
        "workflow": "url_digest",
        "workflow_id": "url_digest-1",
        "status": "completed",
    }


async def test_the_same_chat_is_reused_across_runs(digest):
    digest.db.set_workflow_settings("url_digest", {"chat_mode": "same"})
    await runs_service.deliver_to_chat("url_digest", "run-1", "completed", "first")
    await runs_service.deliver_to_chat("url_digest", "run-2", "completed", "second")
    assert len(digest.db.list_chats()) == 1


async def test_a_new_chat_per_run_is_a_setting(digest):
    digest.db.set_workflow_settings("url_digest", {"chat_mode": "new"})
    await runs_service.deliver_to_chat("url_digest", "run-1", "completed", "first")
    await runs_service.deliver_to_chat("url_digest", "run-2", "completed", "second")
    assert len(digest.db.list_chats()) == 2


async def test_a_run_that_produced_nothing_still_says_how_it_ended(digest):
    await runs_service.deliver_to_chat("url_digest", "run-1", "failed", None)
    chat_id = digest.db.list_chats()[0]["id"]
    assert digest.db.list_messages(chat_id)[0]["content"] == "`failed`"


async def test_a_deleted_workflow_still_delivers_its_last_result(backend):
    await runs_service.deliver_to_chat("gone_away", "run-1", "completed", "the last word")
    chat = backend.db.list_chats()[0]
    assert chat["title"].startswith("gone_away")


# ----------------------------------------------------- run history for an agent


def test_a_run_digest_is_names_and_times_not_columns():
    row = {"workflow": "url_digest", "workflow_id": "id-1", "status": "running", "trigger": "manual"}
    info = {"status": "COMPLETED", "start_time": "2026-01-01T00:00:00", "close_time": "2026-01-01T00:01:00"}
    assert runs_service.digest(row, info) == {
        "workflow": "url_digest",
        "workflow_id": "id-1",
        "status": "completed",
        "trigger": "manual",
        "started": "2026-01-01T00:00:00",
        "closed": "2026-01-01T00:01:00",
    }


def test_a_run_digest_falls_back_to_what_the_app_recorded():
    row = {"workflow_id": "id-1", "status": "TERMINATED", "created_at": 1_767_225_600}
    digest = runs_service.digest(row, None)
    assert digest["status"] == "terminated"
    assert digest["started"] == "2026-01-01T00:00:00+00:00"
    assert digest["closed"] is None
