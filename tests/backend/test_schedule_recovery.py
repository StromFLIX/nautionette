"""Schedule actions bypass the HTTP start route; discover and deliver them anyway."""

import asyncio

import pytest
from nautionette_backend import background, runs
from nautionette_backend.schedules import DailySchedule, temporal_spec


@pytest.fixture(autouse=True)
def polling(monkeypatch):
    monkeypatch.setattr(runs, "POLL_FLOOR_SECONDS", 0)
    monkeypatch.setattr(runs, "POLL_CEILING_SECONDS", 0)


def scheduled(backend, workflow_id="digest-scheduled-2026-09-07", status="COMPLETED"):
    backend.temporal.executions[workflow_id] = {
        "workflow_id": workflow_id,
        "run_id": "run-1",
        "workflow_type": "digest",
        "status": status,
        "scheduled": True,
    }
    backend.temporal.histories[workflow_id] = [{"input": {"text": "x" * 5000}}]
    backend.temporal.results[workflow_id] = {"summary": "Actual result"}
    return workflow_id


async def settle():
    await asyncio.gather(*background._running)
    await asyncio.sleep(0)


async def test_closed_run_recovers_full_input_start_time_and_chat_once(backend):
    workflow_id = scheduled(backend)
    # No live workflow/schedule is needed to recover a retained execution.
    await runs.discover_scheduled_runs()
    await runs.discover_scheduled_runs()
    assert len(background._running) == 1
    await settle()
    await runs.discover_scheduled_runs()
    row = backend.db.list_runs()[0]
    assert row["workflow_id"] == workflow_id
    assert row["trigger"] == "schedule"
    assert row["created_at"] == 1788782400.0
    assert row["input"] == {"text": "x" * 5000}
    assert row["status"] == "completed"
    chat = backend.db.workflow_settings("digest")["chat_id"]
    assert [m["content"] for m in backend.db.list_messages(chat)] == ["Actual result"]
    assert not background._running


async def test_running_schedule_is_followed_until_completion(backend):
    workflow_id = scheduled(backend, status="RUNNING")
    await runs.discover_scheduled_runs()
    await asyncio.sleep(0)
    assert backend.db.list_runs()[0]["status"] == "running"
    backend.temporal.executions[workflow_id]["status"] = "FAILED"
    await settle()
    assert backend.db.list_runs()[0]["status"] == "failed"


async def test_unreadable_input_is_retried_without_blocking_other_runs(backend):
    broken = scheduled(backend, "broken")
    backend.temporal.histories.pop(broken)
    scheduled(backend, "healthy")
    await runs.discover_scheduled_runs()
    await settle()
    assert runs.stored_run(broken) is None
    assert runs.stored_run("healthy")["status"] == "completed"
    backend.temporal.histories[broken] = [{"input": {"retry": True}}]
    await runs.discover_scheduled_runs()
    await settle()
    assert runs.stored_run(broken)["input"] == {"retry": True}


async def test_discovery_is_not_capped_at_the_recent_runs_page(backend):
    for i in range(125):
        scheduled(backend, f"scheduled-{i}")
    await runs.discover_scheduled_runs()
    await settle()
    assert len(backend.db.list_runs(limit=200)) == 125


async def test_restart_and_discovery_share_a_watcher(backend):
    workflow_id = scheduled(backend)
    backend.db.record_run("digest", workflow_id, "run-1", "schedule", {})
    runs.resume_unfinished()
    await runs.discover_scheduled_runs()
    assert len(background._running) == 1
    await settle()


def test_duplicate_record_does_not_overwrite_a_finished_run(db):
    db.record_run("digest", "id", "run-1", "schedule", {"saved": True}, created_at=123)
    db.update_run("id", "completed", "done")
    db.record_run("digest", "id", "run-1", "schedule", {})
    row = db.list_runs()[0]
    assert row["status"] == "completed"
    assert row["input"] == {"saved": True}
    assert row["result"] == "done"
    assert row["created_at"] == 123


async def test_existing_schedule_timeouts_follow_the_manifest(backend):
    backend.authoring.add_workflow("digest", manifest={"timeout_minutes": 17})
    spec = temporal_spec(DailySchedule(frequency="daily", at="07:30", timezone="Europe/Berlin"))
    await backend.temporal.set_schedule("deleted", spec, {})
    await backend.temporal.set_schedule("digest", spec, {"days": 7}, paused=True)
    await runs.reconcile_schedule_timeouts()
    saved = backend.temporal.schedule_specs["digest"]
    assert saved == {"spec": spec, "input": {"days": 7}, "paused": True, "timeout_minutes": 17}


async def test_reconciliation_retries_after_outage_and_cancels_cleanly(backend, monkeypatch):
    attempts = 0
    ready = asyncio.Event()

    async def discover():
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise RuntimeError("Temporal starting")
        ready.set()

    monkeypatch.setattr(runs, "discover_scheduled_runs", discover)
    monkeypatch.setattr(runs, "RECONCILE_SECONDS", 0)
    task = asyncio.create_task(runs.reconcile_schedules())
    await asyncio.wait_for(ready.wait(), timeout=1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert attempts >= 2
