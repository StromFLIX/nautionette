"""Opt-in real Temporal test: NAUTIONETTE_TEMPORAL_TEST=1 uv run pytest -k real_schedule.

The SDK downloads a local dev server. No production services or models are used.
"""

import asyncio
import os
from datetime import timedelta

import pytest
from nautionette_backend import background, runs
from nautionette_backend.clients.temporal_server import TemporalGateway
from nautionette_backend.config import settings
from temporalio import workflow
from temporalio.client import ScheduleIntervalSpec, ScheduleSpec
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import UnsandboxedWorkflowRunner, Worker


@workflow.defn(name="schedule_probe")
class ScheduleProbe:
    @workflow.run
    async def run(self, payload: dict) -> dict:
        return {"summary": f"Received {len(payload['text'])} characters"}


@pytest.mark.skipif(os.environ.get("NAUTIONETTE_TEMPORAL_TEST") != "1", reason="opt-in local Temporal")
async def test_real_schedule_is_discovered_and_delivered(backend, monkeypatch):
    # Minimal agent containers may not export USER; the Go dev server requires it.
    monkeypatch.setenv("USER", os.environ.get("USER") or "temporal-test")
    async with await WorkflowEnvironment.start_local() as env:
        gateway = TemporalGateway()
        gateway._client = env.client
        monkeypatch.setattr(runs, "temporal", gateway)
        monkeypatch.setattr(runs, "POLL_FLOOR_SECONDS", 0.05)
        monkeypatch.setattr(runs, "POLL_CEILING_SECONDS", 0.05)
        async with Worker(
            env.client, task_queue=settings.temporal_task_queue,
            workflows=[ScheduleProbe], workflow_runner=UnsandboxedWorkflowRunner(),
        ):
            payload = {"text": "x" * 5000}
            await gateway.set_schedule(
                "schedule_probe", ScheduleSpec(intervals=[ScheduleIntervalSpec(every=timedelta(seconds=1))]),
                payload, timeout_minutes=2,
            )
            handle = env.client.get_schedule_handle("schedule-schedule_probe")
            try:
                async with asyncio.timeout(45):
                    while not backend.db.list_runs():
                        await runs.discover_scheduled_runs()
                        await asyncio.sleep(0.1)
                    await handle.pause()
                    await asyncio.gather(*background._running)
                row = backend.db.list_runs()[0]
                assert row["trigger"] == "schedule"
                assert row["input"] == payload
                assert row["status"] == "completed"
                assert row["result"] == {"summary": "Received 5000 characters"}
                history = await env.client.get_workflow_handle(row["workflow_id"]).fetch_history()
                started = history.events[0].workflow_execution_started_event_attributes
                assert started.workflow_execution_timeout.ToTimedelta() == timedelta(minutes=2)
                chat = backend.db.workflow_settings("schedule_probe")["chat_id"]
                assert backend.db.list_messages(chat)[0]["content"] == "Received 5000 characters"
                # Real SDK updates decode/encode args differently from the test fake.
                before = await handle.describe()
                await gateway.ensure_schedule_timeout("schedule_probe", 3)
                after = await handle.describe()
                assert after.schedule.state.paused
                assert after.schedule.spec == before.schedule.spec
                assert (await gateway.schedule("schedule_probe"))["input"] == payload
                assert after.schedule.action.execution_timeout == timedelta(minutes=3)
            finally:
                await handle.delete()
                await background.drain()
