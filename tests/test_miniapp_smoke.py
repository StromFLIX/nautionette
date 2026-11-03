"""Staging smoke never starts a release, calls a model or retries an unknown start."""

import json

import pytest
from nautionette_workflow_mcp.validate import run_checks

from scripts import check_miniapps as smoke


class FakeAPI:
    def __init__(self, *, conflict=False, lost=False):
        self.calls = []
        self.conflict = conflict
        self.lost = lost
        self.name = None
        self.request_ids = []

    def call(self, path, method="GET", body=None, expected=200):
        self.calls.append((path, method, body))
        if path == "/api/workflows":
            return {"workflows": []}
        if path.startswith("/api/workflows/"):
            self.name = path.split("/")[3]
            assert self.name.startswith("miniapp_smoke_")
            if method == "GET":
                assert expected == 404
            if method == "POST":
                checked = run_checks(self.name, body["code"])
                assert checked["valid"], checked
                assert '"notify":False' in body["code"]
                return {"ready": True}
            return {}
        if path == "/api/miniapps/contract":
            return {"example": {"title": "Counter", "frontend": {"html": ""}, "backend": {"source": ""}}}
        if path.endswith("/invoke"):
            if body["path"] == "/start":
                self.request_ids.append(body["request_id"])
                if self.lost:
                    raise RuntimeError("Reply lost")
                return {"structuredContent": {"result": json.dumps({"workflow_id": "test-run"})}}
            assert "request_id" not in body and "method" not in body  # GET: read-only, no mutation ID
            return {
                "content": [{"type": "text", "text": json.dumps({"runs": [{"workflow_id": "test-run"}]})}]
            }
        if path == "/api/runs/test-run":
            return {"temporal": {"status": "COMPLETED", "result": {"version": "minor"}}}
        if method == "PUT":
            assert body["expected_revision"] == 0
            assert all(self.name in json.dumps(g) for g in body["manifest"]["backend"]["permissions"]["mcp"])
            if self.conflict:
                raise smoke.APIError("Already exists", 409)
        return {"revision": 1}


def test_scoped_workflow_smoke_uses_valid_deterministic_source_and_cleans_up():
    api = FakeAPI()
    smoke.workflow_smoke(api)
    assert len(api.request_ids) == 2 and len(set(api.request_ids)) == 1
    assert any(
        path.startswith("/api/miniapps/ci-workflow-") and method == "DELETE" for path, method, _ in api.calls
    )
    assert api.calls[-1][:2] == ("/api/workflows/" + api.name, "DELETE")


def test_conflicting_app_is_never_archived_or_deleted():
    api = FakeAPI(conflict=True)
    with pytest.raises(smoke.APIError):
        smoke.workflow_smoke(api)
    assert not any(
        method == "PATCH" or (method == "DELETE" and "/miniapps/" in path) for path, method, _ in api.calls
    )


def test_lost_start_is_not_replayed():
    api = FakeAPI(lost=True)
    with pytest.raises(RuntimeError, match="Reply lost"):
        smoke.workflow_smoke(api)
    assert len(api.request_ids) == 1


@pytest.mark.parametrize(
    "error,category",
    [
        (
            "Docker network address pools are exhausted. Configure MINIAPP_NETWORK_CIDR with a reserved pool",
            "runtime_network_pool",
        ),
        ("Candidate health check failed; private-runtime-message", "runtime_health"),
        ("Rust compilation failed: private-source", "rust_compilation"),
        ("unknown-private-token", "build_or_health_unclassified"),
    ],
)
def test_failure_categories_never_expose_runtime_details(error, category):
    assert smoke.failure_category({"releases": [{"error": error}]}) == category
    assert "private" not in smoke.failure_category({"releases": [{"error": error}]})


def test_failed_first_publication_reports_safe_category_and_cleans_only_its_app(monkeypatch, capsys):
    class CounterAPI:
        calls = []

        def call(self, path, method="GET", body=None, expected=200):
            self.calls.append((path, method))
            if path == "/api/miniapps/contract":
                return {"example": {"title": "Counter"}}
            if path == "/api/miniapps/validate":
                return {"compiled": True, "artifact_bytes": 64}
            if method == "PUT":
                raise smoke.APIError("Unavailable", 422)
            if method == "GET":
                return {
                    "revision": 1,
                    "active_revision": None,
                    "releases": [
                        {"state": "failed", "error": "Configure MINIAPP_NETWORK_CIDR private-token"}
                    ],
                }
            return {}

    api = CounterAPI()
    monkeypatch.setattr(smoke.time, "sleep", lambda seconds: None)
    with pytest.raises(RuntimeError, match="runtime_network_pool"):
        smoke.run(api, "ci-smoke-test")
    assert "private-token" not in capsys.readouterr().out
    assert api.calls[-1] == ("/api/miniapps/ci-smoke-test?expected_revision=1", "DELETE")
    assert len([c for c in api.calls if c[1] == "PUT"]) == 1
