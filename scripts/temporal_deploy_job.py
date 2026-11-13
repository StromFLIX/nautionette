"""Restartable maintenance controller; no HTTP port, SSH key or public endpoint."""

from __future__ import annotations

import fcntl
import json
import os
import shutil
import subprocess
import sys
import time
import traceback
from pathlib import Path

import temporal_deploy_host as host


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
    while True:
        time.sleep(3600)


if __name__ == "__main__":
    if sys.argv[1] == "serve":
        serve()
    elif sys.argv[1] == "status":
        print(json.dumps(status()))
    elif sys.argv[1] == "finish":
        sys.path.insert(0, str(location()))
        host.finish(location())
    else:
        raise ValueError("Unknown controller operation")
