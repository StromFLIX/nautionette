"""Temporal: start runs, read history, attach schedules."""

from __future__ import annotations

import asyncio
import json
from datetime import timedelta
from typing import Any

from temporalio.client import (
    Client,
    Schedule,
    ScheduleActionStartWorkflow,
    ScheduleSpec,
    ScheduleState,
)

from ..config import settings

# The events that explain a run. The rest is Temporal's own bookkeeping.
_HISTORY_EVENTS = {
    "workflow_execution_started_event_attributes": "workflow.started",
    "workflow_execution_completed_event_attributes": "workflow.completed",
    "workflow_execution_failed_event_attributes": "workflow.failed",
    "workflow_execution_timed_out_event_attributes": "workflow.timed_out",
    "workflow_execution_terminated_event_attributes": "workflow.terminated",
    "workflow_execution_canceled_event_attributes": "workflow.canceled",
    "workflow_task_failed_event_attributes": "workflow.task_failed",
    "activity_task_scheduled_event_attributes": "activity.scheduled",
    "activity_task_completed_event_attributes": "activity.completed",
    "activity_task_failed_event_attributes": "activity.failed",
    "activity_task_timed_out_event_attributes": "activity.timed_out",
    "timer_started_event_attributes": "timer.started",
    "timer_fired_event_attributes": "timer.fired",
}

# Whoever reads a history pays for every character of it.
_MAX_VALUE_CHARS = 2_000


def _clip(value: Any) -> Any:
    text = value if isinstance(value, str) else json.dumps(value, default=str)
    if len(text) <= _MAX_VALUE_CHARS:
        return value
    return f"{text[:_MAX_VALUE_CHARS]}… (truncated from {len(text)} characters)"


def _failure(failure: Any) -> dict[str, Any]:
    """Temporal nests the real cause; flatten enough of it to act on."""
    out: dict[str, Any] = {"message": failure.message}
    if failure.WhichOneof("failure_info") == "application_failure_info":
        if kind := failure.application_failure_info.type:
            out["type"] = kind
    if failure.stack_trace:
        out["stack"] = failure.stack_trace[-1_200:]
    if failure.HasField("cause"):
        out["cause"] = _failure(failure.cause)
    return out


class TemporalGateway:
    def __init__(self) -> None:
        self._client: Client | None = None
        self._lock = asyncio.Lock()
        self.last_error: str | None = None

    async def client(self) -> Client:
        async with self._lock:
            if self._client is None:
                self._client = await Client.connect(
                    settings.temporal_address, namespace=settings.temporal_namespace
                )
                self.last_error = None
            return self._client

    async def healthy(self) -> bool:
        try:
            client = await self.client()
            await client.service_client.check_health()
            return True
        except Exception as exc:  # noqa: BLE001 - health must never raise
            self.last_error = str(exc)
            self._client = None
            return False

    async def start(
        self, workflow: str, workflow_id: str, payload: dict[str, Any], timeout_minutes: int = 30
    ) -> dict[str, Any]:
        client = await self.client()
        handle = await client.start_workflow(
            workflow,
            payload,
            id=workflow_id,
            task_queue=settings.temporal_task_queue,
            execution_timeout=timedelta(minutes=timeout_minutes),
        )
        return {"workflow_id": handle.id, "run_id": handle.result_run_id}

    async def result(self, workflow_id: str, timeout: float = 5.0) -> Any:
        client = await self.client()
        handle = client.get_workflow_handle(workflow_id)
        return await asyncio.wait_for(handle.result(), timeout=timeout)

    async def describe(self, workflow_id: str) -> dict[str, Any]:
        client = await self.client()
        handle = client.get_workflow_handle(workflow_id)
        info = await handle.describe()
        return {
            "workflow_id": info.id,
            "run_id": info.run_id,
            "workflow_type": info.workflow_type,
            "status": info.status.name if info.status else "UNKNOWN",
            "start_time": info.start_time.isoformat() if info.start_time else None,
            "close_time": info.close_time.isoformat() if info.close_time else None,
        }

    async def recent(self, limit: int = 50) -> list[dict[str, Any]]:
        client = await self.client()
        out: list[dict[str, Any]] = []
        async for execution in client.list_workflows(page_size=min(limit, 100)):
            out.append(
                {
                    "workflow_id": execution.id,
                    "run_id": execution.run_id,
                    "workflow_type": execution.workflow_type,
                    "status": execution.status.name if execution.status else "UNKNOWN",
                    "start_time": execution.start_time.isoformat() if execution.start_time else None,
                    "close_time": execution.close_time.isoformat() if execution.close_time else None,
                }
            )
            if len(out) >= limit:
                break
        return out

    async def history(self, workflow_id: str, limit: int = 200) -> list[dict[str, Any]]:
        """One run's timeline: what each step was given, and what came back."""
        client = await self.client()
        handle = client.get_workflow_handle(workflow_id)

        async def decode(payloads: Any) -> Any:
            if payloads is None or not getattr(payloads, "payloads", None):
                return None
            try:
                values = await client.data_converter.decode(list(payloads.payloads))
            except Exception:  # noqa: BLE001 - one opaque payload must not hide the rest
                return "<undecodable>"
            return _clip(values[0] if len(values) == 1 else values)

        activities: dict[int, str] = {}
        out: list[dict[str, Any]] = []
        async for event in handle.fetch_history_events():
            field = event.WhichOneof("attributes") or ""
            label = _HISTORY_EVENTS.get(field)
            if not label:
                continue
            body = getattr(event, field)
            if field == "activity_task_scheduled_event_attributes":
                activities[event.event_id] = body.activity_type.name
            entry: dict[str, Any] = {
                "id": event.event_id,
                "at": event.event_time.ToDatetime().isoformat(timespec="seconds") + "Z",
                "event": label,
            }
            # Every later activity event points back at the one that scheduled it.
            if activity := activities.get(getattr(body, "scheduled_event_id", 0) or event.event_id):
                entry["activity"] = activity
            for key in ("input", "result", "details"):
                if (value := await decode(getattr(body, key, None))) is not None:
                    entry[key] = value
            if reason := getattr(body, "reason", ""):
                entry["reason"] = reason
            if "failure" in body.DESCRIPTOR.fields_by_name and body.HasField("failure"):
                entry["error"] = _failure(body.failure)
            out.append(entry)
            if len(out) >= limit:
                break
        return out

    async def cancel(self, workflow_id: str) -> None:
        client = await self.client()
        await client.get_workflow_handle(workflow_id).cancel()

    async def terminate(self, workflow_id: str, reason: str = "terminated from the app") -> None:
        """Stop a run without asking it to agree.

        Cancellation is delivered to the workflow, so a run whose file has been
        deleted or whose worker cannot load it will never act on one.
        """
        client = await self.client()
        await client.get_workflow_handle(workflow_id).terminate(reason)

    # --------------------------------------------------------------- schedules

    def _schedule_id(self, workflow: str) -> str:
        return f"schedule-{workflow}"

    async def set_schedule(
        self, workflow: str, cron: str, payload: dict[str, Any], paused: bool = False
    ) -> dict[str, Any]:
        client = await self.client()
        schedule = Schedule(
            action=ScheduleActionStartWorkflow(
                workflow,
                payload,
                id=f"{workflow}-scheduled",
                task_queue=settings.temporal_task_queue,
            ),
            spec=ScheduleSpec(cron_expressions=[cron]),
            state=ScheduleState(paused=paused),
        )
        schedule_id = self._schedule_id(workflow)
        try:
            await client.create_schedule(schedule_id, schedule)
        except Exception:  # already exists -> replace it
            handle = client.get_schedule_handle(schedule_id)
            await handle.delete()
            await client.create_schedule(schedule_id, schedule)
        return {"schedule_id": schedule_id, "cron": cron, "paused": paused}

    async def delete_schedule(self, workflow: str) -> None:
        client = await self.client()
        await client.get_schedule_handle(self._schedule_id(workflow)).delete()

    async def schedules(self) -> list[dict[str, Any]]:
        client = await self.client()
        out: list[dict[str, Any]] = []
        async for item in await client.list_schedules():
            listed = getattr(item, "schedule", None)
            spec = getattr(listed, "spec", None)
            state = getattr(listed, "state", None)
            out.append(
                {
                    "id": item.id,
                    "workflow": item.id.removeprefix("schedule-"),
                    "cron": _cron_of(spec),
                    "paused": bool(state is not None and getattr(state, "paused", False)),
                }
            )
        return out


_CRON_FIELD_SPANS = {
    "minute": (0, 59),
    "hour": (0, 23),
    "day_of_month": (1, 31),
    "month": (1, 12),
    "day_of_week": (0, 6),
}


def _cron_field(ranges: Any, field: str) -> str:
    """Render one calendar field back as a cron field.

    Temporal rewrites a cron expression into structured calendars, so reading a
    schedule back never returns the string that created it.
    """
    low, high = _CRON_FIELD_SPANS[field]
    if not ranges:
        return "*"
    parts: list[str] = []
    for entry in ranges:
        start = getattr(entry, "start", 0)
        end = getattr(entry, "end", start)
        step = getattr(entry, "step", 1) or 1
        covers_field = start <= low and end >= high
        if covers_field and step == 1:
            return "*"
        if covers_field:
            parts.append(f"*/{step}")
        elif step != 1:
            parts.append(f"{start}-{end}/{step}")
        elif end != start:
            parts.append(f"{start}-{end}")
        else:
            parts.append(str(start))
    return ",".join(parts) or "*"


def _cron_of(spec: Any) -> str | None:
    if spec is None:
        return None
    expressions = getattr(spec, "cron_expressions", None)
    if expressions:
        return expressions[0]
    calendars = getattr(spec, "calendars", None)
    if not calendars:
        return None
    calendar = calendars[0]
    return " ".join(
        _cron_field(getattr(calendar, field, None), field)
        for field in ("minute", "hour", "day_of_month", "month", "day_of_week")
    )


temporal = TemporalGateway()
