"""Measure the real chat POST -> first streamed text delta, retaining the test chat.

BASE=https://your-instance APP_TOKEN=... uv run python scripts/benchmark_chat_ttft.py \
    --project-id <selected-project-id> --samples 5

Uses a dedicated chat, never the caller's active chat. First and subsequent turns
are reported separately. Status/phase events, reasoning and tool starts are NOT
text tokens. This is HTTP/SSE latency, not browser paint latency. Model calls cost
money. No prompts, responses, tool arguments or credentials are written to output.
"""

from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
import time
import uuid
from collections.abc import Callable, Iterator
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlsplit

import httpx

PROMPT = "Reply with exactly READY. Do not call tools or modify files."


def sse_events(response: httpx.Response) -> Iterator[dict[str, Any]]:
    data: list[str] = []
    for line in response.iter_lines():
        if line.startswith("data:"):
            data.append(line[5:].removeprefix(" "))
        elif not line and data:
            yield json.loads("\n".join(data))
            data = []
    if data:
        yield json.loads("\n".join(data))


def stop_test_turn(client: httpx.Client, chat_id: str, turn_id: str) -> str:
    """Stop only our exact input after a broken/incomplete stream; never replay it."""
    try:
        response = client.post(f"/api/chats/{chat_id}/stop", json={"turn_id": turn_id})
        if response.status_code == 409:
            return "already_finished"
        return "stop_requested" if response.is_success else f"stop_http_{response.status_code}"
    except httpx.HTTPError:
        return "stop_unconfirmed_inspect_chat"


def measure_turn(
    client: httpx.Client,
    chat_id: str,
    project_ids: list[str],
    *,
    clock: Callable[[], float] = time.perf_counter,
) -> dict[str, Any]:
    turn_id = uuid.uuid4().hex
    sample: dict[str, Any] = {
        "turn_id": turn_id,
        "ok": False,
        "first_event_ms": None,
        "ttft_text_ms": None,
        "first_reasoning_ms": None,
        "milestones_ms": {},
        "tool_calls": 0,
    }
    started = clock()
    done = False
    result_ok = False
    failure = None
    try:
        with client.stream(
            "POST",
            f"/api/chats/{chat_id}/messages",
            headers={"Accept": "text/event-stream"},
            json={"text": PROMPT, "message_id": turn_id, "project_ids": project_ids},
        ) as response:
            sample["headers_ms"] = round((clock() - started) * 1000, 3)
            response.raise_for_status()
            if "text/event-stream" not in response.headers.get("content-type", ""):
                raise ValueError("expected_sse_response")
            for event in sse_events(response):
                elapsed = round((clock() - started) * 1000, 3)
                kind = event.get("type", "unknown")
                if sample["first_event_ms"] is None:
                    sample["first_event_ms"] = elapsed
                key = f"phase_{event.get('phase')}" if kind == "phase" else kind
                if kind in {"started", "session_ready", "commands", "phase", "tool", "result", "done"}:
                    sample["milestones_ms"].setdefault(key, elapsed)
                if kind == "delta" and event.get("text") and sample["ttft_text_ms"] is None:
                    sample["ttft_text_ms"] = elapsed
                elif kind == "thinking" and event.get("text") and sample["first_reasoning_ms"] is None:
                    sample["first_reasoning_ms"] = elapsed
                elif kind == "tool":
                    sample["tool_calls"] += 1
                elif kind == "error":
                    failure = "agent_error"
                elif kind == "result":
                    result_ok = event.get("ok") is True
                    if not result_ok:
                        failure = "agent_result_failed"
                elif kind == "done":
                    done = True
                    meta = (event.get("message") or {}).get("meta") or {}
                    if meta.get("error") or meta.get("interrupted"):
                        failure = "turn_failed_or_interrupted"
                    break
    except httpx.HTTPStatusError as exc:
        failure = f"http_{exc.response.status_code}"
    except httpx.HTTPError:
        failure = "transport_error"
    except (ValueError, TypeError, AttributeError):
        failure = "invalid_stream"
    finally:
        sample["total_ms"] = round((clock() - started) * 1000, 3)
        if not done:
            sample["cleanup"] = stop_test_turn(client, chat_id, turn_id)
    failure = failure or (
        "stream_ended_before_done"
        if not done
        else "missing_successful_result"
        if not result_ok
        else "no_text_delta"
        if sample["ttft_text_ms"] is None
        else None
    )
    sample["ok"] = failure is None
    if failure:
        sample["failure"] = failure
    return sample


def summarize(samples: list[dict[str, Any]]) -> dict[str, Any]:
    values = [sample["ttft_text_ms"] for sample in samples if sample["ok"]]
    return {
        "successful": len(values),
        "failed": len(samples) - len(values),
        "ttft_text_ms": {
            "median": round(statistics.median(values), 3),
            "min": min(values),
            "max": max(values),
        }
        if values
        else None,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", default=os.environ.get("BASE", ""))
    parser.add_argument("--project-id", action="append", default=[], help="Repeat for multiple projects")
    parser.add_argument("--model", help="Omit to inherit the current default agent's model")
    parser.add_argument("--reasoning-effort", help="Omit to inherit the agent/model default")
    parser.add_argument("--no-mcp-tools", action="store_true", help="Explicit empty MCP selection control")
    parser.add_argument("--samples", type=int, default=5)
    parser.add_argument("--timeout", type=float, default=180, help="HTTP inactivity timeout, seconds")
    args = parser.parse_args(argv)
    base = args.base.rstrip("/")
    url = urlsplit(base)
    if (
        url.scheme not in {"http", "https"}
        or not url.netloc
        or url.username
        or url.password
        or url.query
        or url.fragment
    ):
        parser.error("set BASE or --base to an HTTP(S) app origin without credentials or a query")
    if not os.environ.get("APP_TOKEN"):
        parser.error("set APP_TOKEN in the environment (never pass credentials on the command line)")
    if not 1 <= args.samples <= 100 or args.timeout <= 0:
        parser.error("samples must be 1..100 and timeout must be positive")
    config: dict[str, Any] = {
        "title": "TTFT benchmark",
        "project_ids": args.project_id,
        "timeout_exempt": False,
    }
    if args.model:
        config["model"] = args.model
    if args.reasoning_effort:
        config["reasoning_effort"] = args.reasoning_effort
    if args.no_mcp_tools:
        config["tools"] = []
    with httpx.Client(
        base_url=base,
        headers={"Authorization": f"Bearer {os.environ['APP_TOKEN']}"},
        timeout=httpx.Timeout(args.timeout, connect=15),
        follow_redirects=False,
    ) as client:
        health = client.get("/healthz")
        health.raise_for_status()
        build = health.json()
        response = client.post("/api/chats", json=config)
        response.raise_for_status()
        chat = response.json()
        report: dict[str, Any] = {
            "scope": "message POST to first SSE text delta; excludes chat creation and browser paint",
            "measured_at": datetime.now(UTC).isoformat(),
            "app": {key: build.get(key) for key in ("version", "build_commit", "environment")},
            "chat_id": chat["id"],
            "model": chat.get("model"),
            "reasoning_effort": chat.get("reasoning_effort"),
            "project_ids": args.project_id,
            "mcp_selection": "empty" if args.no_mcp_tools else "inherited",
            "selected_mcp_count": len(chat["tools"]) if isinstance(chat.get("tools"), list) else "all",
            "samples": [],
        }
        print(f"Retaining benchmark chat {chat['id']} for inspection.", file=sys.stderr)
        for index in range(args.samples):
            sample = measure_turn(client, chat["id"], args.project_id)
            sample["turn"] = "first" if index == 0 else "subsequent"
            report["samples"].append(sample)
            if not sample["ok"]:
                break  # Never queue more work after an uncertain/failed turn.
        report["summary"] = summarize(report["samples"])
        report["subsequent_turns"] = summarize(report["samples"][1:])
    print(json.dumps(report, indent=2))
    return 0 if all(sample["ok"] for sample in report["samples"]) else 1


if __name__ == "__main__":
    sys.exit(main())
