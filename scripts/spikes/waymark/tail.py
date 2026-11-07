"""Probe whether a successful timed action keeps its timeout VM resident."""

import asyncio
import json
import os
import signal
import time

import run as harness
from waymark import Workflow, workflow

from workflows import Chain, step


@workflow
class SuccessfulTimeout(Workflow):
    async def run(self, key: str) -> str:
        return await self.run_action(step(key, 0.02, 0), timeout=6.0)


async def main():
    if os.environ.get("SPIKE_DISPOSABLE_DB") != "1":
        raise RuntimeError("Requires disposable DB")
    harness.ENV["WAYMARK_MAX_CONCURRENT_INSTANCES"] = "1"
    executor = harness.spawn("waymark-executor", "tail-executor.log")
    broker = None
    findings = {}
    try:
        await harness.ready(executor)
        broker = harness.spawn("waymark-bridge", "tail-bridge.log")
        await harness.run(Chain, key=f"warm-tail-{harness.TOKEN}", delay=0.02)
        start = time.perf_counter()
        await harness.run(Chain, key=f"control-tail-{harness.TOKEN}", delay=0.02)
        findings["control_next_workflow_seconds"] = round(time.perf_counter() - start, 3)
        start = time.perf_counter()
        await harness.run(SuccessfulTimeout, key=f"tail-{harness.TOKEN}")
        findings["timed_action_result_ms"] = round((time.perf_counter() - start) * 1000, 2)
        start = time.perf_counter()
        await harness.run(Chain, key=f"after-tail-{harness.TOKEN}", delay=0.02)
        findings["next_workflow_seconds"] = round(time.perf_counter() - start, 3)
        print(json.dumps(findings, indent=2))
    finally:
        (harness.OUT / "tail-results.json").write_text(json.dumps(findings, indent=2))
        for p in [executor, broker]:
            if p is not None and p.poll() is None:
                p.terminate()
                try:
                    p.wait(timeout=10)
                except Exception:
                    os.killpg(p.pid, signal.SIGKILL)
                    p.wait()


if __name__ == "__main__":
    asyncio.run(main())
