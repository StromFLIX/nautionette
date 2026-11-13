"""The staging probe touches only its UUID resources and never calls a model."""

import pytest
from nautionette_workflow_mcp.validate import run_checks

from scripts import check_workflow_lifecycle as smoke


class API:
    def __init__(self, environment="staging", lost_start=False):
        self.environment = environment
        self.lost_start = lost_start
        self.calls = []
        self.settings = {"disabled": False, "chat_id": None}
        self.schedule = None

    def call(self, path, method="GET", body=None, expected=200):
        self.calls.append((path, method, body))
        if path == "/api/system":
            return {"environment": self.environment}
        if path == "/api/workflows":
            return {"workflows": []}
        if path.endswith("/deploy"):
            name = path.split("/")[3]
            assert name.startswith("lifecycle_smoke_")
            checked = run_checks(name, body["code"])
            assert checked["valid"], checked
            assert "execute_activity" not in body["code"]
            return {"ready": True}
        if path.endswith("/schedule"):
            if expected == 409:
                assert self.settings["disabled"]
                return None
            self.schedule = {**body, "paused": False} if method == "POST" else None
        elif path.endswith("/settings"):
            self.settings.update(body)
            self.schedule["paused"] = body["disabled"]
        elif path.endswith("/run"):
            if expected == 409:
                assert self.settings["disabled"]
                return None
            if self.lost_start:
                raise RuntimeError("Reply lost")
            self.settings["chat_id"] = "owned-chat"
            return {"workflow_id": "owned-run"}
        elif path == "/api/runs/owned-run":
            return {"run": {"status": "completed"}}
        elif path == "/api/chats/owned-chat":
            return {
                "messages": [
                    {
                        "meta": {
                            "run": {"workflow_id": "owned-run"},
                            "origin": {
                                "relation": "result",
                                "workflow_id": "owned-run",
                            },
                        }
                    }
                ]
            }
        return {"settings": dict(self.settings), "schedule": self.schedule}


def test_probe_checks_disable_reenable_and_delivery_and_cleans_only_its_resources():
    api = API()
    smoke.check(api)
    assert api.calls[-2][:2] == ("/api/chats/owned-chat", "DELETE")
    assert api.calls[-1][1] == "DELETE"
    assert api.calls[-1][0].startswith("/api/workflows/lifecycle_smoke_")
    assert len([c for c in api.calls if c[0].endswith("/run") and c[2] == {}]) == 2  # refused + accepted


def test_probe_refuses_production_before_any_mutation():
    api = API(environment="production")
    with pytest.raises(RuntimeError, match="non-staging"):
        smoke.check(api)
    assert api.calls == [("/api/system", "GET", None)]


def test_probe_never_retries_an_ambiguous_start():
    api = API(lost_start=True)
    with pytest.raises(RuntimeError, match="Reply lost"):
        smoke.check(api)
    assert len([c for c in api.calls if c[0].endswith("/run")]) == 2  # refused + one lost reply
