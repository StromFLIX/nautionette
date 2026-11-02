"""Expired history is not a failed run, and an outage is not expired history."""

import asyncio
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from nautionette_backend import runs
from nautionette_backend.clients.temporal_server import TemporalGateway
from nautionette_backend.config import Settings, settings
from temporalio.api.namespace.v1 import NamespaceConfig
from temporalio.api.workflowservice.v1 import DescribeNamespaceResponse
from temporalio.service import RPCError, RPCStatusCode


@pytest.fixture
def expired(db):
    db.record_run("digest", "old-run", "run-1", "schedule", {"days": 3}, created_at=1767225600)
    db.update_run("old-run", "completed", {"summary": "Saved output"})
    return "old-run"


def test_expired_run_keeps_its_status_input_and_output(client, expired):
    response = client.get(f"/api/runs/{expired}")
    assert response.status_code == 200
    detail = response.json()
    assert detail["temporal"] is None
    assert detail["history_status"] == "expired"
    assert detail["run"]["status"] == "completed"
    assert detail["run"]["input"] == {"days": 3}
    assert detail["run"]["result"] == {"summary": "Saved output"}
    assert "expired or was removed" in detail["note"]


def test_expired_graph_contains_no_invented_steps_or_times(client, expired):
    response = client.get(f"/api/runs/{expired}/graph")
    assert response.status_code == 200
    graph = response.json()
    assert graph["history_status"] == "expired"
    assert graph["status"] == "completed"
    assert graph["edges"] == []
    assert len(graph["nodes"]) == 1
    root = graph["nodes"][0]
    assert root["label"] == "digest"
    assert root["started_at"] == "2026-01-01T00:00:00+00:00"
    assert root["finished_at"] is None
    assert root["result"] == {"summary": "Saved output"}
    assert graph["warnings"] == [runs.HISTORY_EXPIRED_NOTE]


@pytest.mark.parametrize("prefix", ["/api/runs", "/internal/runs"])
def test_expired_history_is_consistent_for_users_and_agents(client, internal_headers, expired, prefix):
    suffix = "/history" if prefix == "/api/runs" else ""
    response = client.get(f"{prefix}/{expired}{suffix}", headers=internal_headers)
    assert response.status_code == 200
    detail = response.json()
    assert detail["history_status"] == "expired"
    assert detail["events"] == []
    assert detail["status"] == "completed"
    assert detail["result"] == {"summary": "Saved output"}
    assert detail["note"] == runs.HISTORY_EXPIRED_NOTE


@pytest.mark.parametrize("suffix", ["", "/graph", "/history"])
def test_unknown_runs_are_404_and_reads_still_require_auth(client, anonymous, suffix):
    assert anonymous.get(f"/api/runs/never-existed{suffix}").status_code == 401
    assert client.get(f"/api/runs/never-existed{suffix}").status_code == 404


@pytest.mark.parametrize("suffix", ["", "/graph", "/history"])
@pytest.mark.parametrize("status", [RPCStatusCode.UNAVAILABLE, RPCStatusCode.PERMISSION_DENIED])
def test_temporal_failure_is_retryable_not_mislabeled_as_expiry(
    client, live, monkeypatch, expired, suffix, status
):
    monkeypatch.setattr(live.temporal, "describe", AsyncMock(side_effect=RPCError("private", status, b"")))
    response = client.get(f"/api/runs/{expired}{suffix}")
    assert response.status_code == 503
    assert response.json() == {"detail": "Temporal is unavailable. Please retry."}


@pytest.mark.parametrize("suffix", ["/graph", "/history"])
@pytest.mark.parametrize("status", [RPCStatusCode.NOT_FOUND, RPCStatusCode.UNAVAILABLE])
def test_history_expiring_between_describe_and_fetch_is_handled(
    client, backend, live, monkeypatch, expired, suffix, status
):
    backend.temporal.executions[expired] = {
        "workflow_id": expired,
        "run_id": "specific-run",
        "status": "COMPLETED",
    }
    history = AsyncMock(side_effect=RPCError("history gone", status, b""))
    monkeypatch.setattr(live.temporal, "history", history)
    response = client.get(f"/api/runs/{expired}{suffix}")
    assert response.status_code == (200 if status == RPCStatusCode.NOT_FOUND else 503)
    if status == RPCStatusCode.NOT_FOUND:
        assert response.json()["history_status"] == "expired"
    assert history.call_args.kwargs["run_id"] == "specific-run"


async def test_retention_reconciles_existing_namespace_without_changing_other_config(monkeypatch):
    monkeypatch.setattr(settings, "temporal_namespace", "isolated-test")
    monkeypatch.setattr(settings, "temporal_history_retention_days", 7)
    config = NamespaceConfig(
        history_archival_uri="s3://existing-archive",
        history_archival_state=2,
        custom_search_attribute_aliases={"Keyword07": "CustomKeywordField"},
    )
    config.workflow_execution_retention_ttl.FromTimedelta(timedelta(days=1))
    response = DescribeNamespaceResponse(config=config)

    async def update(request):
        # Temporal treats alias entries as allocations, even if they match the read config.
        if request.config.custom_search_attribute_aliases:
            raise RPCError(
                "Custom search attribute field name already allocated.", RPCStatusCode.INVALID_ARGUMENT, b""
            )
        # UpdateNamespace patches only supplied fields; other namespace policy survives.
        response.config.workflow_execution_retention_ttl.CopyFrom(
            request.config.workflow_execution_retention_ttl
        )

    service = SimpleNamespace(
        describe_namespace=AsyncMock(return_value=response), update_namespace=AsyncMock(side_effect=update)
    )
    gateway = TemporalGateway()
    monkeypatch.setattr(gateway, "client", AsyncMock(return_value=SimpleNamespace(workflow_service=service)))
    await gateway.ensure_retention()
    assert service.describe_namespace.call_args.args[0].namespace == "isolated-test"
    request = service.update_namespace.call_args.args[0]
    assert request.namespace == "isolated-test"
    assert request.config.workflow_execution_retention_ttl.ToTimedelta() == timedelta(days=7)
    assert [field.name for field, _ in request.config.ListFields()] == ["workflow_execution_retention_ttl"]
    assert response.config.history_archival_uri == "s3://existing-archive"
    assert response.config.history_archival_state == 2
    assert dict(response.config.custom_search_attribute_aliases) == {"Keyword07": "CustomKeywordField"}
    # No extra writes once the configured policy matches.
    await gateway.ensure_retention()
    assert service.update_namespace.await_count == 1


async def test_retention_retries_after_startup_failure_without_blocking_discovery(backend, live, monkeypatch):
    ready = asyncio.Event()
    ensure = AsyncMock(side_effect=[RuntimeError("Temporal starting"), None])

    async def discover():
        if ensure.await_count >= 2:
            ready.set()

    monkeypatch.setattr(live.temporal, "ensure_retention", ensure)
    monkeypatch.setattr(runs, "discover_scheduled_runs", discover)
    monkeypatch.setattr(runs, "RECONCILE_SECONDS", 0)
    task = asyncio.create_task(runs.reconcile_schedules())
    await asyncio.wait_for(ready.wait(), timeout=1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert ensure.await_count >= 2


def test_retention_defaults_to_seven_days_and_allows_explicit_override(monkeypatch):
    monkeypatch.delenv("TEMPORAL_HISTORY_RETENTION_DAYS", raising=False)
    assert Settings().temporal_history_retention_days == 7
    monkeypatch.setenv("TEMPORAL_HISTORY_RETENTION_DAYS", "14")
    assert Settings().temporal_history_retention_days == 14
    monkeypatch.setenv("TEMPORAL_HISTORY_RETENTION_DAYS", "0")
    with pytest.raises(ValueError, match="TEMPORAL_HISTORY_RETENTION_DAYS"):
        Settings()
