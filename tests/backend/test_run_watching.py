"""Following a run to its end, including across a restart of this process."""

from __future__ import annotations

import asyncio

import pytest
from nautionette_backend import background
from nautionette_backend import runs as runs_service
from temporalio.client import WorkflowFailureError
from temporalio.exceptions import (
    ActivityError,
    ApplicationError,
    CancelledError,
    RetryState,
    TerminatedError,
    TimeoutType,
)
from temporalio.exceptions import (
    TimeoutError as TemporalTimeoutError,
)


@pytest.fixture(autouse=True)
def instant_polling(monkeypatch: pytest.MonkeyPatch):
    """The loop is the thing under test, not how long it sleeps between polls."""
    monkeypatch.setattr(runs_service, "POLL_FLOOR_SECONDS", 0.0)
    monkeypatch.setattr(runs_service, "POLL_CEILING_SECONDS", 0.0)


@pytest.fixture
def digest(backend):
    backend.authoring.add_workflow("url_digest", manifest={"schema": 1, "name": "url_digest"})
    return backend


async def started(client, digest) -> str:
    workflow_id = client.post("/api/workflows/url_digest/run", json={}).json()["workflow_id"]
    # The request's own watcher belongs to a loop that is already gone.
    background._running.clear()
    return workflow_id


async def test_a_finished_run_is_recorded_and_delivered(client, digest):
    workflow_id = await started(client, digest)
    digest.temporal.executions[workflow_id]["status"] = "COMPLETED"
    digest.temporal.results[workflow_id] = {"summary": "all quiet"}

    await runs_service.watch("url_digest", workflow_id)

    run = digest.db.list_runs("url_digest")[0]
    assert run["status"] == "completed"
    assert run["result"] == {"summary": "all quiet"}
    chat_id = digest.db.workflow_settings("url_digest")["chat_id"]
    assert digest.db.list_messages(chat_id)[0]["content"] == "all quiet"


async def test_a_failed_run_is_recorded_with_no_result(client, digest):
    workflow_id = await started(client, digest)
    digest.temporal.executions[workflow_id]["status"] = "FAILED"

    await runs_service.watch("url_digest", workflow_id)

    run = digest.db.list_runs("url_digest")[0]
    assert (run["status"], run["result"]) == ("failed", None)


@pytest.mark.parametrize("mode", ["same", "new"])
async def test_failure_summary_is_recorded_and_delivered_as_markdown(client, digest, live, monkeypatch, mode):
    workflow_id = await started(client, digest)
    digest.db.set_workflow_settings("url_digest", {"chat_mode": mode})
    digest.temporal.executions[workflow_id]["status"] = "FAILED"
    summary = "# Release preparation blocked\n\nStaging is unhealthy.\n\n## Next steps\nCheck staging."
    error = WorkflowFailureError(
        cause=ApplicationError("Release blocked", {"summary": summary}, non_retryable=True)
    )

    async def result(_workflow_id, timeout):
        assert _workflow_id == workflow_id
        raise error

    monkeypatch.setattr(live.temporal, "result", result)
    await runs_service.watch("url_digest", workflow_id)

    run = digest.db.list_runs("url_digest")[0]
    assert run["status"] == "failed"
    assert run["result"] == {"summary": summary}
    message = digest.db.list_messages(digest.db.list_chats()[0]["id"])[0]
    assert summary in message["content"]
    assert "**Run failed**" in message["content"]
    assert workflow_id in message["content"]
    assert "```json" not in message["content"]
    assert message["meta"]["run"]["status"] == "failed"


@pytest.mark.parametrize(
    ("status", "cause", "expected"),
    [
        (
            "FAILED",
            ApplicationError("Configured staging SHA is missing"),
            "Configured staging SHA is missing",
        ),
        (
            "TIMED_OUT",
            TemporalTimeoutError(
                "Execution deadline exceeded", type=TimeoutType.START_TO_CLOSE, last_heartbeat_details=[]
            ),
            "Execution deadline exceeded",
        ),
        ("CANCELED", CancelledError("Canceled by request"), "Canceled by request"),
        ("TERMINATED", TerminatedError("Worker cannot load workflow"), "Worker cannot load workflow"),
    ],
)
async def test_unsuccessful_runs_show_their_actual_reason(
    client, digest, live, monkeypatch, status, cause, expected
):
    workflow_id = await started(client, digest)
    digest.temporal.executions[workflow_id]["status"] = status

    async def result(_workflow_id, timeout):
        raise WorkflowFailureError(cause=cause)

    monkeypatch.setattr(live.temporal, "result", result)
    await runs_service.watch("url_digest", workflow_id)
    run = digest.db.list_runs("url_digest")[0]
    assert run["status"] == status.lower()
    assert expected in run["result"]["summary"]
    chat_id = digest.db.workflow_settings("url_digest")["chat_id"]
    assert expected in digest.db.list_messages(chat_id)[0]["content"]


def test_activity_failure_includes_the_nested_cause_without_arbitrary_details():
    cause = ActivityError(
        "Activity task failed",
        scheduled_event_id=1,
        started_event_id=2,
        identity="worker",
        activity_type="mcp_call",
        activity_id="1",
        retry_state=RetryState.MAXIMUM_ATTEMPTS_REACHED,
    )
    cause.__cause__ = ApplicationError("Coolify read failed: HTTP 503", {"private_payload": "not for chat"})
    summary = runs_service.failure_summary(WorkflowFailureError(cause=cause))
    assert "Activity task failed" in summary
    assert "Coolify read failed: HTTP 503" in summary
    assert "not for chat" not in summary


def test_nested_authored_summary_wins_over_generic_wrappers():
    cause = ApplicationError("Wrapper", {"summary": ""})
    cause.__cause__ = ApplicationError("Blocker", {"summary": "# Useful next steps"})
    assert runs_service.failure_summary(WorkflowFailureError(cause=cause)) == "# Useful next steps"


def test_failure_cause_cycles_do_not_hang_delivery():
    cause = ApplicationError("Blocked")
    cause.__cause__ = cause
    assert runs_service.failure_summary(WorkflowFailureError(cause=cause)) == "Blocked"


async def test_a_temporal_that_cannot_answer_is_waited_out_not_given_up_on(client, digest, live, monkeypatch):
    workflow_id = await started(client, digest)
    answers = ["unreachable", "unreachable", {"status": "COMPLETED"}]

    async def describe(_workflow_id):
        answer = answers.pop(0)
        if answer == "unreachable":
            raise RuntimeError("temporal is not up yet")
        return {**digest.temporal.executions[workflow_id], **answer}

    monkeypatch.setattr(live.temporal, "describe", describe)
    await runs_service.watch("url_digest", workflow_id)
    assert digest.db.list_runs("url_digest")[0]["status"] == "completed"


async def test_watching_gives_up_rather_than_polling_for_ever(client, digest, monkeypatch):
    workflow_id = await started(client, digest)
    monkeypatch.setattr(runs_service, "WATCH_CEILING_SECONDS", 0.0)
    await runs_service.watch("url_digest", workflow_id)
    assert digest.db.list_runs("url_digest")[0]["status"] == "running"


async def test_a_restart_picks_up_the_runs_it_left_in_flight(client, digest):
    workflow_id = await started(client, digest)
    assert digest.db.unfinished_runs() == [{"workflow": "url_digest", "workflow_id": workflow_id}]

    digest.temporal.executions[workflow_id]["status"] = "COMPLETED"
    digest.temporal.results[workflow_id] = "done"
    runs_service.resume_unfinished()
    await asyncio.gather(*background._running)

    assert digest.db.list_runs("url_digest")[0]["status"] == "completed"
    assert digest.db.unfinished_runs() == []


async def test_a_run_that_already_ended_is_not_resumed(client, digest):
    workflow_id = await started(client, digest)
    digest.db.update_run(workflow_id, "completed", "done")
    runs_service.resume_unfinished()
    assert background._running == set()


# ------------------------------------------------------------------ background


async def test_a_background_task_is_held_until_it_finishes():
    started_flag = asyncio.Event()

    async def work():
        started_flag.set()
        await asyncio.sleep(0)

    task = background.spawn(work(), name="test-work")
    assert task in background._running
    await task
    await asyncio.sleep(0)
    assert started_flag.is_set()
    assert task not in background._running


async def test_a_background_task_that_fails_is_reported_not_swallowed(caplog):
    async def explode():
        raise RuntimeError("no space left on device")

    task = background.spawn(explode(), name="test-explode")
    with pytest.raises(RuntimeError):
        await task
    await asyncio.sleep(0)
    assert "no space left on device" in caplog.text
    assert background._running == set()
