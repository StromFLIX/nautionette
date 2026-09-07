from datetime import UTC, datetime
from types import SimpleNamespace

from google.protobuf.timestamp_pb2 import Timestamp
from nautionette_backend.clients.temporal_server import TemporalGateway
from temporalio.api.common.v1 import ActivityType, Payloads, WorkflowExecution, WorkflowType
from temporalio.api.enums.v1 import PendingActivityState
from temporalio.api.history.v1 import (
    ActivityTaskCompletedEventAttributes,
    ActivityTaskScheduledEventAttributes,
    ActivityTaskStartedEventAttributes,
    ChildWorkflowExecutionStartedEventAttributes,
    HistoryEvent,
)
from temporalio.api.workflow.v1 import PendingActivityInfo
from temporalio.api.workflowservice.v1 import DescribeWorkflowExecutionResponse
from temporalio.client import WorkflowExecutionStatus
from temporalio.converter import DataConverter


async def test_sdk_history_preserves_correlations_precision_payloads_and_run_id():
    timestamp = Timestamp()
    timestamp.FromDatetime(datetime(2026, 9, 7, 12, 0, 0, 172000, tzinfo=UTC))
    payloads = Payloads(payloads=await DataConverter.default.encode([{"ok": True}]))
    events = [
        HistoryEvent(
            event_id=5,
            event_time=timestamp,
            activity_task_scheduled_event_attributes=ActivityTaskScheduledEventAttributes(
                activity_id="fetch-1",
                activity_type=ActivityType(name="fetch"),
                workflow_task_completed_event_id=4,
                input=payloads,
            ),
        ),
        HistoryEvent(
            event_id=6,
            event_time=timestamp,
            activity_task_started_event_attributes=ActivityTaskStartedEventAttributes(
                scheduled_event_id=5, attempt=2
            ),
        ),
        HistoryEvent(
            event_id=7,
            event_time=timestamp,
            activity_task_completed_event_attributes=ActivityTaskCompletedEventAttributes(
                scheduled_event_id=5, started_event_id=6, result=payloads
            ),
        ),
        HistoryEvent(
            event_id=9,
            event_time=timestamp,
            child_workflow_execution_started_event_attributes=ChildWorkflowExecutionStartedEventAttributes(
                initiated_event_id=8,
                workflow_type=WorkflowType(name="Child"),
                workflow_execution=WorkflowExecution(workflow_id="child-1", run_id="child-run"),
            ),
        ),
    ]

    async def history():
        for item in events:
            yield item

    requested = []

    def handle(workflow_id, **kwargs):
        requested.append((workflow_id, kwargs))
        return SimpleNamespace(fetch_history_events=history)

    gateway = TemporalGateway()
    gateway._client = SimpleNamespace(get_workflow_handle=handle, data_converter=DataConverter.default)
    result = await gateway.history("demo", run_id="specific-run")
    assert requested == [("demo", {"run_id": "specific-run"})]
    assert result[0]["workflow_task_completed_event_id"] == 4
    assert result[0]["input"] == {"ok": True}
    assert result[0]["at"] == "2026-09-07T12:00:00.172Z"
    assert result[1]["scheduled_event_id"] == 5
    assert result[1]["attempt"] == 2
    assert result[2]["activity"] == "fetch"
    assert result[2]["result"] == {"ok": True}
    assert result[3]["initiated_event_id"] == 8
    assert result[3]["workflow_id"] == "child-1"
    assert len(await gateway.history("demo", limit=2)) == 2


async def test_sdk_description_exposes_pending_activity_without_started_history_event():
    timestamp = Timestamp()
    timestamp.FromDatetime(datetime(2026, 9, 7, tzinfo=UTC))

    async def describe():
        return SimpleNamespace(
            id="demo",
            run_id="run-1",
            workflow_type="Demo",
            status=WorkflowExecutionStatus.RUNNING,
            start_time=datetime(2026, 9, 7, tzinfo=UTC),
            close_time=None,
            raw_description=DescribeWorkflowExecutionResponse(
                pending_activities=[
                    PendingActivityInfo(
                        activity_id="fetch-1",
                        state=PendingActivityState.PENDING_ACTIVITY_STATE_STARTED,
                        attempt=2,
                        last_started_time=timestamp,
                    ),
                ]
            ),
        )

    gateway = TemporalGateway()
    gateway._client = SimpleNamespace(get_workflow_handle=lambda _: SimpleNamespace(describe=describe))
    result = await gateway.describe("demo")
    assert result["pending_activities"][0] == {
        "activity_id": "fetch-1",
        "state": "started",
        "attempt": 2,
        "started_at": "2026-09-07T00:00:00Z",
        "error": None,
    }
