"""Additional isolated capacity/health probes; run after run.py, not concurrently."""

import asyncio
import json
import os
import signal
import time
from pathlib import Path

import httpx
import psutil
import run as harness
from waymark import Workflow, workflow

from workflows import Chain, step


@workflow
class BareFailure(Workflow):
    async def run(self, key: str) -> str:
        return await step(key, 0.02, 5)


@workflow
class Sleeper(Workflow):
    async def run(self, key: str) -> str:
        started = await step(key, 0.02, 0)
        await asyncio.sleep(6.0)
        return started


async def main():
    if os.environ.get("SPIKE_DISPOSABLE_DB") != "1":
        raise RuntimeError("Requires disposable DB")
    harness.ENV["WAYMARK_MAX_CONCURRENT_INSTANCES"] = "3"
    first = harness.spawn("waymark-executor", "extra-executor.log")
    second = broker = None
    findings = {}
    try:
        await harness.ready(first)
        broker = harness.spawn("waymark-bridge", "extra-bridge.log")
        key = f"bare-{harness.TOKEN}"
        try:
            await harness.run(BareFailure, key=key)
            raise AssertionError("bare failing action unexpectedly succeeded")
        except RuntimeError as exc:
            findings["bare_failure"] = {"error": str(exc), "attempts": harness.calls(key, "started")}
        assert findings["bare_failure"]["attempts"] == 1
        for mode in ["single_node", "add_second_node"]:
            keys = [f"{mode}-{harness.TOKEN}-{i}" for i in range(3)]
            sleepers = [await Sleeper().run(key=k, _blocking=False) for k in keys]
            for k in keys:
                await harness.wait_calls(k, "finished", 1)
            start = time.perf_counter()
            short = await Chain().run(key=f"short-{mode}-{harness.TOKEN}", delay=0.02, _blocking=False)
            if mode == "add_second_node":
                harness.ENV.update(
                    {
                        "WAYMARK_WORKER_GRPC_ADDR": "127.0.0.1:24218",
                        "WAYMARK_HTTP_ADDR": "127.0.0.1:24219",
                        "METRICS_ADDR": "127.0.0.1:9218",
                    }
                )
                second = harness.spawn("waymark-executor", "extra-second-node.log")
                # Restore primary settings for later work; the second node owns its env copy.
                harness.ENV.update(
                    {
                        "WAYMARK_WORKER_GRPC_ADDR": "127.0.0.1:24118",
                        "WAYMARK_HTTP_ADDR": "127.0.0.1:24119",
                        "METRICS_ADDR": "127.0.0.1:9118",
                    }
                )
            await harness.result(short)
            findings[mode] = {"short_workflow_seconds": round(time.perf_counter() - start, 3)}
            await asyncio.gather(*[harness.result(i) for i in sleepers])
            assert all(harness.calls(k, "started") == 1 for k in keys)
            if second:
                second.terminate()
                second.wait(timeout=10)
                second = None
        # Default recycling is off: killing workers leaves a live but useless node.
        for p in psutil.Process(first.pid).children(recursive=True):
            p.kill()
        await asyncio.sleep(0.2)
        health = httpx.get("http://127.0.0.1:24119/healthz")
        try:
            await harness.run(Chain, key=f"dead-pool-{harness.TOKEN}", delay=0.02)
            raise AssertionError("dead pool unexpectedly recovered")
        except RuntimeError as exc:
            findings["dead_pool"] = {
                "health_status": health.status_code,
                "health_body": health.json(),
                "workflow_error": str(exc),
                "live_python_workers": sum(
                    p.status() != psutil.STATUS_ZOMBIE
                    for p in psutil.Process(first.pid).children(recursive=True)
                ),
            }
        print(json.dumps(findings, indent=2))
    finally:
        (Path(harness.OUT) / "extra-results.json").write_text(json.dumps(findings, indent=2))
        for p in [first, second, broker]:
            if p is not None and p.poll() is None:
                p.terminate()
                try:
                    p.wait(timeout=10)
                except Exception:
                    os.killpg(p.pid, signal.SIGKILL)
                    p.wait()


if __name__ == "__main__":
    asyncio.run(main())
