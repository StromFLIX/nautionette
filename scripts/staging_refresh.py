"""GitHub runner transport for the host-side staging refresh. No app/chat calls."""

from __future__ import annotations

import json
import os
import re
import shlex
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from scripts.coolify_release import Coolify

ROOT = "/var/lib/nautionette-refresh/runs"
TERMINAL = {"completed", "dry_run_passed", "failed", "recovery_failed", "launch_failed"}


def required(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise ValueError(f"Configure {name} first")
    return value


def identifier(value: str) -> str:
    if not re.fullmatch(r"[a-zA-Z0-9_-]{1,100}", value):
        raise ValueError("Invalid resource/run identifier")
    return value


def configuration() -> tuple[str, str, str, str]:
    if required("GITHUB_REF") != "refs/heads/main":
        raise ValueError("Refresh may only run from main")
    prod = identifier(required("PRODUCTION_APPLICATION_UUID"))
    stage = identifier(required("STAGING_APPLICATION_UUID"))
    if prod == stage:
        raise ValueError("Production and staging must differ")
    dry_run = required("DRY_RUN")
    if dry_run not in {"true", "false"}:
        raise ValueError("DRY_RUN must be a boolean")
    operation = os.environ.get("OPERATION", "refresh")
    if operation not in {"refresh", "check-existing-run"}:
        raise ValueError("Unknown operation")
    if (
        operation == "refresh"
        and dry_run == "false"
        and os.environ.get("CONFIRMATION") != "OVERWRITE STAGING"
    ):
        raise ValueError(
            "Enter OVERWRITE STAGING to acknowledge replacing staging and a production write pause"
        )
    run_id = identifier(required("GITHUB_RUN_ID") + "-" + required("GITHUB_RUN_ATTEMPT"))
    if operation == "check-existing-run":
        return prod, stage, identifier(required("EXISTING_RUN_ID")), "check"
    return prod, stage, run_id, "dry-run" if dry_run == "true" else "apply"


def bootstrap(source: str, run_id: str, prod: str, stage: str, mode: str) -> str:
    """Transmit only reviewed source and resource identifiers, never credentials."""
    path = ROOT + "/" + identifier(run_id)
    return f"""import os, pathlib, runpy, sys
assert os.geteuid() == 0, 'root/passwordless sudo is required'
os.umask(0o077)
path = pathlib.Path({path!r})
path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
path.mkdir(mode=0o700)  # Refuse reuse; retries poll, never launch twice.
script = path / 'staging_refresh_host.py'
script.write_text({source!r})
sys.argv = [str(script), 'launch', str(path), {prod!r}, {stage!r}, {mode!r}]
runpy.run_path(str(script), run_name='__main__')
"""


class SSH:
    def __init__(self, folder: Path):
        host = required("REFRESH_SSH_HOST")
        user = required("REFRESH_SSH_USER")
        port = os.environ.get("REFRESH_SSH_PORT", "22") or "22"
        if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9.-]*", host):
            raise ValueError("SSH host must be a hostname or IPv4 address")
        if (
            not re.fullmatch(r"[a-z_][a-z0-9_-]*", user)
            or not port.isdecimal()
            or not 1 <= int(port) <= 65535
        ):
            raise ValueError("Invalid SSH user/port")
        key, known_hosts = folder / "key", folder / "known_hosts"
        key.write_text(required("REFRESH_SSH_PRIVATE_KEY") + "\n")
        known_hosts.write_text(required("REFRESH_SSH_KNOWN_HOSTS") + "\n")
        key.chmod(0o600)
        known_hosts.chmod(0o600)
        self.args = [
            "/usr/bin/ssh",
            "-T",
            "-p",
            port,
            "-i",
            str(key),
            "-o",
            "BatchMode=yes",
            "-o",
            "IdentitiesOnly=yes",
            "-o",
            "StrictHostKeyChecking=yes",
            "-o",
            "UserKnownHostsFile=" + str(known_hosts),
            "-o",
            "ConnectTimeout=20",
            "-o",
            "ServerAliveInterval=15",
            "-o",
            "ServerAliveCountMax=4",
            user + "@" + host,
        ]

    def call(self, args: list[str], source: str | None = None, timeout: int = 120) -> dict:
        # OpenSSH uses a remote shell: quote every argument, even after local validation.
        remote = shlex.join(["sudo", "-n", "/usr/bin/python3", *args])
        try:
            result = subprocess.run(  # noqa: S603 - fixed command + shell-quoted validated arguments
                [*self.args, remote],
                input=source,
                capture_output=True,
                text=True,
                timeout=timeout,
                check=True,
            )
            return json.loads(result.stdout)
        except (subprocess.SubprocessError, OSError, json.JSONDecodeError):
            raise RuntimeError(
                "SSH operation failed; inspect host run state (do not blindly relaunch)"
            ) from None


def guard_stage(api: Coolify, stage: str, run_id: str, clear: bool = False) -> None:
    path = f"/applications/{stage}"
    details = api.call(path)
    repo = str(details.get("git_repository", "")).removesuffix(".git").removeprefix("https://github.com/")
    if repo != required("GITHUB_REPOSITORY"):
        raise ValueError("Refresh guard application belongs to a different repository")
    description = str(details.get("description") or "")
    prefix = f"[nautionette-refresh:{run_id}] "
    if clear:
        if not description.startswith(prefix):
            raise RuntimeError("Maintenance guard changed; inspect before clearing it")
        updated = description.removeprefix(prefix)
    else:
        if description.startswith("[nautionette-refresh:"):
            raise RuntimeError("A refresh guard already exists; check its host run instead of copying again")
        updated = prefix + description
    api.call(path, "PATCH", {"description": updated})
    if api.call(path).get("description") != updated:
        raise RuntimeError("Maintenance guard update was not acknowledged; inspect before retrying")


def main() -> None:
    prod, stage, run_id, mode = configuration()
    source = Path(__file__).with_name("staging_refresh_host.py").read_text()
    with tempfile.TemporaryDirectory(prefix="nautionette-refresh-") as temp:
        ssh = SSH(Path(temp))
        path = ROOT + "/" + run_id
        api = Coolify() if mode != "dry-run" else None
        # Print before launch: an SSH acknowledgement can be lost even when the
        # detached job started. This ID is all an operator needs to poll it.
        print("Host refresh run ID: " + run_id, flush=True)
        if mode == "check":
            state = ssh.call([path + "/staging_refresh_host.py", "status", path])
        else:
            if api is not None:
                # Persistent guard survives workflow cancellation and SSH loss.
                # Never clear in finally: the detached host may still be copying.
                guard_stage(api, stage, run_id)
            # Preflight is read-only, but may need time for dataset-size checks.
            state = ssh.call(["-"], bootstrap(source, run_id, prod, stage, mode), timeout=1200)
        print("Refresh " + run_id + ": " + state["status"])
        deadline = time.monotonic() + 100 * 60
        while state["status"] not in TERMINAL and time.monotonic() < deadline:
            time.sleep(15)
            try:
                state = ssh.call([path + "/staging_refresh_host.py", "status", path])
            except RuntimeError:
                # Connection retries only: the detached operation is never retried.
                continue
        if state["status"] not in {"completed", "dry_run_passed"}:
            raise RuntimeError(f"Refresh not successful ({state['status']}); inspect host run {run_id}")
        if state["status"] == "completed" and api is not None:
            guard_stage(api, stage, run_id, clear=True)
        print("Refresh " + run_id + ": " + state["status"] + "; backups remain private on the host.")


if __name__ == "__main__":
    try:
        main()
    except (ValueError, RuntimeError) as exc:
        print(f"Refresh failed: {exc}", file=sys.stderr)
        sys.exit(1)
