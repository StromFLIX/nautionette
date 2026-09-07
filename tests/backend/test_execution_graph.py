from nautionette_backend.execution_graph import execution_graph


def test_terminal_event_wins_if_execution_finishes_after_describe():
    graph = execution_graph(
        {"workflow_id": "demo", "status": "RUNNING"},
        [
            event(1, "workflow.started"),
            event(5, "workflow.completed", result="done"),
        ],
    )
    assert graph["status"] == "completed"
    assert graph["nodes"][0]["status"] == "completed"
    assert graph["nodes"][-1]["label"] == "Completed"


def test_parent_completion_does_not_claim_an_abandoned_child_has_stopped():
    graph = execution_graph(
        {"workflow_id": "demo", "status": "COMPLETED"},
        [
            event(1, "workflow.started"),
            event(5, "child.scheduled", workflow_type="Child"),
            event(6, "child.started", initiated_event_id=5),
            event(9, "workflow.completed"),
        ],
    )
    assert graph["nodes"][1]["status"] == "unknown"


def event(event_id, kind, **fields):
    return {"id": event_id, "event": kind, "at": f"2026-09-07T12:00:{event_id:02d}Z", **fields}


def test_repeated_names_and_parallel_activity_states_are_correlated_by_id():
    graph = execution_graph(
        {"workflow_id": "demo", "status": "RUNNING"},
        [
            event(1, "workflow.started"),
            event(
                5, "activity.scheduled", activity="fetch", activity_id="a", workflow_task_completed_event_id=4
            ),
            event(
                6, "activity.scheduled", activity="fetch", activity_id="b", workflow_task_completed_event_id=4
            ),
            event(7, "activity.started", scheduled_event_id=5),
            event(8, "activity.completed", scheduled_event_id=5, result={"ok": True}),
            event(9, "activity.started", scheduled_event_id=6),
            event(12, "activity.scheduled", activity="save", workflow_task_completed_event_id=11),
        ],
    )
    nodes = {node["id"]: node for node in graph["nodes"]}
    assert nodes["event-5"]["status"] == "completed"
    assert nodes["event-6"]["status"] == "running"
    assert nodes["event-5"]["result"] == {"ok": True}
    assert [(edge["source"], edge["target"]) for edge in graph["edges"]] == [
        ("workflow", "event-5"),
        ("workflow", "event-6"),
        ("event-5", "event-12"),
    ]


def test_pending_activities_expose_retries_not_stale_scheduled_state():
    graph = execution_graph(
        {
            "workflow_id": "demo",
            "status": "RUNNING",
            "pending_activities": [
                {"activity_id": "a", "state": "scheduled", "attempt": 3, "error": {"message": "offline"}}
            ],
        },
        [event(5, "activity.scheduled", activity="fetch", activity_id="a")],
    )
    assert graph["nodes"][1]["status"] == "retrying"
    assert graph["nodes"][1]["attempt"] == 3


def test_child_timer_failure_signal_and_outcome():
    graph = execution_graph(
        {"workflow_id": "demo", "status": "FAILED"},
        [
            event(1, "workflow.started", input={"name": "test"}),
            event(5, "child.scheduled", workflow_type="Child", workflow_id="child-1"),
            event(6, "child.started", initiated_event_id=5, workflow_id="child-1", run_id="child-run"),
            event(7, "child.failed", initiated_event_id=5, error={"message": "failed"}),
            event(8, "timer.started", duration_seconds=10),
            event(9, "timer.fired", started_event_id=8),
            event(10, "signal.received", signal_name="approve"),
            event(11, "workflow.failed", error={"message": "failed"}),
        ],
    )
    assert [node["status"] for node in graph["nodes"]] == [
        "failed",
        "failed",
        "completed",
        "received",
        "failed",
    ]
    assert graph["nodes"][1]["workflow_id"] == "child-1"
    assert graph["nodes"][-1]["error"]["message"] == "failed"


def test_truncation_does_not_invent_step_outcomes():
    graph = execution_graph(
        {"workflow_id": "demo", "status": "COMPLETED"},
        [
            event(5, "activity.scheduled", activity="fetch"),
        ],
        truncated=True,
    )
    assert graph["warnings"]
    assert graph["nodes"][1]["status"] == "unknown"


def test_graph_endpoint_is_authenticated_and_reads_real_history(client, anonymous, backend):
    backend.authoring.add_workflow("demo")
    run_id = client.post("/api/workflows/demo/run", json={}).json()["workflow_id"]
    backend.temporal.histories[run_id] = [event(5, "activity.scheduled", activity="fetch")]
    assert anonymous.get(f"/api/runs/{run_id}/graph").status_code == 401
    graph = client.get(f"/api/runs/{run_id}/graph").json()
    assert graph["status"] == "running"
    assert graph["nodes"][1]["label"] == "fetch"


def test_draft_exposes_both_versions(client, backend):
    backend.authoring.add_workflow("demo")
    backend.authoring.drafts["demo"] = {"name": "demo", "code": "", "is_new": False}
    draft = client.get("/api/drafts/demo").json()
    assert draft["previous_graph"]["mode"] == "definition"
    backend.authoring.drafts["demo"]["is_new"] = True
    assert client.get("/api/drafts/demo").json()["previous_graph"] is None
