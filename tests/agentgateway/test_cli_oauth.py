"""Real processes/sockets/browser hook and authenticated callback relay; no M365 account."""

import importlib.util
import json
import socket
import sys
import threading
import time
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlsplit
from urllib.request import Request, urlopen

import pytest

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location(
    "gateway_cli_oauth", ROOT / "services/agentgateway/cli_oauth.py"
)
cli = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = cli
spec.loader.exec_module(cli)


@pytest.fixture
def control(tmp_path):
    supervisor = cli.Supervisor(ttl=20)
    server = cli.ControlServer(("127.0.0.1", 0), "control-secret", supervisor)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server, supervisor, f"http://127.0.0.1:{server.server_port}"
    finally:
        supervisor.close()
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def request(base, path, method="GET", payload=None, token="control-secret"):  # noqa: S107 - test-only
    data = json.dumps(payload).encode() if payload is not None else None
    req = Request(  # noqa: S310 - fixed loopback test control server
        base + path,
        data=data,
        method=method,
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
    )
    with urlopen(req, timeout=10) as response:  # noqa: S310 - fixed loopback test control server
        return json.load(response)


def config(tmp_path, mode="login", **env):
    return {
        "command": sys.executable,
        "login_args": [str(Path(__file__).with_name("oauth-cli.py")), mode],
        "setup_args": [[str(Path(__file__).with_name("oauth-cli.py")), "setup"]],
        "env": {"HOME": str(tmp_path), **env},
        "provider": "workiq",
    }


def wait(supervisor, identifier, status="authorizing"):
    for _ in range(200):
        result = supervisor.get(identifier).public()
        if result["status"] == status:
            return result
        if result["status"] in {"failed", "expired", "cancelled"}:
            pytest.fail(str(result))
        time.sleep(0.025)
    pytest.fail("CLI did not reach " + status)


def forward(base, identifier, authorization, **fields):
    params = parse_qs(urlsplit(authorization["authorization_url"]).query)
    payload = {
        "capture_id": authorization["id"],
        "method": "POST" if authorization["response_mode"] == "form_post" else "GET",
        "fields": {"state": authorization["state"], "code": params["code_challenge"][0], **fields},
    }
    return request(base, f"/sessions/{identifier}/callback", "POST", payload), payload


@pytest.mark.parametrize("mode", ["form_post", "query"])
def test_real_login_and_callback_preserve_pkce_cli_owns_cache(control, tmp_path, mode):
    _, supervisor, base = control
    start = request(base, "/sessions", "POST", config(tmp_path, RESPONSE_MODE=mode, SECRET_VALUE="$literal"))
    result = wait(supervisor, start["id"])
    auth = result["authorization"]
    assert auth["port"] > 1023 and auth["response_mode"] == mode
    reply, submitted = forward(base, start["id"], auth)
    assert reply == {"status": "forwarded"}
    done = wait(supervisor, start["id"], "complete")
    public = json.dumps(done)
    assert "synthetic-private" not in public and "SECRET_VALUE" not in public
    assert (
        json.loads((tmp_path / "synthetic-cli-cache.json").read_text())["refresh_token"]
        == "synthetic-private-refresh"
    )
    # Lost responses can be checked with the same capture, but never replayed.
    assert request(base, f"/sessions/{start['id']}/callback", "POST", submitted) == reply
    submitted["fields"]["code"] = "different"
    with pytest.raises(HTTPError) as error:
        request(base, f"/sessions/{start['id']}/callback", "POST", submitted)
    assert error.value.code == 409


def test_authentication_is_required_even_on_health(control, tmp_path):
    _, _, base = control
    for method, path, body in [("GET", "/health", None), ("POST", "/sessions", config(tmp_path))]:
        with pytest.raises(HTTPError) as error:
            request(base, path, method, body, token="wrong")
        assert error.value.code == 401


def test_unowned_loopback_port_cannot_be_used_as_localhost_proxy(control, tmp_path):
    _, supervisor, base = control
    other = socket.socket()
    other.bind(("127.0.0.1", 0))
    other.listen()
    try:
        start = request(
            base, "/sessions", "POST", config(tmp_path, "unowned", UNOWNED_PORT=str(other.getsockname()[1]))
        )
        for _ in range(100):
            result = supervisor.get(start["id"]).public()
            if result["status"] == "failed":
                break
            time.sleep(0.025)
        assert result["status"] == "failed"
        assert "not CLI-owned" in result["message"]
        assert "authorization" not in result
    finally:
        other.close()


def test_state_method_destination_and_duplicate_fields_are_fenced(control, tmp_path):
    _, supervisor, base = control
    start = request(base, "/sessions", "POST", config(tmp_path))
    auth = wait(supervisor, start["id"])["authorization"]
    payload = {"capture_id": auth["id"], "method": "POST", "fields": {"state": "wrong", "code": "wrong"}}
    with pytest.raises(HTTPError) as error:
        request(base, f"/sessions/{start['id']}/callback", "POST", payload)
    assert error.value.code == 400
    payload["fields"]["state"] = auth["state"]
    payload["method"] = "GET"
    with pytest.raises(HTTPError) as error:
        request(base, f"/sessions/{start['id']}/callback", "POST", payload)
    assert error.value.code == 400
    payload["method"] = "POST"
    payload["fields"]["access_token"] = "must-not-be-forwarded"
    with pytest.raises(HTTPError) as error:
        request(base, f"/sessions/{start['id']}/callback", "POST", payload)
    assert error.value.code == 400
    assert not supervisor.get(start["id"]).captures[-1].used


def test_workiq_exit_zero_after_denial_is_failure_and_details_never_leak(control, tmp_path):
    _, supervisor, base = control
    start = request(base, "/sessions", "POST", config(tmp_path, "deny"))
    auth = wait(supervisor, start["id"])["authorization"]
    forward(base, start["id"], auth)
    for _ in range(100):
        result = supervisor.get(start["id"]).public()
        if result["status"] == "failed":
            break
        time.sleep(0.025)
    assert result["status"] == "failed"
    assert "private-detail" not in json.dumps(result)
    assert not (tmp_path / "synthetic-cli-cache.json").exists()


def test_cli_can_request_another_actual_port_and_complete(control, tmp_path):
    _, supervisor, base = control
    start = request(base, "/sessions", "POST", config(tmp_path, "retry"))
    auth = wait(supervisor, start["id"])["authorization"]
    forward(base, start["id"], auth)
    for _ in range(200):
        result = supervisor.get(start["id"]).public()
        if result.get("authorization", {}).get("id") not in {None, auth["id"]}:
            break
        time.sleep(0.025)
    again = result["authorization"]
    assert again["id"] != auth["id"]
    forward(base, start["id"], again)
    assert wait(supervisor, start["id"], "complete")["status"] == "complete"


def test_same_home_cannot_race_and_cancel_kills_login_group(control, tmp_path):
    _, supervisor, base = control
    start = request(base, "/sessions", "POST", config(tmp_path))
    wait(supervisor, start["id"])
    with pytest.raises(HTTPError) as error:
        request(base, "/sessions", "POST", config(tmp_path))
    assert error.value.code == 409
    process = supervisor.get(start["id"]).process
    request(base, f"/sessions/{start['id']}", "DELETE")
    process.wait(timeout=5)
    assert not cli.group_listeners(process)


def test_stdout_url_supported_and_native_only_flow_is_explicit_failure(control, tmp_path):
    _, supervisor, base = control
    start = request(base, "/sessions", "POST", config(tmp_path, "stdout"))
    auth = wait(supervisor, start["id"])["authorization"]
    forward(base, start["id"], auth)
    wait(supervisor, start["id"], "complete")
    start = request(base, "/sessions", "POST", config(tmp_path / "other", "native"))
    for _ in range(100):
        result = supervisor.get(start["id"]).public()
        if result["status"] == "failed":
            break
        time.sleep(0.025)
    assert result["status"] == "failed"
    assert "unsupported flow" in result["message"]


@pytest.mark.parametrize(
    "change",
    [
        {"command": "sh -c"},
        {"login_args": [123]},
        {"env": {"TOKEN": None}},
        {"setup_args": [[]] * 5},
    ],
)
def test_bad_launch_never_starts_a_process(control, tmp_path, change):
    _, _, base = control
    with pytest.raises(HTTPError) as error:
        request(base, "/sessions", "POST", {**config(tmp_path), **change})
    assert error.value.code == 400
