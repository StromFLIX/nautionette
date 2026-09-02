"""In-memory stand-ins for everything the backend talks to.

They are behavioural, not mocks: the gateway fake keeps a resource store so a
write followed by a read behaves the way agentgateway does.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import httpx


def http_error(status: int, body: str = "") -> httpx.HTTPStatusError:
    request = httpx.Request("GET", "http://agentgateway.test/api")
    response = httpx.Response(status, text=body, request=request)
    return httpx.HTTPStatusError(f"HTTP {status}", request=request, response=response)


class FakeGateway:
    """agentgateway, with its config resource store held in a dict."""

    def __init__(self) -> None:
        self.storage_mode = "hybrid"
        # kind -> resource id -> value
        self.resources: dict[str, dict[str, dict[str, Any]]] = {}
        # Models /v1/models is willing to serve, beyond the integration wildcards.
        self.served_models: list[dict[str, Any]] = []
        # Targets and models owned by the gateway's config file, which the app cannot edit.
        self.file_targets: list[dict[str, str]] = []
        # instance -> the provider's own /models payload
        self.provider_payloads: dict[str, dict[str, Any]] = {}
        # url ("" means the federated endpoint) -> tools
        self.tools: dict[str, list[dict[str, str]]] = {"": []}
        self.test_result: dict[str, Any] = {
            "ok": True,
            "status": 200,
            "model": "",
            "message": "answered through agentgateway.",
        }
        self.writes: list[tuple[str, dict[str, Any]]] = []
        self.deletes: list[tuple[str, str]] = []
        self.fail_kinds: dict[str, Exception] = {}

    # ------------------------------------------------------------- the client API

    async def health(self) -> dict[str, Any]:
        return {"status": "ok", "code": 200}

    async def runtime(self) -> dict[str, Any]:
        return {"ui": {"configStoreMode": self.storage_mode}}

    async def config_resources(self, kind: str) -> list[dict[str, Any]]:
        return [{"id": key, "value": value} for key, value in self.resources.get(kind, {}).items()]

    async def put_config_resources(self, kind: str, values: list[dict[str, Any]]) -> None:
        if kind in self.fail_kinds:
            raise self.fail_kinds[kind]
        for value in values:
            self.writes.append((kind, value))
            self.resources.setdefault(kind, {})[value.get("id") or value["name"]] = value

    async def delete_config_resource(self, kind: str, resource_id: str) -> None:
        self.deletes.append((kind, resource_id))
        self.resources.get(kind, {}).pop(resource_id, None)

    async def config(self) -> dict[str, Any]:
        """The effective view: file-owned baseline plus whatever was written at runtime."""
        providers: list[str] = []
        routes: list[dict[str, str]] = []
        wildcard = False
        for identifier, value in self.resources.get("llm.model", {}).items():
            provider = value.get("provider")
            if isinstance(provider, dict):
                name = str(provider.get("reference") or next(iter(provider)))
            else:
                name = provider
            if name and name not in providers:
                providers.append(name)
            if name and isinstance(value.get("name"), str):
                routes.append({"name": value["name"], "provider": name, "id": identifier})
            if value.get("name") == "*":
                wildcard = True
        targets = [
            *self.file_targets,
            *(
                {"name": value["name"], "host": value["mcp"]["host"]}
                for value in self.resources.get("mcp.target", {}).values()
            ),
        ]
        return {
            "providers": providers,
            "wildcard_models": wildcard,
            "model_routes": routes,
            "targets": targets,
        }

    async def integration_models(self, instance: str) -> dict[str, Any]:
        if instance not in self.provider_payloads:
            raise http_error(404, "no route for that integration")
        return self.provider_payloads[instance]

    async def test_model(self, model: str, name: str, credential: str) -> dict[str, Any]:
        return {**self.test_result, "model": model}

    async def models(self) -> list[dict[str, Any]]:
        return list(self.served_models)

    async def mcp_tools(
        self, url: str | None = None, extra: dict[str, str] | None = None
    ) -> list[dict[str, str]]:
        key = url or ""
        if key not in self.tools:
            raise http_error(404, "not an MCP endpoint")
        return list(self.tools[key])


class FakeBroker:
    def __init__(self) -> None:
        self.sets: list[dict[str, Any]] = [{"name": "default", "image": "pi-agent:test", "ready": True}]
        self.events: list[dict[str, Any]] = [
            {"type": "delta", "text": "hello"},
            {"type": "result", "ok": True, "text": "hello", "output": None},
        ]
        self.jobs: list[dict[str, Any]] = []
        self.restarts = 0
        self.restart_error: Exception | None = None

    async def health(self) -> dict[str, Any]:
        return {"status": "ok"}

    async def agent_sets(self) -> list[dict[str, Any]]:
        return list(self.sets)

    async def run_agent(self, job: dict[str, Any], timeout: float = 900) -> AsyncIterator[dict[str, Any]]:
        self.jobs.append(job)
        for event in self.events:
            yield event

    async def restart_worker(self) -> dict[str, Any]:
        if self.restart_error:
            raise self.restart_error
        self.restarts += 1
        return {"restarted": ["worker-1"], "grace_seconds": 60}


class FakeAuthoring:
    """workflow-mcp's REST side, over a dict of workflow files."""

    def __init__(self) -> None:
        self.workflows: dict[str, dict[str, Any]] = {}
        self.drafts: dict[str, dict[str, Any]] = {}
        self.validation: dict[str, Any] = {"valid": True, "errors": [], "warnings": [], "steps": []}

    def add_workflow(self, name: str, **fields: Any) -> dict[str, Any]:
        entry = {
            "name": name,
            "title": fields.pop("title", name.replace("_", " ").title()),
            "description": "",
            "manifest": fields.pop("manifest", {"schema": 1, "name": name, "timeout_minutes": 30}),
            "code": fields.pop("code", "MANIFEST = {}\n"),
            **fields,
        }
        self.workflows[name] = entry
        return entry

    async def health(self) -> dict[str, Any]:
        return {"status": "ok"}

    async def list_workflows(self) -> list[dict[str, Any]]:
        return [dict(entry) for entry in self.workflows.values()]

    async def get_workflow(self, name: str) -> dict[str, Any]:
        if name not in self.workflows:
            raise RuntimeError(f"workflow '{name}' does not exist")
        return dict(self.workflows[name])

    async def list_drafts(self) -> list[dict[str, Any]]:
        return [dict(entry) for entry in self.drafts.values()]

    async def get_draft(self, name: str) -> dict[str, Any]:
        return dict(self.drafts[name])

    async def validate(self, name: str, code: str) -> dict[str, Any]:
        return dict(self.validation)

    async def write_draft(self, name: str, code: str, message: str = "") -> dict[str, Any]:
        draft = {"name": name, "code": code, "message": message, "diff": f"+++ {name}.py", "is_new": True}
        self.drafts[name] = draft
        return dict(draft)

    async def publish(self, name: str) -> dict[str, Any]:
        draft = self.drafts.pop(name)
        self.add_workflow(name, code=draft["code"])
        return {"name": name, "published": True, "diff": draft["diff"]}

    async def discard(self, name: str) -> dict[str, Any]:
        self.drafts.pop(name, None)
        return {"name": name, "discarded": True}

    async def delete_workflow(self, name: str) -> dict[str, Any]:
        self.workflows.pop(name, None)
        return {"name": name, "deleted": True}


class FakeModelCatalog:
    def __init__(self) -> None:
        self.payloads: dict[str, dict[str, Any]] = {}

    async def payload(self, url: str) -> dict[str, Any]:
        if url not in self.payloads:
            raise http_error(404, "no catalog there")
        return self.payloads[url]


class FakeTemporal:
    def __init__(self) -> None:
        self.last_error: str | None = None
        self.up = True
        self.started: list[dict[str, Any]] = []
        self.executions: dict[str, dict[str, Any]] = {}
        self.results: dict[str, Any] = {}
        self.histories: dict[str, list[dict[str, Any]]] = {}
        self.schedule_specs: dict[str, dict[str, Any]] = {}
        self.cancelled: list[str] = []
        self.terminated: list[tuple[str, str]] = []

    async def healthy(self) -> bool:
        return self.up

    async def start(
        self, workflow: str, workflow_id: str, payload: dict[str, Any], timeout_minutes: int = 30
    ) -> dict[str, Any]:
        self.started.append(
            {"workflow": workflow, "workflow_id": workflow_id, "input": payload, "timeout": timeout_minutes}
        )
        self.executions[workflow_id] = {
            "workflow_id": workflow_id,
            "run_id": "run-1",
            "workflow_type": workflow,
            "status": "RUNNING",
            "start_time": "2026-01-01T00:00:00+00:00",
            "close_time": None,
        }
        return {"workflow_id": workflow_id, "run_id": "run-1"}

    async def result(self, workflow_id: str, timeout: float = 5.0) -> Any:
        if workflow_id not in self.results:
            raise TimeoutError("no result yet")
        return self.results[workflow_id]

    async def describe(self, workflow_id: str) -> dict[str, Any]:
        if workflow_id not in self.executions:
            raise RuntimeError("no such workflow")
        return dict(self.executions[workflow_id])

    async def recent(self, limit: int = 50) -> list[dict[str, Any]]:
        return [dict(item) for item in list(self.executions.values())[:limit]]

    async def history(self, workflow_id: str, limit: int = 200) -> list[dict[str, Any]]:
        return list(self.histories.get(workflow_id, []))[:limit]

    async def cancel(self, workflow_id: str) -> None:
        self.cancelled.append(workflow_id)

    async def terminate(self, workflow_id: str, reason: str = "terminated from the app") -> None:
        self.terminated.append((workflow_id, reason))

    async def set_schedule(
        self, workflow: str, cron: str, payload: dict[str, Any], paused: bool = False
    ) -> dict[str, Any]:
        self.schedule_specs[workflow] = {"cron": cron, "input": payload, "paused": paused}
        return {"schedule_id": f"schedule-{workflow}", "cron": cron, "paused": paused}

    async def delete_schedule(self, workflow: str) -> None:
        self.schedule_specs.pop(workflow, None)

    async def schedules(self) -> list[dict[str, Any]]:
        return [
            {"id": f"schedule-{name}", "workflow": name, "cron": spec["cron"], "paused": spec["paused"]}
            for name, spec in self.schedule_specs.items()
        ]
