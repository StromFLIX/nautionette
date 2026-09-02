"""The Temporal worker.

At least one is always running: if nothing is listening, triggers pile up with
nothing to pick them up. A restart drains in-flight activities instead of
killing them.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import signal
import sys
from datetime import timedelta

import httpx
from temporalio.client import Client
from temporalio.worker import UnsandboxedWorkflowRunner, Worker

from .activities import ALL as ACTIVITIES
from .loader import install_dependencies, load_workflows

logging.basicConfig(level=logging.INFO, format="%(asctime)s worker %(levelname)s %(message)s")
log = logging.getLogger("worker")

TEMPORAL_ADDRESS = os.environ.get("TEMPORAL_ADDRESS", "temporal:7233")
TEMPORAL_NAMESPACE = os.environ.get("TEMPORAL_NAMESPACE", "default")
TASK_QUEUE = os.environ.get("TEMPORAL_TASK_QUEUE", "nautionette")
WORKFLOWS_DIR = os.environ.get("WORKFLOWS_DIR", "/workflows")
BACKEND_URL = os.environ.get("BACKEND_URL", "http://backend:8080").rstrip("/")
INTERNAL_TOKEN = os.environ.get("INTERNAL_TOKEN", "").strip()
GRACE_SECONDS = int(os.environ.get("WORKER_GRACE_SECONDS", "50"))

# Filled in before the event loop starts; a file with a broken header is reported,
# never imported.
BAD_HEADERS: dict[str, str] = {}


async def announce(report: list[dict]) -> None:
    headers = {"X-Internal-Token": INTERNAL_TOKEN} if INTERNAL_TOKEN else {}
    body = {
        "scope": "worker",
        "kind": "worker.loaded",
        "payload": {
            "task_queue": TASK_QUEUE,
            "files": report,
            "loaded": sum(len(item["workflows"]) for item in report),
            "failed": [item["file"] for item in report if item["error"]],
        },
    }
    with contextlib.suppress(Exception):
        async with httpx.AsyncClient(timeout=10) as client:
            await client.post(f"{BACKEND_URL}/internal/events", json=body, headers=headers)


async def connect_with_retry() -> Client:
    delay = 1.0
    while True:
        try:
            return await Client.connect(TEMPORAL_ADDRESS, namespace=TEMPORAL_NAMESPACE)
        except Exception as exc:  # noqa: BLE001 - Temporal may still be starting
            log.warning("temporal not ready (%s), retrying in %.0fs", exc, delay)
            await asyncio.sleep(delay)
            delay = min(delay * 2, 15)


async def main() -> None:
    client = await connect_with_retry()
    workflows, report = load_workflows(WORKFLOWS_DIR, BAD_HEADERS)
    log.info(
        "starting worker on %s with %d workflow(s) and %d activities",
        TASK_QUEUE,
        len(workflows),
        len(ACTIVITIES),
    )
    await announce(report)

    worker = Worker(
        client,
        task_queue=TASK_QUEUE,
        workflows=workflows,
        activities=ACTIVITIES,
        # Workflow files are user code loaded at runtime; the sandbox cannot see them.
        workflow_runner=UnsandboxedWorkflowRunner(),
        # Without this a bug in a workflow file fails the workflow task and Temporal
        # retries it forever, so the run sits at RUNNING and nobody is told why.
        workflow_failure_exception_types=[Exception],
        graceful_shutdown_timeout=timedelta(seconds=GRACE_SECONDS),
        max_concurrent_activities=8,
    )

    stopping = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        with contextlib.suppress(NotImplementedError):
            loop.add_signal_handler(sig, stopping.set)

    async with worker:
        await stopping.wait()
        log.info("shutdown requested, draining for up to %ss", GRACE_SECONDS)


if __name__ == "__main__":
    # Dependencies first, then a clean interpreter: a package that appears part way
    # through a process does not import reliably.
    installed, BAD_HEADERS = install_dependencies(WORKFLOWS_DIR)
    if installed:
        log.info("dependencies installed, restarting into a clean interpreter")
        os.execv(sys.executable, [sys.executable, "-m", "nautionette_worker.main"])  # noqa: S606 - own interpreter
    asyncio.run(main())
