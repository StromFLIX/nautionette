"""Host-side, explicit staging refresh. Invoked by staging_refresh.py over SSH.

Requires root, Docker, Python 3.11+, systemd and GNU du on the deployment host.
No service credentials leave the host. Production volumes are only mounted RO
in copy helpers. A systemd ExecStopPost recovers production after any exit,
including timeout/SIGKILL/disconnected runners. A failed restore stays offline.
"""

from __future__ import annotations

import asyncio
import fcntl
import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path("/var/lib/nautionette-refresh")
SERVICES = {
    "backend",
    "docker-broker",
    "worker",
    "workflow-mcp",
    "agentgateway",
    "frontend-web",
    "website",
    "temporal",
    "postgres",
}
START_ORDER = [
    "postgres",
    "temporal",
    "workflow-mcp",
    "agentgateway",
    "frontend-web",
    "website",
    "worker",
    "docker-broker",
    "backend",
]
STOP_ORDER = [
    "docker-broker",
    "backend",
    "worker",
    "workflow-mcp",
    "agentgateway",
    "frontend-web",
    "website",
    "temporal",
    "postgres",
]
DATASETS = {
    "backend-data": ("backend", "/data"),
    "agentgateway-data": ("agentgateway", "/data"),
    "postgres-data": ("postgres", "/var/lib/postgresql/data"),
    "workflows": ("backend", "/workflows"),
    "artifacts": ("backend", "/artifacts"),
    "projects": ("backend", "/projects"),
}


def checked_id(value: str) -> str:
    if not re.fullmatch(r"[a-zA-Z0-9_-]{1,100}", value):
        raise ValueError("Invalid resource/run identifier")
    return value


def command(*args: str, timeout: int = 120) -> str:
    # Never forward Docker/API output: inspect contains environment credentials.
    try:
        result = subprocess.run(args, capture_output=True, text=True, timeout=timeout, check=True)  # noqa: S603
        return result.stdout.strip()
    except (subprocess.SubprocessError, OSError):
        raise RuntimeError(f"Host operation failed ({Path(args[0]).name}); inspect on host") from None


def docker(*args: str, timeout: int = 120) -> str:
    return command("/usr/bin/docker", *args, timeout=timeout)


def containers() -> list[dict]:
    ids = docker("ps", "-aq").split()
    return json.loads(docker("inspect", *ids)) if ids else []


def env(container: dict) -> dict[str, str]:
    return dict(item.split("=", 1) for item in container["Config"].get("Env", []) if "=" in item)


def labels(container: dict) -> dict:
    return container["Config"].get("Labels") or {}


def service(container: dict) -> str:
    return labels(container).get("com.docker.compose.service", "")


def mounted(container: dict) -> set[str]:
    return {m["Name"] for m in container.get("Mounts", []) if m["Type"] == "volume"}


def volume(container: dict, destination: str) -> str:
    matches = [m for m in container["Mounts"] if m["Destination"] == destination]
    if len(matches) != 1 or matches[0]["Type"] != "volume":
        raise RuntimeError("Expected a unique named volume; bind mounts are not supported")
    return checked_id(matches[0]["Name"])


def inventory(rows: list[dict], app: str, environment: str) -> dict:
    members = [
        r for r in rows if labels(r).get("com.docker.compose.project") == app and service(r) in SERVICES
    ]
    groups = {name: [r for r in members if service(r) == name] for name in SERVICES}
    if any(not group for group in groups.values()) or any(
        len(group) != 1 for name, group in groups.items() if name != "worker"
    ):
        raise RuntimeError("Expected one complete Compose stack (workers may have replicas)")
    if any(r["State"]["Status"] != "running" or r["State"].get("Paused") for r in members):
        raise RuntimeError("All stack services must be running before a refresh")
    backend = groups["backend"][0]
    settings = env(backend)
    if settings.get("APP_ENVIRONMENT") != environment or not settings.get("APP_TOKEN"):
        raise RuntimeError("Environment identity/authentication check failed")
    volumes = {name: volume(groups[svc][0], dest) for name, (svc, dest) in DATASETS.items()}
    if len(set(volumes.values())) != len(DATASETS):
        raise RuntimeError("Datasets unexpectedly share volumes")
    broker = groups["docker-broker"][0]
    if env(broker).get("WORKFLOWS_VOLUME") != volumes["workflows"]:
        raise RuntimeError("Broker workflow volume is not the backend workflow volume")
    internal = env(broker).get("TARGET_NETWORK", "")
    if not internal or internal not in groups["temporal"][0]["NetworkSettings"]["Networks"]:
        raise RuntimeError("Cannot establish the isolated Temporal network")
    return {
        "app": app,
        "volumes": volumes,
        "network": checked_id(internal),
        "backend": backend["Id"],
        "copy_image": backend["Image"],
        "worker_image": groups["worker"][0]["Image"],
        "postgres_image": groups["postgres"][0]["Image"],
        "containers": [
            {"id": r["Id"], "service": service(r), "restart": r["HostConfig"]["RestartPolicy"]}
            for r in members
        ],
    }


def assert_isolated(rows: list[dict], production: dict, staging: dict) -> None:
    source, target = set(production["volumes"].values()), set(staging["volumes"].values())
    if production["app"] == staging["app"] or source & target or production["network"] == staging["network"]:
        raise RuntimeError("Production and staging are not isolated")
    if production["postgres_image"] != staging["postgres_image"]:
        raise RuntimeError("Physical PostgreSQL copy requires the same Postgres image ID")
    for stack, forbidden in [(production, target), (staging, source)]:
        owned = {r["id"] for r in stack["containers"]}
        for row in rows:
            if row["Id"] in owned:
                if mounted(row) & forbidden:
                    raise RuntimeError("A stack service mounts the other environment's data")
            elif row["State"]["Status"] in {"running", "paused", "restarting", "created"}:
                networks = row.get("NetworkSettings", {}).get("Networks", {})
                agent_on_network = "nautionette.chat" in labels(row) and stack["network"] in networks
                if mounted(row) & set(stack["volumes"].values()) or agent_on_network:
                    raise RuntimeError(
                        "Another container/agent uses this stack; finish or stop its work first"
                    )


def assert_password_compatible(rows: list[dict], production: dict, staging: dict) -> None:
    # A physical copy also copies database role passwords, not Coolify env vars.
    pg_env = []
    for stack in (production, staging):
        pg = next(
            r
            for r in rows
            if r["Id"] == next(c["id"] for c in stack["containers"] if c["service"] == "postgres")
        )
        pg_env.append(env(pg))
    if any(pg_env[0].get(k) != pg_env[1].get(k) for k in ("POSTGRES_USER", "POSTGRES_PASSWORD")):
        raise RuntimeError(
            "PostgreSQL role configuration differs; physical restore would break staging login"
        )


def save(path: Path, state: dict) -> None:
    temp = path / "state.tmp"
    with temp.open("w") as file:
        json.dump(state, file)
        file.flush()
        os.fsync(file.fileno())
    temp.replace(path / "state.json")
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def load(path: Path) -> dict:
    return json.loads((path / "state.json").read_text())


def free_space(path: Path, production: dict, staging: dict) -> None:
    names = list(production["volumes"].values()) + list(staging["volumes"].values())
    mounts = [Path(v["Mountpoint"]) for v in json.loads(docker("volume", "inspect", *names))]
    size = sum(int(command("/usr/bin/du", "-sb", "--", str(m), timeout=600).split()[0]) for m in mounts)
    # Conservative allowance for both backups and replacement staging data,
    # including cross-filesystem destinations. Reflinks/compression not assumed.
    required = size * 2 + 512 * 1024 * 1024
    if any(shutil.disk_usage(m).free < required for m in [path, *mounts]):
        raise RuntimeError("Insufficient free disk for backups and staging restoration")


def health(stack: dict, expected: str, attempts: int = 60) -> None:
    code = """import json, os, urllib.request
headers = {'Authorization': 'Bearer '+os.environ['APP_TOKEN']}
r = urllib.request.Request('http://127.0.0.1:8080/api/system', headers=headers)
s = json.load(urllib.request.urlopen(r, timeout=15))
assert s['environment'] == os.environ['APP_ENVIRONMENT'] == EXPECTED
assert s['auth_enabled'] is True
assert {c['name'] for c in s['components']} == {'temporal', 'broker', 'agentgateway', 'workflow-mcp'}
assert all(c['status'] == 'ok' for c in s['components'])
""".replace("EXPECTED", repr(expected))
    for attempt in range(attempts):
        try:
            docker("exec", stack["backend"], "python", "-c", code, timeout=30)
            return
        except RuntimeError:
            if attempt == attempts - 1:
                raise RuntimeError(f"{expected} authenticated health checks failed") from None
            time.sleep(5)


def stop(stack: dict) -> None:
    # Persisted original policies are restored on recovery. Disable Docker's own
    # restart loop before stopping; never remove service containers or volumes.
    for item in stack["containers"]:
        docker("update", "--restart=no", item["id"])
    for name in STOP_ORDER:
        for item in stack["containers"]:
            if item["service"] == name:
                docker("stop", "--time", "90", item["id"], timeout=120)
                info = json.loads(docker("inspect", item["id"]))[0]
                if info["State"]["Running"] or info["State"].get("ExitCode") == 137:
                    raise RuntimeError("Service did not shut down cleanly; no snapshot will be taken")


def start(stack: dict, only: set[str] | None = None) -> None:
    for name in START_ORDER:
        if only is not None and name not in only:
            continue
        for item in stack["containers"]:
            if item["service"] == name:
                docker("start", item["id"])
                policy = item["restart"]
                value = policy["Name"] or "no"
                if value == "on-failure" and policy.get("MaximumRetryCount"):
                    value += ":" + str(policy["MaximumRetryCount"])
                docker("update", "--restart=" + value, item["id"])


def helper(path: Path, image: str, mode: str, mounts: list[str], network: str = "none") -> None:
    name = "nautionette-refresh-" + path.name
    # The fixed name is also removed by ExecStopPost, before production resumes.
    args = [
        "run",
        "-d",
        "--name",
        name,
        "--network",
        network,
        "--user",
        "0:0",
        "--security-opt",
        "no-new-privileges:true",
        "--entrypoint",
        "python",
        "--mount",
        f"type=bind,src={path},dst=/refresh,readonly",
    ]
    for mount in mounts:
        args.extend(["--mount", mount])
    docker(*args, image, "/refresh/staging_refresh_host.py", mode)
    result = docker("wait", name, timeout=5400)
    if result != "0":
        raise RuntimeError(f"{mode} helper failed; staging remains protected; inspect helper logs on host")
    docker("rm", name)


def copy_datasets(path: Path, stack: dict, backup: str, restore: bool = False) -> None:
    for dataset, name in stack["volumes"].items():
        folder = path / backup / dataset
        folder.mkdir(parents=True, exist_ok=True, mode=0o700)
        if restore:
            mounts = [f"type=bind,src={folder},dst=/src,readonly", f"type=volume,src={name},dst=/dst"]
        else:
            mounts = [f"type=volume,src={name},dst=/src,readonly", f"type=bind,src={folder},dst=/dst"]
        helper(path, stack["copy_image"], "copy", mounts)


def verify_stopped(stack: dict) -> None:
    rows = containers()
    if any(r["State"]["Running"] and mounted(r) & set(stack["volumes"].values()) for r in rows):
        raise RuntimeError("A writer appeared after shutdown; refusing to copy live data")


def recover(path: Path) -> None:
    state = load(path)
    if state.get("status") == "completed" or state.get("recovery_complete"):
        return
    if not state.get("touched"):
        state["status"] = "failed"
        state["recovery_complete"] = True
        save(path, state)
        return
    # No outstanding helper may read production while production is restarted,
    # or continue restoring staging after recovery decides what is safe to run.
    name = "nautionette-refresh-" + path.name
    if docker("ps", "-aq", "--filter", "name=^/" + name + "$"):
        docker("rm", "-f", name)
    errors = []
    for key, expected in [("production", "production"), ("staging", "staging")]:
        try:
            if key == "staging" and state.get("restore_started") and not state.get("quarantined"):
                # Keep *all* copied writers stopped even after a host reboot.
                stop(state[key])
            else:
                start(state[key])
                health(state[key], expected)
        except RuntimeError:
            errors.append(key)
    state.setdefault("failure_phase", state["status"])
    state["status"] = "recovery_failed" if errors else "failed"
    state["recovery_errors"] = errors
    state["recovery_complete"] = not errors
    save(path, state)
    if errors:
        raise RuntimeError("Recovery needs operator attention on host")


def run(path: Path) -> None:
    state = load(path)
    production, staging = state["production"], state["staging"]
    # Fresh inventory after entering the detached unit, before any write pause.
    rows = containers()
    fresh_prod = inventory(rows, production["app"], "production")
    fresh_stage = inventory(rows, staging["app"], "staging")
    if fresh_prod != production or fresh_stage != staging:
        raise RuntimeError("Containers changed since preflight; rerun with a new run ID")
    assert_isolated(rows, production, staging)
    state["touched"] = True
    save(path, state)
    try:
        state["status"] = "backing_up_staging"
        save(path, state)
        stop(staging)
        verify_stopped(staging)
        copy_datasets(path, staging, "staging-before")
        state["staging_backup_complete"] = True
        state["status"] = "snapshotting_production"
        save(path, state)
        stop(production)
        verify_stopped(production)
        copy_datasets(path, production, "production-snapshot")
        state["snapshot_complete"] = True
        save(path, state)
        start(production)
        health(production, "production")
        state["production_resumed"] = True
        state["restore_started"] = True
        state["status"] = "restoring_staging"
        save(path, state)
        verify_stopped(staging)
        copy_datasets(path, staging, "production-snapshot", restore=True)
        start(staging, {"postgres", "temporal"})
        state["status"] = "quarantining_staging"
        save(path, state)
        helper(path, staging["worker_image"], "quarantine", [], staging["network"])
        state["quarantined"] = True
        save(path, state)
        start(staging)
        health(staging, "staging")
        health(production, "production")
        state["status"] = "completed"
        save(path, state)
    finally:
        if load(path).get("status") != "completed":
            recover(path)


def digest(path: Path) -> str:
    with path.open("rb") as file:
        return hashlib.file_digest(file, "sha256").hexdigest()


def copy_tree(source: Path, target: Path, hardlinks: dict | None = None) -> None:
    """Copy trusted local trees, including external symlinks, without following them."""
    if hardlinks is None:
        hardlinks = {}
    info = source.lstat()
    if stat.S_ISLNK(info.st_mode):
        target.symlink_to(os.readlink(source))
    elif stat.S_ISDIR(info.st_mode):
        target.mkdir(exist_ok=True)
        for child in source.iterdir():
            copy_tree(child, target / child.name, hardlinks)
    elif stat.S_ISREG(info.st_mode):
        inode = (info.st_dev, info.st_ino)
        if inode in hardlinks:
            os.link(hardlinks[inode], target)
        else:
            shutil.copyfile(source, target, follow_symlinks=False)
            hardlinks[inode] = target
    else:
        raise RuntimeError("Unsupported special file in snapshot")
    os.chown(target, info.st_uid, info.st_gid, follow_symlinks=False)
    shutil.copystat(source, target, follow_symlinks=False)


def compare_tree(source: Path, target: Path) -> None:
    left, right = source.lstat(), target.lstat()
    if (left.st_mode, left.st_uid, left.st_gid) != (right.st_mode, right.st_uid, right.st_gid):
        raise RuntimeError("Snapshot metadata mismatch")
    if source.is_symlink():
        if os.readlink(source) != os.readlink(target):
            raise RuntimeError("Snapshot symlink mismatch")
    elif source.is_dir():
        if {p.name for p in source.iterdir()} != {p.name for p in target.iterdir()}:
            raise RuntimeError("Snapshot directory mismatch")
        for child in source.iterdir():
            compare_tree(child, target / child.name)
    elif left.st_size != right.st_size or digest(source) != digest(target):
        raise RuntimeError("Snapshot content mismatch")


def copy_helper() -> None:
    source, target = Path("/src"), Path("/dst")
    # shutil.rmtree is fd-based on Linux. Never recurse through a symlink.
    for child in target.iterdir():
        if child.is_dir() and not child.is_symlink():
            shutil.rmtree(child)
        else:
            child.unlink()
    copy_tree(source, target)
    compare_tree(source, target)
    os.sync()


async def quarantine() -> None:
    # Only the worker image needs the SDK. The host uses the standard library.
    from temporalio.api.workflowservice.v1 import ListNamespacesRequest
    from temporalio.client import Client

    client = None
    for attempt in range(90):
        try:
            client = await Client.connect("temporal:7233")
            break
        except Exception:
            if attempt == 89:
                raise RuntimeError("Staging Temporal did not become ready") from None
            await asyncio.sleep(5)
    assert client is not None
    namespaces = []
    token = b""
    while True:
        response = await client.workflow_service.list_namespaces(
            ListNamespacesRequest(page_size=100, next_page_token=token)
        )
        namespaces.extend(
            n.namespace_info.name for n in response.namespaces if n.namespace_info.name != "temporal-system"
        )
        token = response.next_page_token
        if not token:
            break
    if not namespaces:
        raise RuntimeError("No restored Temporal namespaces found")
    for namespace in namespaces:
        scoped = await Client.connect("temporal:7233", namespace=namespace)
        clean = 0
        for _ in range(60):
            changed = False
            async for schedule in scoped.list_schedules():
                handle = scoped.get_schedule_handle(schedule.id)
                description = await handle.describe()
                if not description.schedule.state.paused:
                    await handle.pause(note="Staging snapshot: copied production automation is quarantined")
                    changed = True
            async for execution in scoped.list_workflows('ExecutionStatus = "Running"'):
                await scoped.get_workflow_handle(execution.id, run_id=execution.run_id).terminate(
                    reason="Staging snapshot: do not replay production side effects"
                )
                changed = True
            clean = 0 if changed else clean + 1
            if clean >= 3:
                break
            await asyncio.sleep(5)
        else:
            raise RuntimeError("Copied automation did not quiesce")


def launch(path: Path, prod: str, stage: str, apply: bool) -> None:
    if prod == stage:
        raise ValueError("Source and destination must differ")
    active = ROOT / "active-run"
    if active.exists():
        previous = load(ROOT / "runs" / checked_id(active.read_text().strip()))
        if previous.get("status") not in {"completed", "failed", "launch_failed"}:
            raise RuntimeError(
                "Previous host operation is unfinished; inspect/recover it before another refresh"
            )
    rows = containers()
    production, staging = inventory(rows, prod, "production"), inventory(rows, stage, "staging")
    assert_isolated(rows, production, staging)
    assert_password_compatible(rows, production, staging)
    health(production, "production", attempts=1)
    health(staging, "staging", attempts=1)
    free_space(path, production, staging)
    state = {"status": "queued" if apply else "dry_run_passed", "production": production, "staging": staging}
    save(path, state)
    if apply:
        script = str(path / "staging_refresh_host.py")
        active.write_text(path.name)
        # Transient units do not survive a machine reboot. Install a fixed boot
        # recovery unit before stopping anything, using the durable run state.
        recovery_unit = Path("/etc/systemd/system/nautionette-refresh-recovery.service")
        recovery_unit.write_text(
            "[Unit]\nDescription=Recover an interrupted Nautionette refresh\n"
            "After=docker.service\nRequires=docker.service\n[Service]\nType=oneshot\n"
            f"ExecStart=/usr/bin/python3 {script} recover {path}\n"
            "TimeoutStartSec=20min\nUMask=0077\n[Install]\nWantedBy=multi-user.target\n"
        )
        command("/usr/bin/systemctl", "daemon-reload")
        command("/usr/bin/systemctl", "enable", "nautionette-refresh-recovery.service")
        try:
            command(
                "/usr/bin/systemd-run",
                "--unit=nautionette-refresh-" + path.name,
                "--property=Type=exec",
                "--property=RuntimeMaxSec=90min",
                "--property=TimeoutStopSec=15min",
                "--property=KillMode=control-group",
                "--property=UMask=0077",
                "--property=ExecStopPost=/usr/bin/python3 " + script + " recover " + str(path),
                "/usr/bin/python3",
                script,
                "run",
                str(path),
            )
        except RuntimeError:
            # A lost acknowledgement is ambiguous: leave the queued marker in
            # place for inspection rather than assuming systemd never started.
            raise
    print(json.dumps({"status": state["status"], "run": path.name}))


def main() -> None:
    mode = sys.argv[1]
    if mode == "copy":
        copy_helper()
        return
    if mode == "quarantine":
        asyncio.run(quarantine())
        return
    if os.geteuid() != 0:
        raise RuntimeError("Host refresh requires root (or passwordless sudo)")
    os.umask(0o077)
    path = Path(sys.argv[2])
    if path.parent != ROOT / "runs" or checked_id(path.name) != path.name:
        raise ValueError("Invalid run directory")
    if mode == "status":
        state = load(path)
        print(
            json.dumps(
                {key: state[key] for key in ("status", "failure_phase", "recovery_errors") if key in state}
            )
        )
        return
    # One host operation, even across repositories/runners. Recovery executes
    # after the worker exits, so its lock is released before ExecStopPost runs.
    with (ROOT / "refresh.lock").open("a") as lock:
        # The detached worker can start before launch releases its lock.
        fcntl.flock(lock, fcntl.LOCK_EX | (fcntl.LOCK_NB if mode == "launch" else 0))
        if mode == "launch":
            launch(path, checked_id(sys.argv[3]), checked_id(sys.argv[4]), sys.argv[5] == "apply")
        elif mode == "run":
            run(path)
        elif mode == "recover":
            recover(path)
        else:
            raise ValueError("Unknown operation")


if __name__ == "__main__":
    try:
        main()
    except Exception:
        # No exception repr: library/daemon messages can include secret state.
        print(
            "Refresh operation failed. Inspect the private host run state and systemd unit.", file=sys.stderr
        )
        sys.exit(1)
