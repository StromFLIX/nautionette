"""Private, side-effect-free deployment checks, executed in the existing worker image.

No application activity queues are polled. Namespace/schedule/history identities stay
in the root-only host run directory, never in CI logs or public artifacts.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import sys
from datetime import timedelta
from pathlib import Path

from temporalio import activity, workflow
from temporalio.api.workflowservice.v1 import GetClusterInfoRequest, ListNamespacesRequest
from temporalio.client import Client, Schedule, ScheduleActionStartWorkflow, ScheduleSpec, ScheduleState
from temporalio.worker import Replayer, UnsandboxedWorkflowRunner, Worker

STATE = Path("/migration/probe.json")


@activity.defn
async def migration_echo() -> str:
    return "ok"


@workflow.defn(name="NautionetteTemporalDeploymentProbe")
class MigrationProbe:
    def __init__(self):
        self.released = False
        self.ready = False

    @workflow.run
    async def run(self, wait: bool) -> str:
        result = await workflow.execute_activity(migration_echo, start_to_close_timeout=timedelta(seconds=30))
        self.ready = True
        if wait:
            await workflow.wait_condition(lambda: self.released)
        return result

    @workflow.signal
    def release(self):
        self.released = True

    @workflow.query
    def checkpoint(self) -> bool:
        return self.ready


def save(state: dict):
    temporary = STATE.with_suffix(".tmp")
    temporary.write_text(json.dumps(state))
    temporary.replace(STATE)
    os.sync()


def fingerprints(history) -> list[str]:
    return [
        hashlib.sha256(event.SerializeToString(deterministic=True)).hexdigest() for event in history.events
    ]


async def namespaces(client):
    result = {}
    token = b""
    while True:
        page = await client.workflow_service.list_namespaces(
            ListNamespacesRequest(page_size=100, next_page_token=token)
        )
        for item in page.namespaces:
            if item.namespace_info.name != "temporal-system":
                result[item.namespace_info.name] = {
                    "id": item.namespace_info.id,
                    "retention": item.config.workflow_execution_retention_ttl.seconds,
                }
        token = page.next_page_token
        if not token:
            return result


async def probe(mode: str):
    address, namespace = os.environ["TEMPORAL_ADDRESS"], os.environ["TEMPORAL_NAMESPACE"]
    client = await Client.connect(address, namespace=namespace)
    cluster = await client.workflow_service.get_cluster_info(GetClusterInfoRequest())
    cluster_identity = {"id": cluster.cluster_id, "shards": cluster.history_shard_count}
    if mode == "capture":
        if cluster.history_shard_count != int(os.environ["PROBE_SHARDS"]):
            raise RuntimeError("Persisted cluster shard count differs from the deployment configuration")
        if STATE.exists():
            raise RuntimeError("Refusing to overwrite migration evidence")
        state = {
            "namespaces": await namespaces(client),
            "schedules": {},
            "histories": [],
            "queue": os.environ["PROBE_ID"],
            "cluster": cluster_identity,
        }
        if namespace not in state["namespaces"]:
            raise RuntimeError("Configured namespace is missing")
        for name in state["namespaces"]:
            scoped = await Client.connect(address, namespace=name)
            state["schedules"][name] = {}
            async for row in scoped.list_schedules():
                description = await scoped.get_schedule_handle(row.id).describe()
                state["schedules"][name][row.id] = description.schedule.state.paused
            async for row in scoped.list_workflows('ExecutionStatus = "Running"'):
                if row.workflow_type.startswith("temporal-sys-"):
                    continue
                history = await scoped.get_workflow_handle(row.id, run_id=row.run_id).fetch_history()
                state["histories"].append(
                    {"namespace": name, "id": row.id, "run_id": row.run_id, "events": fingerprints(history)}
                )
        # Persist original schedule states BEFORE the first pause. Resume is idempotent.
        save(state)
        for name, schedules in state["schedules"].items():
            scoped = await Client.connect(address, namespace=name)
            for id, paused in schedules.items():
                if not paused:
                    await scoped.get_schedule_handle(id).pause(note="Temporal deployment maintenance")
        async with Worker(
            client,
            task_queue=state["queue"],
            workflows=[MigrationProbe],
            activities=[migration_echo],
            workflow_runner=UnsandboxedWorkflowRunner(),
        ):
            handle = await client.start_workflow(
                MigrationProbe.run, True, id=state["queue"], task_queue=state["queue"]
            )
            async with asyncio.timeout(90):
                while not await handle.query(MigrationProbe.checkpoint):
                    await asyncio.sleep(0.5)
        await client.create_schedule(
            state["queue"],
            Schedule(
                action=ScheduleActionStartWorkflow(
                    MigrationProbe.run, False, id=state["queue"] + "-scheduled", task_queue=state["queue"]
                ),
                spec=ScheduleSpec(),
                state=ScheduleState(paused=True),
            ),
        )
        return

    state = json.loads(STATE.read_text())
    if mode in {"check", "finish"}:
        if cluster_identity != state["cluster"]:
            raise RuntimeError("Cluster identity/history shard count changed")
        if await namespaces(client) != state["namespaces"]:
            raise RuntimeError("Namespace identity/retention changed")
        for item in state["histories"]:
            scoped = await Client.connect(address, namespace=item["namespace"])
            history = await scoped.get_workflow_handle(item["id"], run_id=item["run_id"]).fetch_history()
            if fingerprints(history)[: len(item["events"])] != item["events"]:
                raise RuntimeError("Retained workflow history changed or disappeared")
        for name, schedules in state["schedules"].items():
            scoped = await Client.connect(address, namespace=name)
            actual = {row.id async for row in scoped.list_schedules()}
            if actual - {state["queue"]} != set(schedules):
                raise RuntimeError("Schedule identities changed")
            for id in schedules:
                if not (await scoped.get_schedule_handle(id).describe()).schedule.state.paused:
                    raise RuntimeError("A schedule resumed during maintenance")
        history = await client.get_workflow_handle(state["queue"]).fetch_history()
        await Replayer(
            workflows=[MigrationProbe], workflow_runner=UnsandboxedWorkflowRunner()
        ).replay_workflow(history)
        async with Worker(
            client,
            task_queue=state["queue"],
            workflows=[MigrationProbe],
            activities=[migration_echo],
            workflow_runner=UnsandboxedWorkflowRunner(),
        ):
            if not await client.get_workflow_handle(state["queue"]).query(MigrationProbe.checkpoint):
                raise RuntimeError("Suspended workflow did not resume")
            result = await client.execute_workflow(
                MigrationProbe.run,
                False,
                id=state["queue"] + "-" + os.environ["PROBE_VERSION"],
                task_queue=state["queue"],
                execution_timeout=timedelta(seconds=90),
            )
            if result != "ok":
                raise RuntimeError("Synthetic activity failed")
            if mode == "finish":
                handle = client.get_workflow_handle(state["queue"])
                await handle.signal(MigrationProbe.release)
                if await asyncio.wait_for(handle.result(), 90) != "ok":
                    raise RuntimeError("Durable signal/resumption failed")
                schedule = client.get_schedule_handle(state["queue"])
                await schedule.trigger()
                async with asyncio.timeout(90):
                    while not (await schedule.describe()).info.recent_actions:
                        await asyncio.sleep(1)
                    id = (await schedule.describe()).info.recent_actions[-1].action.workflow_id
                    if await client.get_workflow_handle(id).result() != "ok":
                        raise RuntimeError("Schedule dispatch failed")
                await schedule.delete()
        return
    if mode == "resume":
        for name, schedules in state["schedules"].items():
            scoped = await Client.connect(address, namespace=name)
            for id, paused in schedules.items():
                if not paused:
                    await scoped.get_schedule_handle(id).unpause(note="Temporal deployment verified")
        return
    raise ValueError("Unknown probe operation")


if __name__ == "__main__":
    asyncio.run(probe(sys.argv[1]))
