"""Disposable PostgreSQL-backed upgrade/restore rehearsal; never targets a live stack.

Run with `uv run --locked python -m scripts.check_temporal_upgrade` on a Docker host.
Tests actual 1.29 histories through every minor, not an ephemeral CLI dev server.
"""

from __future__ import annotations

# Commands below use fixed Docker verbs and disposable fixture names, never user input.
# ruff: noqa: S603, S607
import asyncio
import os
import secrets
import subprocess
import tempfile
import time
from datetime import timedelta
from pathlib import Path

from temporalio import activity, workflow
from temporalio.client import (
    Client,
    Schedule,
    ScheduleActionStartWorkflow,
    ScheduleIntervalSpec,
    ScheduleSpec,
    ScheduleState,
)
from temporalio.worker import Replayer, UnsandboxedWorkflowRunner, Worker

ROOT = Path(__file__).resolve().parents[1]
VERSIONS = ("1.30.9", "1.31.5", "1.32.2")
QUEUE = "upgrade-rehearsal"


@activity.defn
async def echo(value: str) -> str:
    return value


@workflow.defn
class UpgradeProbe:
    def __init__(self):
        self.released = False
        self.ready = False

    @workflow.run
    async def run(self, value: str) -> str:
        saved = await workflow.execute_activity(echo, value, start_to_close_timeout=timedelta(seconds=30))
        self.ready = True
        await workflow.wait_condition(lambda: self.released)
        return await workflow.execute_activity(
            echo, saved + ":resumed", start_to_close_timeout=timedelta(seconds=30)
        )

    @workflow.signal
    def release(self):
        self.released = True

    @workflow.query
    def checkpoint(self) -> bool:
        return self.ready


class Rehearsal:
    def __init__(self):
        self.prefix = "temporal-upgrade-" + secrets.token_hex(4)
        self.network = self.prefix
        self.containers: list[str] = []
        self.volumes: list[str] = []
        # Only disposable CI credentials, passed by environment, never printed.
        self.env = {**os.environ, "POSTGRES_PASSWORD": secrets.token_urlsafe(24)}
        self.env["POSTGRES_PWD"] = self.env["POSTGRES_PASSWORD"]

    def docker(self, *args: str, data: bytes | None = None) -> bytes:
        return subprocess.run(
            ["docker", *args], input=data, capture_output=True, check=True, env=self.env
        ).stdout

    def build(self, version: str, kind: str) -> str:
        tag = f"{self.prefix}/{kind}:{version}"
        dockerfile = "Dockerfile" if kind == "legacy" else f"Dockerfile.{kind}"
        # Build output contains no credentials and is useful when CI fails.
        subprocess.run(
            [
                "docker",
                "build",
                "--build-arg",
                f"TEMPORAL_VERSION={version}",
                "-t",
                tag,
                "-f",
                str(ROOT / "infra/temporal" / dockerfile),
                str(ROOT / "infra/temporal"),
            ],
            check=True,
        )
        return tag

    def postgres(self, suffix: str) -> str:
        name = f"{self.prefix}-{suffix}"
        volume = name + "-data"
        self.volumes.append(volume)
        self.containers.append(name)
        self.docker(
            "run",
            "-d",
            "--name",
            name,
            "--network",
            self.network,
            "--network-alias",
            suffix,
            "-e",
            "POSTGRES_PASSWORD",
            "-e",
            "POSTGRES_USER=temporal",
            "-e",
            "POSTGRES_DB=postgres",
            "-v",
            volume + ":/var/lib/postgresql/data",
            "postgres:17-alpine",
        )
        self.wait(lambda: self.docker("exec", name, "pg_isready", "-U", "temporal"))
        return name

    def wait(self, fn, seconds: int = 180):
        deadline = time.monotonic() + seconds
        while True:
            try:
                return fn()
            except subprocess.CalledProcessError:
                if time.monotonic() >= deadline:
                    raise
                time.sleep(2)

    def start(self, image: str, db: str = "postgres") -> tuple[str, str]:
        name = f"{self.prefix}-server-{len(self.containers)}"
        self.containers.append(name)
        self.docker(
            "run",
            "-d",
            "--name",
            name,
            "--network",
            self.network,
            "-p",
            "127.0.0.1::7233",
            "-e",
            "DB=postgres12",
            "-e",
            "DB_PORT=5432",
            "-e",
            "POSTGRES_USER=temporal",
            "-e",
            "POSTGRES_PWD",
            "-e",
            f"POSTGRES_SEEDS={db}",
            "-e",
            "BIND_ON_IP=0.0.0.0",
            "-e",
            "NUM_HISTORY_SHARDS=4",
            "-e",
            "DEFAULT_NAMESPACE=default",
            "-e",
            "DEFAULT_NAMESPACE_RETENTION=7d",
            "-e",
            "DYNAMIC_CONFIG_FILE_PATH=config/dynamicconfig/development-sql.yaml",
            image,
        )
        self.wait(
            lambda: self.docker(
                "exec",
                name,
                "temporal",
                "--address",
                "127.0.0.1:7233",
                "--command-timeout",
                "4s",
                "operator",
                "cluster",
                "health",
            )
        )
        address = self.docker("port", name, "7233").decode().strip()
        return name, address

    def migrate(self, image: str, command: str, db: str = "postgres", succeeds: bool = True):
        args = (
            "run",
            "--rm",
            "--network",
            self.network,
            "-e",
            "POSTGRES_USER=temporal",
            "-e",
            "POSTGRES_PWD",
            "-e",
            f"POSTGRES_SEEDS={db}",
            image,
            command,
        )
        if succeeds:
            self.docker(*args)
        else:
            try:
                self.docker(*args)
            except subprocess.CalledProcessError:
                return
            raise AssertionError(f"Unsafe schema operation unexpectedly succeeded: {command}")

    def cleanup(self):
        for container in reversed(self.containers):
            subprocess.run(["docker", "rm", "-f", container], capture_output=True)
        for volume in self.volumes:
            subprocess.run(["docker", "volume", "rm", volume], capture_output=True)
        subprocess.run(["docker", "network", "rm", self.network], capture_output=True)


async def checkpoint(client: Client, ids: list[str]):
    async with Worker(
        client,
        task_queue=QUEUE,
        workflows=[UpgradeProbe],
        activities=[echo],
        workflow_runner=UnsandboxedWorkflowRunner(),
    ):
        for id in ids:
            async with asyncio.timeout(90):
                while not await client.get_workflow_handle(id).query(UpgradeProbe.checkpoint):
                    await asyncio.sleep(0.5)


async def verify(client: Client, ids: list[str], paused: bool):
    for id in ids:
        history = await client.get_workflow_handle(id).fetch_history()
        await Replayer(workflows=[UpgradeProbe], workflow_runner=UnsandboxedWorkflowRunner()).replay_workflow(
            history
        )
    schedule = await client.get_schedule_handle("upgrade-schedule").describe()
    assert schedule.schedule.state.paused is paused
    # SQL visibility indexing is asynchronous; test the real 1.32 converter.
    async with asyncio.timeout(60):
        while True:
            found = {row.id async for row in client.list_workflows(query="WorkflowType = 'UpgradeProbe'")}
            if set(ids) <= found:
                break
            await asyncio.sleep(1)


async def resume(client: Client, ids: list[str]):
    async with Worker(
        client,
        task_queue=QUEUE,
        workflows=[UpgradeProbe],
        activities=[echo],
        workflow_runner=UnsandboxedWorkflowRunner(),
    ):
        for id in ids:
            handle = client.get_workflow_handle(id)
            await handle.signal(UpgradeProbe.release)
            assert await asyncio.wait_for(handle.result(), 90) == id + ":resumed"


async def run():
    test = Rehearsal()
    try:
        test.docker("network", "create", test.network)
        pg = test.postgres("postgres")
        old = test.build("1.29.7", "legacy")
        server, address = test.start(old)
        test.wait(
            lambda: test.docker(
                "exec",
                server,
                "temporal",
                "operator",
                "namespace",
                "describe",
                "--namespace",
                "default",
            )
        )
        # Allow namespace propagation through the server's namespace cache.
        await asyncio.sleep(10)
        client = await Client.connect(address)
        await client.create_schedule(
            "upgrade-schedule",
            Schedule(
                action=ScheduleActionStartWorkflow(
                    UpgradeProbe.run, "scheduled", id="scheduled-probe", task_queue=QUEUE
                ),
                spec=ScheduleSpec(intervals=[ScheduleIntervalSpec(every=timedelta(minutes=1))]),
                state=ScheduleState(paused=True, note="Upgrade rehearsal"),
            ),
        )
        ids = ["upgrade-pending"]
        await client.start_workflow(UpgradeProbe.run, ids[0], id=ids[0], task_queue=QUEUE)
        await checkpoint(client, ids)
        await verify(client, ids, paused=True)
        test.docker("stop", "-t", "60", server)

        with tempfile.TemporaryDirectory(prefix=test.prefix) as directory:
            # Both DBs from the same stopped-server boundary. Restore is tested below.
            for database in ("temporal", "temporal_visibility"):
                dump = test.docker("exec", pg, "pg_dump", "-U", "temporal", "-Fc", database)
                Path(directory, database + ".dump").write_bytes(dump)

            previous = "1.29.7"
            for version in VERSIONS:
                print(f"Rehearsing {previous} -> {version}", flush=True)
                tools = test.build(version, "tools")
                image = test.build(version, "server")
                # Refuse destructive initialization and an unsupported jump.
                test.migrate(tools, "initialize-empty", succeeds=False)
                test.migrate(tools, "upgrade-from-1.28.0", succeeds=False)
                test.migrate(tools, "upgrade-from-" + previous)
                server, address = test.start(image)
                client = await Client.connect(address)
                await checkpoint(client, ids)
                await verify(client, ids, paused=True)
                # Leave fresh durable history from every intermediate release pending.
                id = "upgrade-" + version
                await client.start_workflow(UpgradeProbe.run, id, id=id, task_queue=QUEUE)
                ids.append(id)
                await checkpoint(client, ids)
                test.docker("stop", "-t", "60", server)
                previous = version

            # Restart final server on the same DB, then resume all retained executions.
            server, address = test.start(image)
            client = await Client.connect(address)
            await resume(client, ids)
            await verify(client, ids, paused=True)
            schedule = client.get_schedule_handle("upgrade-schedule")
            await schedule.unpause(note="Verify dispatch after upgrade")
            await schedule.trigger()
            async with Worker(
                client,
                task_queue=QUEUE,
                workflows=[UpgradeProbe],
                activities=[echo],
                workflow_runner=UnsandboxedWorkflowRunner(),
            ):
                async with asyncio.timeout(90):
                    while not (await schedule.describe()).info.recent_actions:
                        await asyncio.sleep(1)
                actions = (await schedule.describe()).info.recent_actions
                scheduled_id = actions[-1].action.workflow_id
                await client.get_workflow_handle(scheduled_id).signal(UpgradeProbe.release)
                assert (
                    await asyncio.wait_for(client.get_workflow_handle(scheduled_id).result(), 90)
                    == "scheduled:resumed"
                )
            await schedule.pause(note="Rehearsal done")

            # Rollback means restoring the old backup, NOT starting 1.29 on 1.32 data.
            restore = test.postgres("restored")
            for database in ("temporal", "temporal_visibility"):
                test.docker(
                    "exec",
                    "-i",
                    restore,
                    "pg_restore",
                    "-U",
                    "temporal",
                    "--exit-on-error",
                    "--create",
                    "-d",
                    "postgres",
                    data=Path(directory, database + ".dump").read_bytes(),
                )
            _, address = test.start(old, db="restored")
            client = await Client.connect(address)
            await checkpoint(client, [ids[0]])
            await verify(client, [ids[0]], paused=True)
            await resume(client, [ids[0]])

            # Fresh maintained installs require explicit schemas and namespace creation.
            test.postgres("fresh")
            test.migrate(tools, "initialize-empty", db="fresh")
            fresh, address = test.start(image, db="fresh")
            test.docker(
                "exec",
                fresh,
                "temporal",
                "operator",
                "namespace",
                "create",
                "--namespace",
                "default",
                "--retention",
                "7d",
            )
            await asyncio.sleep(10)
            client = await Client.connect(address)
            await client.start_workflow(UpgradeProbe.run, "fresh", id="fresh", task_queue=QUEUE)
            await checkpoint(client, ["fresh"])
            await resume(client, ["fresh"])
        print(
            "PASS: consecutive minors, replay/resumption, schedules, visibility, restart, restore, "
            "fresh install"
        )
    except subprocess.CalledProcessError as exc:
        # No command/environment dump: tools may include connection configuration.
        print((exc.stderr or b"").decode(errors="replace"))
        raise RuntimeError("Docker rehearsal failed; inspect the CI step") from None
    finally:
        test.cleanup()


if __name__ == "__main__":
    asyncio.run(run())
