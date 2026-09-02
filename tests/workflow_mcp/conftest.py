"""workflow-mcp fixtures: a workflow store rooted in a temporary directory."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from nautionette_workflow_mcp import main, store

GOOD_WORKFLOW = '''"""A workflow that passes every check."""

from datetime import timedelta

from temporalio import workflow

MANIFEST = {
    "schema": 1,
    "name": "url_digest",
    "title": "URL digest",
    "description": "Summarise one page.",
    "inputs": {"type": "object", "properties": {"url": {"type": "string"}}, "required": ["url"]},
    "outputs": {"type": "object", "properties": {"summary": {"type": "string"}}},
    "agent_set": "default",
}


@workflow.defn(name="url_digest")
class UrlDigest:
    @workflow.run
    async def run(self, params: dict) -> dict:
        page = await workflow.execute_activity(
            "http_fetch",
            {"url": params["url"]},
            start_to_close_timeout=timedelta(minutes=5),
        )
        return {"summary": page["body"][:200]}
'''


@pytest.fixture
def workflows(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "workflows"
    monkeypatch.setattr(store, "WORKFLOWS_DIR", root)
    monkeypatch.setattr(store, "DRAFTS_DIR", root / ".drafts")
    store.ensure_dirs()
    return root


@pytest.fixture
def client(workflows: Path) -> TestClient:
    return TestClient(main.app)
