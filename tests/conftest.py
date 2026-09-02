"""Test-wide setup.

Every service reads its configuration from the environment when it is imported,
so the directories and tokens have to exist before the first service import.
"""

from __future__ import annotations

import os
import shutil
import tempfile
from collections.abc import Callable
from pathlib import Path

import httpx
import pytest

APP_TOKEN = "test-app-token"
INTERNAL_TOKEN = "test-internal-token"

_ROOT = Path(tempfile.mkdtemp(prefix="nautionette-tests-"))

os.environ.update(
    {
        "DATA_DIR": str(_ROOT / "data"),
        "ARTIFACTS_DIR": str(_ROOT / "artifacts"),
        "WORKFLOWS_DIR": str(_ROOT / "workflows"),
        "WORKFLOWS_SEED_DIR": str(_ROOT / "seed"),
        "APP_TOKEN": APP_TOKEN,
        "INTERNAL_TOKEN": INTERNAL_TOKEN,
        "APP_VERSION": "test",
        "AGENT_MODEL": "openai/gpt-4o-mini",
        "DEFAULT_AGENT_SET": "default",
        "AGENTGATEWAY_URL": "http://agentgateway.test:4000",
        "FRONTEND_WEB_URL": "http://frontend.test:80",
    }
)
for key in ("DATA_DIR", "ARTIFACTS_DIR", "WORKFLOWS_DIR", "WORKFLOWS_SEED_DIR"):
    Path(os.environ[key]).mkdir(parents=True, exist_ok=True)


@pytest.fixture(scope="session")
def repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


@pytest.fixture(scope="session", autouse=True)
def _discard_scratch_directory():
    yield
    shutil.rmtree(_ROOT, ignore_errors=True)


Handler = Callable[[httpx.Request], httpx.Response]


def json_body(payload: object, status: int = 200) -> Handler:
    return lambda _request: httpx.Response(status, json=payload)


def text_body(text: str, status: int = 200, **headers: str) -> Handler:
    return lambda _request: httpx.Response(status, text=text, headers=headers)


@pytest.fixture
def http(monkeypatch: pytest.MonkeyPatch) -> dict[str, Handler]:
    """Map a URL prefix to a handler. Anything unmapped fails to connect."""
    routes: dict[str, Handler] = {}

    async def send(_self, request: httpx.Request, **_kwargs):
        for prefix, handler in routes.items():
            if str(request.url).startswith(prefix):
                response = handler(request)
                response.request = request
                return response
        raise httpx.ConnectError(f"no route for {request.url}", request=request)

    monkeypatch.setattr(httpx.AsyncClient, "send", send)
    return routes
