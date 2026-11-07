"""Isolated spike: never imported by Nautionette's production loader.

The existing url_digest orchestration is ported; HTTP and artifact actions reuse
production implementations. The model action is an explicitly deterministic
fixture, not an LLM/API benchmark. State/formatting moves into actions because
Waymark 0.30.1 cannot compile dict.get, or, f-strings, or workflow.info().
"""

import asyncio
import os
import sqlite3
import time
from datetime import timedelta

from nautionette_worker.activities import http_fetch, save_artifact
from temporalio import activity
from temporalio import workflow as temporal
from waymark import RetryPolicy, Workflow, action, workflow


def record(key: str, event: str) -> int:
    with sqlite3.connect(os.environ["SPIKE_LEDGER"], timeout=30) as db:
        db.execute("CREATE TABLE IF NOT EXISTS calls (key TEXT, event TEXT, at REAL)")
        db.execute("INSERT INTO calls VALUES (?, ?, ?)", (key, event, time.time()))
        return db.execute("SELECT count(*) FROM calls WHERE key=? AND event=?", (key, event)).fetchone()[0]


async def fixture_agent(params: dict) -> dict:
    record(params["prompt"], "agent")
    await asyncio.sleep(0.02)
    return {"ok": True, "output": {"summary": "Deterministic spike summary", "key_points": ["fixture"]}}


temporal_agent = activity.defn(name="agent_call")(fixture_agent)


@action
async def normalize(params: dict) -> dict:
    return {
        "url": params.get("url") or "https://pi.dev",
        "focus": params.get("focus") or "what it is and who it is for",
        "run_id": params["run_id"],
    }


@action
async def fetch(params: dict) -> dict:
    return await http_fetch({"url": params["url"]})


@action
async def error_output(params: dict, page: dict) -> dict:
    return {"summary": f"{params['url']} returned HTTP {page['status']}", "key_points": []}


@action
async def summarize(params: dict, page: dict) -> dict:
    return await fixture_agent(
        {
            "prompt": (
                f"Summarise this page. Focus on {params['focus']}.\n\n"
                f"URL: {params['url']}\n\n{page['body'][:20000]}"
            )
        }
    )


@action
async def persist(params: dict, digest: dict) -> dict:
    output = digest.get("output") or {"summary": digest.get("text", ""), "key_points": []}
    await save_artifact(
        {
            "name": f"url-digest-{params['run_id']}.md",
            "content": f"# {params['url']}\n\n{output.get('summary', '')}\n",
        }
    )
    return output


@workflow
class WaymarkUrlDigest(Workflow):
    async def run(self, params: dict) -> dict:
        normalized = await normalize(params)
        page = await self.run_action(
            fetch(normalized),
            retry=RetryPolicy(attempts=3, backoff_seconds=0.1),
            timeout=timedelta(minutes=2),
        )
        if page["status"] >= 400:
            return await error_output(normalized, page)
        digest = await self.run_action(
            summarize(normalized, page),
            retry=RetryPolicy(attempts=3, backoff_seconds=0.1),
            timeout=timedelta(minutes=10),
        )
        return await self.run_action(
            persist(normalized, digest),
            retry=RetryPolicy(attempts=3, backoff_seconds=0.1),
            timeout=timedelta(minutes=1),
        )


@action
async def step(key: str, delay: float, failures: int) -> str:
    attempt = record(key, "started")
    await asyncio.sleep(delay)
    if attempt <= failures:
        raise ValueError("intentional spike failure")
    record(key, "finished")
    return key


temporal_step = activity.defn(name="spike_step")(step.__wrapped__ if hasattr(step, "__wrapped__") else step)


@workflow
class Chain(Workflow):
    async def run(self, key: str, delay: float) -> str:
        first = await step(key, delay, 0)
        second = await step(first, delay, 0)
        return await step(second, delay, 0)


@temporal.defn
class TemporalChain:
    @temporal.run
    async def run(self, params: dict) -> str:
        key = params["key"]
        for _ in range(3):
            key = await temporal.execute_activity(
                "spike_step", args=[key, params["delay"], 0], start_to_close_timeout=timedelta(seconds=30)
            )
        return key


@workflow
class Fanout(Workflow):
    async def run(self, keys: list[str], delay: float) -> list[str]:
        return await asyncio.gather(*[step(key, delay, 0) for key in keys])


@temporal.defn
class TemporalFanout:
    @temporal.run
    async def run(self, params: dict) -> list[str]:
        return await asyncio.gather(
            *[
                temporal.execute_activity(
                    "spike_step", args=[key, params["delay"], 0], start_to_close_timeout=timedelta(seconds=30)
                )
                for key in params["keys"]
            ]
        )


@workflow
class Retry(Workflow):
    async def run(self, key: str) -> str:
        return await self.run_action(step(key, 0.02, 2), retry=RetryPolicy(attempts=3, backoff_seconds=0.1))


@workflow
class Failing(Workflow):
    async def run(self, key: str) -> str:
        return await self.run_action(step(key, 0.02, 10), retry=RetryPolicy(attempts=3, backoff_seconds=0.1))


@workflow
class Timeout(Workflow):
    async def run(self, key: str) -> str:
        return await self.run_action(step(key, 2.0, 0), timeout=0.2)


@workflow
class Recovery(Workflow):
    async def run(self, key: str) -> str:
        first = await step(key, 0.02, 0)
        await asyncio.sleep(4.0)
        return await step(first, 0.02, 0)


@workflow
class Inflight(Workflow):
    async def run(self, key: str) -> str:
        return await self.run_action(
            step(key, 4.0, 0),
            retry=RetryPolicy(
                attempts=3, exception_types=["Exception", "ActionExecutionLost"], backoff_seconds=0.1
            ),
        )


@workflow
class RawPort(Workflow):
    async def run(self, params: dict) -> dict:
        url = params.get("url") or "https://pi.dev"
        return {"summary": url}


@workflow
class FormattingProbe(Workflow):
    async def run(self, value: str) -> str:
        return f"Hello {value}"


@workflow
class FinallyProbe(Workflow):
    async def run(self, key: str) -> str:
        try:
            return await step(key, 0.02, 0)
        finally:
            await step(key, 0.02, 0)
