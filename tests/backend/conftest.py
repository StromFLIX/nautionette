"""Backend fixtures.

The app is exercised through a real TestClient with every outbound dependency
replaced by an in-memory fake, so a test never needs Docker, Temporal or a
network. Lifespan is deliberately not run: the startup task that bootstraps the
default integrations is tested on its own instead of racing every other test.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from fastapi.testclient import TestClient
from nautionette_backend import background, clients, main, output_storage, runtime
from nautionette_backend.db import db as database
from nautionette_backend.integrations.registry import INITIALIZED_SETTING

from ..conftest import APP_TOKEN, INTERNAL_TOKEN
from .fakes import FakeAuthoring, FakeBroker, FakeGateway, FakeModelCatalog, FakeTemporal
from .output_fakes import MemoryS3

_TABLES = (
    "dashboard_action_requests",
    "dashboard_action_runs",
    "dashboard_sources",
    "dashboard_revisions",
    "dashboards",
    "dashboard_components",
    "github_issue_chats",
    "github_mentions",
    "messages",
    "chats",
    "project_leases",
    "projects",
    "github_project_connections",
    "github_app_setups",
    "github_webhook_deliveries",
    "runs",
    "events",
    "settings",
    "agent_profiles",
    "pi_package_revisions",
    "pi_package_installations",
    "workflow_settings",
    "chat_output_uploads",
    "output_storage_objects",
    "output_cleanup_plans",
)


def _install(monkeypatch: pytest.MonkeyPatch, target: Any, fake: Any) -> Any:
    """Swap a singleton's methods for the fake's, leaving the object identity alone."""
    for name in dir(type(fake)):
        if not name.startswith("_"):
            monkeypatch.setattr(target, name, getattr(fake, name), raising=False)
    return fake


@pytest.fixture
def db(monkeypatch: pytest.MonkeyPatch):
    """The real SQLite database, emptied between tests, with the caches dropped."""
    for table in _TABLES:
        database.execute(f"DELETE FROM {table}")  # noqa: S608 - names come from the tuple above
    runtime.forget_catalog()
    runtime.model_windows.clear()
    # A request's own watcher is left behind on a loop that has already closed.
    background._running.clear()
    background._chat_turns.clear()
    monkeypatch.setattr(background, "_draining", False)
    monkeypatch.setattr(runtime, "_agent_answered", False)
    return database


@pytest.fixture(autouse=True)
def output_store(monkeypatch: pytest.MonkeyPatch):
    store = MemoryS3()
    monkeypatch.setattr(output_storage, "get_store", lambda: store)
    return store


@pytest.fixture
def gateway(monkeypatch: pytest.MonkeyPatch) -> FakeGateway:
    return _install(monkeypatch, clients.gateway, FakeGateway())


@pytest.fixture
def broker(monkeypatch: pytest.MonkeyPatch) -> FakeBroker:
    return _install(monkeypatch, clients.broker, FakeBroker())


@pytest.fixture
def authoring(monkeypatch: pytest.MonkeyPatch) -> FakeAuthoring:
    return _install(monkeypatch, clients.authoring, FakeAuthoring())


@pytest.fixture
def model_catalog(monkeypatch: pytest.MonkeyPatch) -> FakeModelCatalog:
    return _install(monkeypatch, clients.model_catalog, FakeModelCatalog())


@pytest.fixture
def temporal(monkeypatch: pytest.MonkeyPatch) -> FakeTemporal:
    return _install(monkeypatch, clients.temporal, FakeTemporal())


@pytest.fixture
def backend(db, gateway, broker, authoring, model_catalog, temporal) -> SimpleNamespace:
    """Every outbound dependency faked, with the default integrations already bootstrapped."""
    db.set_setting(INITIALIZED_SETTING, True)
    return SimpleNamespace(
        db=db,
        gateway=gateway,
        broker=broker,
        authoring=authoring,
        model_catalog=model_catalog,
        temporal=temporal,
    )


@pytest.fixture
def live() -> SimpleNamespace:
    """The singletons the fakes were installed onto.

    The app holds these objects, not the fakes, so a test that wants one call to
    fail -- or that sets a plain attribute the app reads, such as `last_error` --
    has to patch them here rather than on the fake.
    """
    return SimpleNamespace(
        gateway=clients.gateway,
        broker=clients.broker,
        authoring=clients.authoring,
        model_catalog=clients.model_catalog,
        temporal=clients.temporal,
    )


@pytest.fixture
def client(backend: SimpleNamespace) -> TestClient:
    """An authenticated client. Lifespan stays off; see the module docstring."""
    http = TestClient(main.app)
    http.headers["Authorization"] = f"Bearer {APP_TOKEN}"
    return http


@pytest.fixture
def anonymous(backend: SimpleNamespace) -> TestClient:
    return TestClient(main.app)


@pytest.fixture
def internal_headers() -> dict[str, str]:
    return {"X-Internal-Token": INTERNAL_TOKEN}
