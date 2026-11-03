import io
import json
import runpy
import urllib.error

import pytest

from scripts import check_artcraft_deployment as checks


def test_artifact_readiness_diagnostics_exclude_arbitrary_errors_and_credentials(repo_root):
    diagnostic = runpy.run_path(str(repo_root / "scripts/check_artcraft_deployment.py"))["diagnostic"]
    result = diagnostic(
        {
            "components": [
                {
                    "name": "broker",
                    "status": "degraded",
                    "detail": {
                        "image_status": "failed",
                        "error": "private-token: all predefined address pools have been fully subnetted",
                        "workers": {"status": "ready"},
                    },
                }
            ],
            "agent_sets": [{"ready": False}],
        }
    )
    assert result["error_categories"] == ["network_address_pool"]
    assert result["workers_ready"] and not result["agent_sets_ready"]
    assert "private-token" not in str(result)
    assert diagnostic({})["broker_status"] == "unknown"


def system_state(*, worker=True, artifacts=True):
    return {
        "components": [
            {
                "name": "broker",
                "status": "ok" if worker and artifacts else "degraded",
                "detail": {
                    "image_status": "ready" if artifacts else "failed",
                    "workers": {"status": "ready" if worker else "starting"},
                    "error": None,
                },
            }
        ],
        "agent_sets": [{"ready": artifacts}],
    }


@pytest.fixture
def clock(monkeypatch):
    now = {"value": 0}
    monkeypatch.setattr(checks.time, "monotonic", lambda: now["value"])
    monkeypatch.setattr(checks.time, "sleep", lambda seconds: now.update(value=now["value"] + seconds))
    return now


def test_readiness_waits_for_worker_recovery_without_relaxing_artifact_checks(clock, capsys):
    states = iter([system_state(worker=False), system_state(worker=False), system_state()])
    calls = []

    def fetch(timeout):
        calls.append(timeout)
        return next(states)

    assert checks.wait_for_ready(fetch, timeout=60)["workers_ready"]
    assert len(calls) == 3 and clock["value"] == 10
    assert len(capsys.readouterr().out.splitlines()) == 2  # status changes only


@pytest.mark.parametrize("component", ["worker", "artifacts"])
def test_readiness_never_turns_a_persistent_failure_into_success(clock, component):
    def fetch(timeout):
        return system_state(**{component: False})

    with pytest.raises(RuntimeError, match="timed out"):
        checks.wait_for_ready(fetch, timeout=16)
    assert clock["value"] == 16


def test_transient_transport_failure_is_retried_without_logging_private_details(clock, capsys):
    calls = []

    def fetch(timeout):
        calls.append(timeout)
        if len(calls) == 1:
            raise RuntimeError("private-service-credential")
        return system_state()

    assert checks.wait_for_ready(fetch, timeout=10)["broker_status"] == "ok"
    assert len(calls) == 2 and "private-service-credential" not in capsys.readouterr().out


@pytest.mark.parametrize("status", [401, 403])
def test_authentication_failures_fail_closed_without_retries_or_body_logging(clock, capsys, status):
    calls = []

    def fetch(timeout):
        calls.append(timeout)
        raise urllib.error.HTTPError("https://stage.example", status, "private-auth-message", None, None)

    with pytest.raises(RuntimeError, match="authentication failed"):
        checks.wait_for_ready(fetch)
    assert len(calls) == 1 and clock["value"] == 0
    assert "private-auth-message" not in capsys.readouterr().out


def test_main_only_retries_authenticated_read_only_status_requests(clock, monkeypatch, capsys):
    monkeypatch.setenv("APP_URL", "https://stage.example")
    monkeypatch.setenv("APP_TOKEN", "private-token")
    states = iter([system_state(worker=False), system_state()])
    calls = []

    class Opener:
        def open(self, request, timeout):
            assert request.get_method() == "GET" and request.data is None
            assert request.full_url == "https://stage.example/api/system"
            assert request.get_header("Authorization") == "Bearer private-token"
            calls.append(timeout)
            return io.BytesIO(json.dumps(next(states)).encode())

    monkeypatch.setattr(checks.urllib.request, "build_opener", lambda *args: Opener())
    checks.main()
    assert len(calls) == 2 and "private-token" not in capsys.readouterr().out
