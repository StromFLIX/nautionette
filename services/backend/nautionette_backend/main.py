"""The backend: the only service that publishes a port.

Auth, chats, workflows, triggers and the stream back to the clients all live
here. It never touches the Docker socket; the broker does that.

This module only assembles the app; every route lives in `routers/`.
"""

from __future__ import annotations

import asyncio
import contextlib
import shutil
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .config import settings
from .events import bus
from .integrations import bootstrap
from .routers import ROUTERS


def seed_workflows() -> None:
    """Copy committed workflows into the shared volume on first start."""
    source, target = Path(settings.seed_dir), Path(settings.workflows_dir)
    target.mkdir(parents=True, exist_ok=True)
    (target / ".drafts").mkdir(exist_ok=True)
    if not source.exists():
        return
    for item in source.glob("*.py"):
        destination = target / item.name
        if not destination.exists():
            shutil.copy2(item, destination)


@asynccontextmanager
async def lifespan(_: FastAPI):
    Path(settings.artifacts_dir).mkdir(parents=True, exist_ok=True)
    seed_workflows()
    # agentgateway may still be starting, so this retries in the background
    # rather than holding the port closed.
    integrations = asyncio.create_task(bootstrap())
    bus.publish("system.start", {"version": settings.version})
    yield
    integrations.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await integrations


app = FastAPI(
    title="Nautionette",
    version=settings.version,
    lifespan=lifespan,
    docs_url="/api/docs",
    openapi_url="/api/openapi.json",
)

# The packaged app runs on its own webview origin, so it is cross-origin to this
# API. Credentials stay off: every call carries a bearer token, never a cookie.
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

for router in ROUTERS:
    app.include_router(router)
