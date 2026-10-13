import json

import httpx
import pytest
from nautionette_workflow_mcp import tools


@pytest.fixture
def registered():
    class Registry:
        def __init__(self):
            self.tools = {}

        def tool(self, **metadata):
            def register(function):
                self.tools[function.__name__] = function
                return function

            return register

    server = Registry()
    tools._register(server)
    return server.tools


async def test_write_deploys_through_backend_without_creating_a_draft(registered, workflows, monkeypatch):
    calls = []

    async def post(path, payload):
        calls.append((path, payload))
        return {"published": True, "ready": True, "name": "demo", "diff": "+code"}

    monkeypatch.setattr(tools.backend, "post", post)
    result = json.loads(await registered["write_workflow"]("demo", "code", "repair"))
    assert result["published"] and result["ready"]
    assert calls == [("/internal/workflows/demo/deploy", {"code": "code", "message": "repair"})]
    assert list((workflows / ".drafts").glob("*.py")) == []


async def test_run_starts_immediately_and_returns_the_run_id(registered, monkeypatch):
    async def post(path, payload):
        assert path == "/internal/workflows/demo/run"
        assert payload == {"input": {"value": 3}}
        return {"workflow_id": "demo-run"}

    monkeypatch.setattr(tools.backend, "post", post)
    assert json.loads(await registered["run_workflow"]("demo", {"value": 3})) == {"workflow_id": "demo-run"}


async def test_deploy_returns_validation_errors_for_the_agent_to_repair(registered, monkeypatch):
    async def post(path, payload):
        request = httpx.Request("POST", "http://backend/deploy")
        response = httpx.Response(
            400, request=request, json={"detail": {"validation": {"errors": ["bad code"]}}}
        )
        raise httpx.HTTPStatusError("invalid", request=request, response=response)

    monkeypatch.setattr(tools.backend, "post", post)
    result = json.loads(await registered["write_workflow"]("demo", "bad code"))
    assert result["ok"] is False
    assert result["error"]["validation"]["errors"] == ["bad code"]


async def test_invalid_names_are_not_forwarded_to_backend(registered):
    assert "error" in json.loads(await registered["run_workflow"]("../bad"))
    assert json.loads(await registered["write_workflow"]("../bad", "code"))["published"] is False
    assert "error" in json.loads(await registered["delete_workflow"]("../bad"))


async def test_delete_uses_backend_cleanup_instead_of_unlinking_directly(registered, workflows, monkeypatch):
    calls = []
    path = workflows / "demo.py"
    path.write_text("keep until backend cleanup succeeds")

    async def post(route, payload):
        calls.append((route, payload))
        return {"name": "demo", "deleted": True}

    monkeypatch.setattr(tools.backend, "post", post)
    assert json.loads(await registered["delete_workflow"]("demo")) == {"name": "demo", "deleted": True}
    assert calls == [("/internal/workflows/demo/delete", {})]
    assert path.exists()


async def test_delete_backend_failure_does_not_remove_the_file(registered, workflows, monkeypatch):
    path = workflows / "demo.py"
    path.write_text("keep for retry")

    async def post(route, payload):
        raise httpx.ConnectError("backend unavailable")

    monkeypatch.setattr(tools.backend, "post", post)
    result = json.loads(await registered["delete_workflow"]("demo"))
    assert result["ok"] is False
    assert path.exists()
