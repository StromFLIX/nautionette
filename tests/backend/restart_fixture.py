"""Disposable subprocess backend for real SIGTERM/SIGKILL restart regressions.

Uses the real app, lifespan, database, HTTP server and turn pipeline. Only model,
Docker and other outbound infrastructure are replaced. The tool is a real child
process that waits read-only, then calls the still-live authenticated backend.
"""

import asyncio
import json
import sys
from pathlib import Path

import uvicorn
from nautionette_backend import catalog, clients, conversations, main
from nautionette_backend.server import DrainingServer

from .fakes import FakeBroker

PORT, JOURNAL, RELEASE = sys.argv[1:]


def record(kind, turn):
    with Path(JOURNAL).open("a") as journal:
        journal.write(json.dumps({"kind": kind, "turn": turn}) + "\n")


async def noop(*args, **kwargs):
    pass


async def model_info(model):
    return {"id": model}


async def control(*args, **kwargs):
    return False


async def cleanup(chat, turn):
    record("cleanup", turn)


async def agent(job):
    turn = job["turn_id"]
    record("agent", turn)
    yield {"type": "delta", "text": f"Starting {turn}. "}
    # Audit launches outside the read-only tool. No model/provider is involved.
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-c",
        "import os, pathlib, sys, time, urllib.request\n"
        "while not pathlib.Path(sys.argv[1]).exists(): time.sleep(0.02)\n"
        "req = urllib.request.Request(sys.argv[2], "
        "headers={'Authorization': 'Bearer ' + os.environ['APP_TOKEN']})\n"
        "with urllib.request.urlopen(req, timeout=3) as response: assert response.status == 200\n"
        "print('Read-only tool completed.')\n",
        RELEASE,
        f"http://127.0.0.1:{PORT}/api/chats/{job['chat_id']}",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    record("tool", turn)
    yield {"type": "tool", "id": f"read-{turn}", "name": "bash", "args": {"command": "read-only wait"}}
    try:
        stdout, stderr = await process.communicate()
        if process.returncode != 0:
            raise RuntimeError(f"Read-only tool failed: {stderr.decode()}")
        yield {"type": "tool_done", "id": f"read-{turn}", "ok": True, "result": stdout.decode()}
        yield {"type": "delta", "text": f"Finished {turn}."}
        yield {"type": "result", "ok": True}
    finally:
        if process.returncode is None:
            process.kill()
            await process.wait()


def run():
    fake = FakeBroker()
    for name in dir(type(fake)):
        if not name.startswith("_"):
            setattr(clients.broker, name, getattr(fake, name))
    clients.broker.control_agent = control
    clients.broker.cleanup_chat_agent = cleanup
    catalog.model_info = model_info
    conversations.stream_agent = agent
    conversations.refine_chat_title = noop
    main.bootstrap = noop
    main.github_mentions.consume = noop
    main.mcp_servers.bootstrap_backend = noop
    main.runs.reconcile_schedules = noop
    DrainingServer(
        uvicorn.Config(main.app, host="127.0.0.1", port=int(PORT), timeout_graceful_shutdown=0.2)
    ).run()


if __name__ == "__main__":
    run()
