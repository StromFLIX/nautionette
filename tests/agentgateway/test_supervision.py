"""Gateway entrypoint restarts on helper failure and cleans up both process groups."""

import importlib.util
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location(
    "gateway_supervision", Path(__file__).resolve().parents[2] / "services/agentgateway/entrypoint.py"
)
entrypoint = importlib.util.module_from_spec(spec)
spec.loader.exec_module(entrypoint)


@pytest.mark.parametrize("configured", [True, False])
def test_private_helper_is_optional_but_failure_stops_gateway(monkeypatch, configured):
    monkeypatch.setenv("OTEL_SDK_DISABLED", "true")
    if configured:
        monkeypatch.setenv("CLI_OAUTH_TOKEN", "test-only-control-key")
    else:
        monkeypatch.delenv("CLI_OAUTH_TOKEN", raising=False)
    monkeypatch.setattr(entrypoint.sys, "argv", ["entrypoint", "-f", "/config/config.yaml"])
    children, commands, signals = [], [], []

    class Child:
        def __init__(self):
            self.pid = 100 + len(children)
            self.returncode = None
            children.append(self)

        def poll(self):
            return self.returncode

        def wait(self, timeout):
            self.returncode = self.returncode or -15
            return self.returncode

    def popen(command, **options):
        commands.append(command)
        assert options == {"start_new_session": True}
        assert "test-only-control-key" not in command
        return Child()

    def tick(_seconds):
        children[-1].returncode = 9

    monkeypatch.setattr(entrypoint.subprocess, "Popen", popen)
    monkeypatch.setattr(entrypoint.time, "sleep", tick)
    monkeypatch.setattr(entrypoint.signal, "signal", lambda *_args: None)
    monkeypatch.setattr(entrypoint.os, "umask", lambda *_args: None)
    monkeypatch.setattr(entrypoint.os, "killpg", lambda *args: signals.append(args))
    with pytest.raises(SystemExit) as error:
        entrypoint.main()
    assert error.value.code == 9
    assert len(commands) == (2 if configured else 1)
    if configured:
        assert commands[1][-1] == "/app/cli_oauth.py"
        assert signals and signals[0][0] == children[0].pid
