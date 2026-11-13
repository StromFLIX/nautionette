"""Restartable maintenance controller; no HTTP port, SSH key or public endpoint."""

from __future__ import annotations

import fcntl
import json
import os
import shutil
import signal
import subprocess
import sys
import time
import traceback
from pathlib import Path

if __package__:
    from nautionette.temporal_state import COMPLETED_RECEIPT

    from . import temporal_deploy_host as host
else:
    import temporal_deploy_host as host
    from temporal_state import COMPLETED_RECEIPT


def location():
    return host.ROOT / "runs" / host.storage.checked_id(os.environ["MAINTENANCE_RUN_ID"])


def status():
    path = location()
    if not (path / "state.json").exists():
        return {"status": "starting", "commit": os.environ["MAINTENANCE_COMMIT"]}
    state = host.load(path)
    return {
        key: state[key] for key in ("status", "commit", "target", "shards", "safe_to_retry") if key in state
    }


def write_completed_receipt():
    value = status()
    if value["status"] != "completed" or value["commit"] != os.environ["MAINTENANCE_COMMIT"]:
        raise RuntimeError("Cannot issue a receipt for incomplete maintenance")
    destination = Path(COMPLETED_RECEIPT)
    temporary = destination.with_suffix(".tmp")
    fd = os.open(temporary, os.O_CREAT | os.O_WRONLY | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as file:
        json.dump(value, file)
        file.flush()
        os.fsync(file.fileno())
    temporary.replace(destination)
    fd = os.open(destination.parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def verify_and_complete():
    # Compose reports 'started' before every service is ready for inspection.
    # This operation only verifies metadata/versions, restores restart policies
    # idempotently and writes the receipt; it never repeats schema migrations.
    deadline = time.monotonic() + 180
    while True:
        try:
            host.finish(location())
            write_completed_receipt()
            return
        except RuntimeError:
            if time.monotonic() >= deadline:
                raise
            time.sleep(2)


def serve():
    path = location()
    os.umask(0o077)
    host.ROOT.mkdir(parents=True, exist_ok=True, mode=0o700)
    host.ROOT.chmod(0o700)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        if path.exists():
            # Never repeat a possibly started schema mutation after a crash/reboot.
            if (path / "state.json").exists():
                state = host.load(path)
                if state["status"] != "completed":
                    host.storage.ROOT.mkdir(parents=True, exist_ok=True, mode=0o700)
                    with (host.storage.ROOT / "refresh.lock").open("a") as lock:
                        fcntl.flock(lock, fcntl.LOCK_EX)
                        host.recover(path, boot=True)
            else:
                raise RuntimeError("Incomplete controller initialization; inspect before retrying")
        else:
            shutil.copytree(Path(__file__).parent, path)
            # The child holds the shared refresh lock. Logs stay private on the host.
            with (path / "controller.log").open("a") as log:
                subprocess.run(  # noqa: S603 - fixed child operation and validated deployment IDs
                    [
                        sys.executable,
                        str(path / "temporal_deploy_host.py"),
                        "launch",
                        str(path),
                        os.environ["MAINTENANCE_APP"],
                        os.environ["MAINTENANCE_ENVIRONMENT"],
                        os.environ["MAINTENANCE_COMMIT"],
                    ],
                    stdout=log,
                    stderr=log,
                    timeout=100 * 60,
                    check=True,
                )
    except Exception:
        path.mkdir(parents=True, exist_ok=True, mode=0o700)
        with (path / "controller.log").open("a") as log:
            traceback.print_exc(file=log)
        if (path / "state.json").exists():
            try:
                host.recover(path, boot=True)
            except Exception:
                state = host.load(path)
                state["status"] = "needs_recovery"
                host.save(path, state)
        else:
            host.save(
                path, {"status": "failed", "safe_to_retry": True, "commit": os.environ["MAINTENANCE_COMMIT"]}
            )
    finally:
        if (
            (path / "state.json").exists()
            and host.load(path).get("stack")
            and host.load(path)["status"] != "completed"
        ):
            # Let the Coolify hook return. Reconciled workers cannot access the
            # stopped old server; intermediate servers existed only on data net.
            host.unpause_broker(host.load(path)["stack"])
    # The post-hook will retire this idle controller after publishing its receipt.
    # Handle SIGTERM so routine cleanup does not wait for Docker's kill timeout.
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
    while True:
        time.sleep(3600)


if __name__ == "__main__":
    if sys.argv[1] == "serve":
        serve()
    elif sys.argv[1] == "status":
        print(json.dumps(status()))
    elif sys.argv[1] == "finish":
        sys.path.insert(0, str(location()))
        verify_and_complete()
    else:
        raise ValueError("Unknown controller operation")
