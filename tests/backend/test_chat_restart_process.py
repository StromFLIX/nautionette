"""Real process restarts, without requiring Docker or a model provider."""

import asyncio
import json
import os
import signal
import sys
from contextlib import asynccontextmanager

import httpx
import pytest

from ..conftest import APP_TOKEN

pytestmark = [pytest.mark.slow, pytest.mark.skipif(os.name != "posix", reason="Requires POSIX signals")]


async def until(probe, predicate, timeout=10):
    async with asyncio.timeout(timeout):
        while True:
            try:
                result = await probe()
            except httpx.TransportError:
                pass
            else:
                if predicate(result):
                    return result
            await asyncio.sleep(0.02)


def journal(path):
    return [json.loads(line) for line in path.read_text().splitlines()]


@asynccontextmanager
async def backend_process(tmp_path, port, repo_root):
    env = {
        **os.environ,
        "DATA_DIR": str(tmp_path / "data"),
        "ARTIFACTS_DIR": str(tmp_path / "artifacts"),
        "WORKFLOWS_DIR": str(tmp_path / "workflows"),
        "WORKFLOWS_SEED_DIR": str(tmp_path / "seed"),
        "BACKEND_CHAT_DRAIN_SECONDS": "10",
        "OTEL_SDK_DISABLED": "true",
    }
    with (tmp_path / "backend.log").open("ab") as log:
        process = await asyncio.create_subprocess_exec(
            sys.executable,
            "-m",
            "tests.backend.restart_fixture",
            str(port),
            str(tmp_path / "journal.jsonl"),
            str(tmp_path / "release"),
            env=env,
            cwd=repo_root,
            stdout=log,
            stderr=log,
        )
        async with httpx.AsyncClient(
            base_url=f"http://127.0.0.1:{port}",
            headers={"Authorization": f"Bearer {APP_TOKEN}", "Accept": "application/json"},
            timeout=2,
        ) as client:
            try:
                await until(lambda: client.get("/healthz"), lambda r: r.status_code == 200)
                yield process, client
            finally:
                if process.returncode is None:
                    process.terminate()
                    try:
                        await asyncio.wait_for(process.wait(), 3)
                    except TimeoutError:
                        process.kill()
                        await process.wait()


async def active_tool(client, chat):
    return await until(
        lambda: client.get(f"/api/chats/{chat}"),
        lambda r: any(s.get("kind") == "tool" for s in (r.json()["active_turn"] or {}).get("steps", [])),
    )


async def submit_with_followup(client):
    chat = (await client.post("/api/chats", json={})).json()["id"]
    assert (
        await client.post(f"/api/chats/{chat}/messages", json={"text": "Run", "message_id": "first"})
    ).status_code == 202
    await active_tool(client, chat)
    # Force a standalone queued turn rather than steering into the first turn.
    assert (await client.patch(f"/api/chats/{chat}", json={"timeout_exempt": True})).status_code == 200
    assert (
        await client.post(
            f"/api/chats/{chat}/messages", json={"text": "Then report", "message_id": "next", "queue": True}
        )
    ).status_code == 202
    return chat


async def test_sigterm_during_read_only_tool_then_restart_completes_queue_once(
    tmp_path, unused_tcp_port, repo_root
):
    async with backend_process(tmp_path, unused_tcp_port, repo_root) as (process, client):
        chat = await submit_with_followup(client)
        # Keep an SSE subscription open too: it must not indefinitely block exit.
        async with client.stream("GET", f"/api/chats/{chat}/stream") as stream:
            assert stream.status_code == 200
            process.send_signal(signal.SIGTERM)
            await until(lambda: client.get("/healthz"), lambda r: r.json()["chat_draining"])
            assert process.returncode is None
            rejected = await client.post(f"/api/chats/{chat}/messages", json={"text": "Too late"})
            assert rejected.status_code == 503
            # The already-running read-only tool calls the backend after SIGTERM.
            (tmp_path / "release").touch()
            assert await asyncio.wait_for(process.wait(), 10) in (0, -signal.SIGTERM)
    assert journal(tmp_path / "journal.jsonl") == [
        {"kind": "agent", "turn": "first"},
        {"kind": "tool", "turn": "first"},
        {"kind": "cleanup", "turn": "first"},
    ]
    async with backend_process(tmp_path, unused_tcp_port, repo_root) as (_, client):
        response = await until(
            lambda: client.get(f"/api/chats/{chat}"),
            lambda r: len(r.json()["messages"]) == 4 and r.json()["active_turn"] is None,
        )
        snapshot = response.json()
        assert snapshot["chat"]["queue_paused"] == 0
        assert [m["content"] for m in snapshot["messages"]] == [
            "Run",
            "Starting first. Finished first.",
            "Then report",
            "Starting next. Finished next.",
        ]
        answers = [m for m in snapshot["messages"] if m["role"] == "assistant"]
        assert all(not m["meta"]["error"] for m in answers)
    assert journal(tmp_path / "journal.jsonl") == [
        {"kind": "agent", "turn": "first"},
        {"kind": "tool", "turn": "first"},
        {"kind": "cleanup", "turn": "first"},
        {"kind": "agent", "turn": "next"},
        {"kind": "tool", "turn": "next"},
        {"kind": "cleanup", "turn": "next"},
    ]


async def test_sigkill_leaves_explicit_safe_recovery_state_without_replaying_tools(
    tmp_path, unused_tcp_port, repo_root
):
    try:
        async with backend_process(tmp_path, unused_tcp_port, repo_root) as (process, client):
            chat = await submit_with_followup(client)
            process.kill()
            assert await asyncio.wait_for(process.wait(), 5) == -signal.SIGKILL
        async with backend_process(tmp_path, unused_tcp_port, repo_root) as (_, client):
            snapshot = (await client.get(f"/api/chats/{chat}")).json()
            assert snapshot["active_turn"] is None
            assert snapshot["chat"]["queue_paused"] == 1
            assert any(m["id"] == "next" and m["meta"].get("queued") for m in snapshot["messages"])
            answer = snapshot["messages"][-1]
            assert answer["content"] == "Starting first. "
            assert "Verify unfinished tools" in answer["meta"]["error"]
            assert "not automatically replayed" in answer["meta"]["error"]
            assert journal(tmp_path / "journal.jsonl") == [
                {"kind": "agent", "turn": "first"},
                {"kind": "tool", "turn": "first"},
            ]
    finally:
        # Let any child that survived its parent exit; this is only a read-only tool.
        (tmp_path / "release").touch()
