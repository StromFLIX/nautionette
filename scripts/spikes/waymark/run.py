"""Run a disposable, real-Postgres spike (NOT pytest's in-memory execution).

Requires an empty, dedicated WAYMARK_DATABASE_URL and installed Waymark 0.30.1
binaries. Never point this at a production database. See README.md.
"""

import asyncio
import importlib.util
import json
import math
import os
import shlex
import signal
import sqlite3
import statistics
import subprocess
import sys
import time
import uuid
from datetime import timedelta
from pathlib import Path

import httpx
import psutil
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import UnsandboxedWorkflowRunner, Worker
from waymark import (
    Absolute,
    bridge,
    delete_schedule,
    list_schedules,
    pause_schedule,
    resume_schedule,
    schedule_workflow,
)
from waymark.actions import deserialize_workflow_outcome

ROOT = Path(__file__).resolve().parents[3]
sys.path[:0] = [str(ROOT / "services/worker"), str(ROOT / "workflows")]
import workflows as cases  # noqa: E402

OUT = Path(os.environ.get("SPIKE_OUTPUT", "/workspace/waymark-results")).resolve()
OUT.mkdir(parents=True, exist_ok=True)
os.environ["SPIKE_LEDGER"] = str(OUT / "ledger.sqlite")
os.environ["ARTIFACTS_DIR"] = str(OUT / "artifacts")
# activities module was imported above; set its artifact directory explicitly.
import nautionette_worker.activities as production_activities  # noqa: E402

production_activities.ARTIFACTS_DIR = OUT / "artifacts"
ENV = dict(os.environ)
ENV.update(
    {
        "PYTHONPATH": os.pathsep.join(sys.path),
        "WAYMARK_USER_MODULE": "workflows",
        "WAYMARK_WORKER_COUNT": "2",
        "WAYMARK_CONCURRENT_PER_WORKER": "10",
        "WAYMARK_HTTP_ENABLED": "true",
        "WAYMARK_HTTP_ADDR": "127.0.0.1:24119",
        "METRICS_ADDR": "127.0.0.1:9118",
        "WAYMARK_BRIDGE_GRPC_ADDR": "127.0.0.1:24117",
        "WAYMARK_BRIDGE_GRPC_PORT": "24117",
    }
)
os.environ.update(ENV)
TOKEN = uuid.uuid4().hex[:10]
RESULTS = {
    "run_token": TOKEN,
    "waymark_version": "0.30.1",
    "configuration": {k: ENV[k] for k in ["WAYMARK_WORKER_COUNT", "WAYMARK_CONCURRENT_PER_WORKER"]},
    "tests": {},
    "benchmarks": {},
}


def spawn(binary, log):
    command = shlex.split(os.environ.get(binary.upper().replace("-", "_") + "_COMMAND", binary))
    # Commands are supplied by the local operator, never workflow input.
    return subprocess.Popen(  # noqa: S603
        command,
        env=ENV,
        cwd=OUT,
        stdout=(OUT / log).open("a"),
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )


async def ready(proc):
    async with httpx.AsyncClient() as client:
        for _ in range(200):
            if proc.poll() is not None:
                raise RuntimeError(f"executor exited {proc.returncode}; see executor.log")
            try:
                if (await client.get("http://127.0.0.1:24119/healthz")).status_code == 200:
                    return
            except httpx.HTTPError:
                pass
            await asyncio.sleep(0.1)
    raise TimeoutError("executor readiness")


def calls(key, event):
    with sqlite3.connect(OUT / "ledger.sqlite") as db:
        return db.execute("SELECT count(*) FROM calls WHERE key=? AND event=?", (key, event)).fetchone()[0]


async def wait_calls(key, event, count):
    for _ in range(600):
        try:
            if calls(key, event) >= count:
                return
        except sqlite3.OperationalError:
            pass
        await asyncio.sleep(0.05)
    raise TimeoutError(f"waiting for {key} {event}")


async def result(instance):
    payload = await asyncio.wait_for(
        bridge.wait_for_instance(instance_id=instance, poll_interval_secs=0.01), 90
    )
    if payload is None:
        raise TimeoutError(instance)
    outcome = deserialize_workflow_outcome(payload)
    if outcome.error is not None:
        raise RuntimeError(str(outcome.error))
    return outcome.result


async def run(cls, **kwargs):
    return await result(await cls().run(_blocking=False, **kwargs))


def distribution(values):
    values = sorted(values)
    return {
        "count": len(values),
        "p50_ms": round(statistics.median(values) * 1000, 2),
        "p95_ms": round(values[math.ceil(len(values) * 0.95) - 1] * 1000, 2),
        "min_ms": round(values[0] * 1000, 2),
        "max_ms": round(values[-1] * 1000, 2),
    }


def resource_sample(proc):
    p = psutil.Process(proc.pid)
    procs = [p, *p.children(recursive=True)]
    return {
        "rss_mib": round(sum(x.memory_info().rss for x in procs) / 2**20, 1),
        "processes": len(procs),
        "cpu_seconds": round(sum(sum(x.cpu_times()[:2]) for x in procs), 2),
    }


async def benchmark_waymark():
    for width in [1, 10, 50]:
        await run(cases.Chain, key=f"warm-{TOKEN}-{width}", delay=0.02)
        latency, enqueue = [], []
        sem = asyncio.Semaphore(width)

        async def one(i, sem=sem, width=width, enqueue=enqueue, latency=latency):
            async with sem:
                start = time.perf_counter()
                iid = await cases.Chain().run(key=f"bench-w-{TOKEN}-{width}-{i}", delay=0.02, _blocking=False)
                enqueue.append(time.perf_counter() - start)
                await result(iid)
                latency.append(time.perf_counter() - start)

        start = time.perf_counter()
        await asyncio.gather(*[one(i) for i in range(50)])
        RESULTS["benchmarks"][f"waymark_chain_concurrency_{width}"] = {
            "completion": distribution(latency),
            "enqueue": distribution(enqueue),
            "workflows_per_second": round(50 / (time.perf_counter() - start), 2),
        }
    start = time.perf_counter()
    await cases.Chain().run(key=f"default-poll-{TOKEN}", delay=0.02)
    RESULTS["benchmarks"]["waymark_default_blocking_ms"] = round((time.perf_counter() - start) * 1000, 2)
    start = time.perf_counter()
    got = await run(cases.Fanout, keys=[f"fan-w-{TOKEN}-{i}" for i in range(20)], delay=0.1)
    assert len(got) == 20
    RESULTS["benchmarks"]["waymark_fanout20_ms"] = round((time.perf_counter() - start) * 1000, 2)


async def temporal_tests():
    # Dev server is NOT our deployed Postgres-backed Temporal cluster.
    spec = importlib.util.spec_from_file_location("original_url_digest", ROOT / "workflows/url_digest.py")
    original = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(original)
    async with await WorkflowEnvironment.start_local() as env:
        async with Worker(
            env.client,
            task_queue=f"spike-{TOKEN}",
            workflows=[original.UrlDigest, cases.TemporalChain, cases.TemporalFanout],
            activities=[
                production_activities.http_fetch,
                production_activities.save_artifact,
                cases.temporal_agent,
                cases.temporal_step,
            ],
            workflow_runner=UnsandboxedWorkflowRunner(),
        ):
            url = "https://raw.githubusercontent.com/piercefreeman/waymark/main/README.md"
            expected = await env.client.execute_workflow(
                original.UrlDigest.run,
                {"url": url, "focus": "durability"},
                id=f"temporal-url-{TOKEN}",
                task_queue=f"spike-{TOKEN}",
            )
            actual = await run(
                cases.WaymarkUrlDigest,
                params={"url": url, "focus": "durability", "run_id": f"waymark-url-{TOKEN}"},
            )
            assert actual == expected
            tfile = OUT / f"artifacts/url-digest-temporal-url-{TOKEN}.md"
            wfile = OUT / f"artifacts/url-digest-waymark-url-{TOKEN}.md"
            assert tfile.read_text() == wfile.read_text()
            RESULTS["tests"]["existing_url_digest"] = {
                "pass": True,
                "original_workflow_unmodified": True,
                "http": "real public GitHub README fetch",
                "agent": "deterministic fixture; no real model",
                "output": actual,
                "artifact_equal": True,
            }
            # A controlled local HTTP fixture exercises the original error branch.
            from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
            from threading import Thread

            class Handler(BaseHTTPRequestHandler):
                def do_GET(self):
                    self.send_response(404)
                    self.end_headers()

                def log_message(self, *args):
                    pass

            server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
            Thread(target=server.serve_forever, daemon=True).start()
            error_url = f"http://127.0.0.1:{server.server_port}/missing"
            try:
                expected_error = await env.client.execute_workflow(
                    original.UrlDigest.run,
                    {"url": error_url},
                    id=f"temporal-404-{TOKEN}",
                    task_queue=f"spike-{TOKEN}",
                )
                actual_error = await run(
                    cases.WaymarkUrlDigest, params={"url": error_url, "run_id": f"waymark-404-{TOKEN}"}
                )
                assert expected_error == actual_error
                assert not (OUT / f"artifacts/url-digest-waymark-404-{TOKEN}.md").exists()
                RESULTS["tests"]["url_digest_404"] = {"pass": True, "output": actual_error}
            finally:
                server.shutdown()
            for width in [1, 10, 50]:
                latency, enqueue = [], []
                sem = asyncio.Semaphore(width)

                async def one(i, sem=sem, width=width, enqueue=enqueue, latency=latency):
                    async with sem:
                        start = time.perf_counter()
                        handle = await env.client.start_workflow(
                            cases.TemporalChain.run,
                            {"key": f"bench-t-{TOKEN}-{width}-{i}", "delay": 0.02},
                            id=f"bench-t-{TOKEN}-{width}-{i}",
                            task_queue=f"spike-{TOKEN}",
                        )
                        enqueue.append(time.perf_counter() - start)
                        await handle.result()
                        latency.append(time.perf_counter() - start)

                start = time.perf_counter()
                await asyncio.gather(*[one(i) for i in range(50)])
                RESULTS["benchmarks"][f"temporal_dev_chain_concurrency_{width}"] = {
                    "completion": distribution(latency),
                    "enqueue": distribution(enqueue),
                    "workflows_per_second": round(50 / (time.perf_counter() - start), 2),
                }
            start = time.perf_counter()
            got = await env.client.execute_workflow(
                cases.TemporalFanout.run,
                {"keys": [f"fan-t-{TOKEN}-{i}" for i in range(20)], "delay": 0.1},
                id=f"fan-t-{TOKEN}",
                task_queue=f"spike-{TOKEN}",
            )
            assert len(got) == 20
            RESULTS["benchmarks"]["temporal_dev_fanout20_ms"] = round((time.perf_counter() - start) * 1000, 2)


async def main():
    if not os.environ.get("SPIKE_DISPOSABLE_DB") == "1":
        raise RuntimeError("Set SPIKE_DISPOSABLE_DB=1 only for a dedicated disposable database")
    if os.environ.get("PYTEST_CURRENT_TEST"):
        raise RuntimeError("must not run under pytest (it switches Waymark to in-memory)")
    executor = spawn("waymark-executor", "executor.log")
    broker = None
    try:
        await ready(executor)
        broker = spawn("waymark-bridge", "bridge.log")
        for cls in [cases.RawPort, cases.FormattingProbe, cases.FinallyProbe]:
            try:
                cls.workflow_ir()
                raise AssertionError(f"{cls.__name__} unexpectedly compiled")
            except Exception as exc:
                if isinstance(exc, AssertionError):
                    raise
                RESULTS["tests"][cls.__name__] = {
                    "expected_rejection": type(exc).__name__,
                    "detail": str(exc),
                }
        assert await run(cases.Retry, key=f"retry-{TOKEN}") == f"retry-{TOKEN}"
        assert calls(f"retry-{TOKEN}", "started") == 3
        RESULTS["tests"]["retry"] = {"pass": True, "attempts": 3}
        for cls in [cases.Failing, cases.Timeout]:
            try:
                await run(cls, key=f"{cls.__name__}-{TOKEN}")
                raise AssertionError("expected failure")
            except Exception as exc:
                if isinstance(exc, AssertionError):
                    raise
                RESULTS["tests"][cls.__name__] = {"expected_failure": type(exc).__name__, "detail": str(exc)}
        await asyncio.sleep(2.1)
        RESULTS["tests"]["timeout_side_effect"] = {
            "started": calls(f"Timeout-{TOKEN}", "started"),
            "finished_after_timeout": calls(f"Timeout-{TOKEN}", "finished"),
        }
        for cls, phase in [(cases.Recovery, "finished"), (cases.Inflight, "started")]:
            key = f"{cls.__name__}-{TOKEN}"
            iid = await cls().run(key=key, _blocking=False)
            await wait_calls(key, phase, 1)
            # SIGKILL the node and its workers to model an entire container dying.
            killed = time.perf_counter()
            os.killpg(executor.pid, signal.SIGKILL)
            executor.wait()
            executor = spawn("waymark-executor", "executor.log")
            await ready(executor)
            assert await result(iid) == key
            RESULTS["tests"][cls.__name__] = {
                "pass": True,
                "restart_to_result_seconds": round(time.perf_counter() - killed, 3),
                "started": calls(key, "started"),
                "finished": calls(key, "finished"),
            }
        # A worker alone dying on a live node follows ActionExecutionLost policy.
        key = f"worker-kill-{TOKEN}"
        iid = await cases.Inflight().run(key=key, _blocking=False)
        await wait_calls(key, "started", 1)
        workers = psutil.Process(executor.pid).children(recursive=True)
        assert workers
        for proc in workers:
            proc.kill()
        try:
            assert await result(iid) == key
            RESULTS["tests"]["worker_kill"] = {
                "pass": True,
                "started": calls(key, "started"),
                "finished": calls(key, "finished"),
            }
        except RuntimeError as exc:
            RESULTS["tests"]["worker_kill"] = {
                "pass": False,
                "error": str(exc),
                "started": calls(key, "started"),
                "finished": calls(key, "finished"),
            }
        # A dead worker pool must not contaminate the following benchmarks.
        executor.terminate()
        executor.wait(timeout=10)
        executor = spawn("waymark-executor", "executor.log")
        await ready(executor)
        schedule = Absolute(f"spike-{TOKEN}")
        try:
            await schedule_workflow(
                cases.Chain,
                schedule_name=schedule,
                schedule=timedelta(seconds=2),
                arguments={"key": f"schedule-{TOKEN}", "delay": 0.02},
            )
            await wait_calls(f"schedule-{TOKEN}", "finished", 3)
            assert await pause_schedule(schedule)
            info = next(x for x in await list_schedules() if x.schedule_name == f"spike-{TOKEN}")
            RESULTS["tests"]["schedule"] = {"pass": info.status == "paused", "info": str(info)}
            assert await resume_schedule(schedule)
        finally:
            await delete_schedule(schedule)
        idle_before = resource_sample(executor)
        await asyncio.sleep(5)
        idle_after = resource_sample(executor)
        RESULTS["resources"] = {
            "waymark_node_and_workers": idle_after,
            "idle_cpu_seconds_over_5s": round(idle_after["cpu_seconds"] - idle_before["cpu_seconds"], 2),
            "bridge": resource_sample(broker),
        }
        await benchmark_waymark()
        await temporal_tests()
        async with httpx.AsyncClient() as client:
            for path, name in [
                ("/api/openapi.json", "openapi"),
                ("/api/docs", "api_docs"),
                ("/", "dashboard"),
                ("/healthz", "healthz"),
            ]:
                response = await client.get("http://127.0.0.1:24119" + path)
                (OUT / f"{name}.txt").write_text(response.text)
                RESULTS["tests"][name] = {"status": response.status_code, "bytes": len(response.content)}
            response = await client.get("http://127.0.0.1:9118/metrics")
            (OUT / "metrics.txt").write_text(response.text)
        print(json.dumps(RESULTS, indent=2))
    finally:
        (OUT / "results.json").write_text(json.dumps(RESULTS, indent=2))
        for proc in [executor, broker]:
            if proc is not None and proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    os.killpg(proc.pid, signal.SIGKILL)
                    proc.wait()


if __name__ == "__main__":
    asyncio.run(main())
