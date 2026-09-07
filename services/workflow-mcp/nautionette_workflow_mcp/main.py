"""workflow-mcp: the narrow door an agent writes code through.

Two faces on the same store:
* MCP at /mcp   - what agents call, through agentgateway.
* REST at /api  - validated deployment and optional legacy draft management.

Agent writes deploy directly after validation and return their diff and reload status.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI

from . import store, tools
from .api import router

logging.basicConfig(level=logging.INFO, format="%(asctime)s workflow-mcp %(levelname)s %(message)s")

mcp_app = tools.build_app()


@asynccontextmanager
async def lifespan(_: FastAPI):
    store.ensure_dirs()
    if mcp_app is None:
        yield
        return
    async with mcp_app.router.lifespan_context(mcp_app):
        yield


app = FastAPI(
    title="nautionette workflow-mcp",
    lifespan=lifespan,
    docs_url="/api/docs",
    openapi_url="/api/openapi.json",
)


@app.get("/healthz")
async def healthz() -> dict[str, Any]:
    return {
        "status": "ok",
        "mcp": mcp_app is not None,
        "workflows": len(store.list_workflows()),
        "drafts": len(store.list_drafts()),
        "workflows_dir": str(store.WORKFLOWS_DIR),
    }


app.include_router(router)

if mcp_app is not None:
    # Mounted last so the REST routes above win; MCP itself answers on /mcp.
    app.mount("/", mcp_app)
