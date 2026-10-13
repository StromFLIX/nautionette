"""App and agent deletion must not leave Temporal firing a missing definition."""

import pytest
from nautionette_backend import runs
from nautionette_backend.schedules import DailySchedule, temporal_spec


@pytest.mark.parametrize("agent", [False, True])
def test_deletion_cleans_schedule_settings_and_reloads_without_losing_history(
    client, backend, internal_headers, agent
):
    name = "digest"
    backend.authoring.add_workflow(name)
    client.post(
        f"/api/workflows/{name}/schedule",
        json={"frequency": "daily", "at": "08:00", "timezone": "UTC"},
    )
    chat = backend.db.create_chat("Saved output", "default")
    backend.db.add_message(chat["id"], "assistant", "Keep this result")
    backend.db.set_workflow_settings(name, {"chat_id": chat["id"]})
    backend.db.record_run(name, "finished-run", "run-1", "schedule", {})
    backend.db.update_run("finished-run", "completed", "Keep this result")
    response = (
        client.post(f"/internal/workflows/{name}/delete", headers=internal_headers)
        if agent
        else client.delete(f"/api/workflows/{name}")
    )
    assert response.json() == {"name": name, "deleted": True}
    assert name not in backend.authoring.workflows
    assert backend.temporal.schedule_specs == {}
    assert backend.db.query("SELECT * FROM workflow_settings") == []
    assert backend.broker.restarts == 1
    assert backend.db.list_runs(name)[0]["result"] == "Keep this result"
    assert backend.db.list_messages(chat["id"])[0]["content"] == "Keep this result"


def test_deletion_stops_the_schedule_before_removing_the_definition(client, backend, live, monkeypatch):
    backend.authoring.add_workflow("digest")
    backend.db.set_workflow_settings("digest", {"disabled": True})

    async def unavailable(name):
        assert name in backend.authoring.workflows
        raise RuntimeError("Temporal unavailable")

    monkeypatch.setattr(live.temporal, "delete_schedule", unavailable)
    response = client.delete("/api/workflows/digest")
    assert response.status_code == 503
    assert "workflow was not deleted" in response.json()["detail"]
    assert "digest" in backend.authoring.workflows
    assert backend.db.workflow_settings("digest")["disabled"]
    assert backend.broker.restarts == 0


async def test_orphan_schedule_is_removed_but_other_schedules_are_preserved(backend):
    backend.authoring.add_workflow("digest", manifest={"timeout_minutes": 17})
    spec = temporal_spec(DailySchedule(frequency="daily", at="08:00", timezone="UTC"))
    await backend.temporal.set_schedule("deleted", spec, {})
    await backend.temporal.set_schedule("digest", spec, {"days": 7}, paused=True)
    await runs.reconcile_schedule_timeouts()
    assert "deleted" not in backend.temporal.schedule_specs
    assert backend.temporal.schedule_specs["digest"] == {
        "spec": spec,
        "input": {"days": 7},
        "paused": True,
        "timeout_minutes": 17,
    }


async def test_authoring_outage_does_not_prove_a_workflow_was_deleted(backend, live, monkeypatch):
    spec = temporal_spec(DailySchedule(frequency="daily", at="08:00", timezone="UTC"))
    await backend.temporal.set_schedule("digest", spec, {})

    async def unavailable():
        raise RuntimeError("authoring unavailable")

    monkeypatch.setattr(live.authoring, "list_workflows", unavailable)
    with pytest.raises(RuntimeError, match="authoring unavailable"):
        await runs.reconcile_schedule_timeouts()
    assert "digest" in backend.temporal.schedule_specs


async def test_unmanaged_schedules_are_not_cleaned_up(backend, live, monkeypatch):
    removed = []

    async def schedules():
        return [{"id": "external-schedule", "workflow": "external"}]

    async def delete_schedule(name):
        removed.append(name)

    monkeypatch.setattr(live.temporal, "schedules", schedules)
    monkeypatch.setattr(live.temporal, "delete_schedule", delete_schedule)
    await runs.reconcile_schedule_timeouts()
    assert removed == []
