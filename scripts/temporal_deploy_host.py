"""Durable Temporal maintenance, inside the deployment's private controller container.

Uses the existing Docker control boundary, not SSH or host systemd. Before schema
writes a stopped-volume backup is restored/booted in isolation. After writes,
failures keep Temporal offline and never attempt a schema downgrade.
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
import subprocess
import sys
import time
import traceback
from pathlib import Path

if __package__:
    from . import staging_refresh_host as storage
else:
    import staging_refresh_host as storage

ROOT = Path("/var/lib/nautionette-temporal")
TERMINAL = {"prepared", "completed", "failed", "needs_recovery"}
WRITERS = {"docker-broker", "backend", "worker", "agentgateway", "workflow-mcp"}


def note_stopping(service: str):
    if os.environ.get("MAINTENANCE_RUN_ID"):
        path = ROOT / "runs" / storage.checked_id(os.environ["MAINTENANCE_RUN_ID"])
        state = load(path)
        state["stopping_service"] = service
        save(path, state)


def stop(stack: dict):
    # Drain admission while its broker/gateway dependencies are still serving.
    # Pausing the broker first can deadlock an admitted turn waiting on it.
    for item in stack["containers"]:
        if item["service"] == "backend":
            note_stopping("backend")
            info = json.loads(docker("inspect", item["id"]))[0]
            docker("update", "--restart=no", item["id"])
            if info["State"]["Running"]:
                drain = storage.env(info).get("BACKEND_CHAT_DRAIN_SECONDS", "3600")
                if not drain.isdecimal() or int(drain) > 3600:
                    raise RuntimeError("Backend drain configuration exceeds automatic maintenance budget")
                grace = max(90, int(drain) + 120, int(info["Config"].get("StopTimeout") or 0))
                docker("stop", "--time", str(grace), item["id"], timeout=grace + 30)
                stopped = json.loads(docker("inspect", item["id"]))[0]["State"]
                if stopped["Running"] or stopped.get("ExitCode") == 137:
                    raise RuntimeError("Backend did not drain cleanly; no snapshot will be taken")
    # The pre-hook lives in the broker: pause it rather than killing the hook.
    for item in stack["containers"]:
        if item["service"] == "docker-broker":
            note_stopping("docker-broker")
            info = json.loads(docker("inspect", item["id"]))[0]
            docker("update", "--restart=no", item["id"])
            if info["State"]["Running"] and not info["State"].get("Paused"):
                docker("pause", item["id"])
    # Close MCP clients before MCP servers. Otherwise long-lived gateway streams
    # hold workflow-mcp's Uvicorn shutdown open until Docker would SIGKILL it.
    for service in (
        "worker",
        "agentgateway",
        "workflow-mcp",
        "frontend-web",
        "website",
        "temporal",
        "maxio",
        "postgres",
    ):
        group = [item for item in stack["containers"] if item["service"] == service]
        if group:
            note_stopping(service)
            if service == "maxio":
                # Pinned MaxIO 0.4.2 only installs tokio::signal::ctrl_c(),
                # so SIGTERM is ignored as PID 1. Never SIGKILL it for backup.
                for item in group:
                    docker("update", "--restart=no", item["id"])
                    docker("stop", "--signal", "SIGINT", "--time", "90", item["id"], timeout=120)
                    info = json.loads(docker("inspect", item["id"]))[0]["State"]
                    if info["Running"] or info.get("ExitCode") == 137:
                        raise RuntimeError("MaxIO did not drain cleanly; no snapshot will be taken")
            else:
                storage.stop({**stack, "containers": group})


def unpause_broker(stack: dict):
    for item in stack["containers"]:
        if item["service"] == "docker-broker":
            ids = docker("ps", "-aq", "--filter", "id=" + item["id"])
            if ids and json.loads(docker("inspect", item["id"]))[0]["State"].get("Paused"):
                docker("unpause", item["id"])


def start(stack: dict, only: set[str] | None = None):
    if only is None or "docker-broker" in only:
        unpause_broker(stack)
    storage.start(stack, only)


def save(path: Path, state: dict):
    storage.save(path, state)
    if path.parent == ROOT / "runs" and state.get("app") and state.get("commit"):
        folder = ROOT / "fences"
        folder.mkdir(exist_ok=True, mode=0o755)
        folder.chmod(0o755)
        destination = folder / storage.checked_id(state["app"])
        temporary = folder / (destination.name + ".tmp")
        temporary.write_text(state["commit"] + "\n" + state["status"] + "\n")
        temporary.chmod(0o644)  # Only source revision/phase, never private backup data.
        temporary.replace(destination)
        os.sync()


def load(path: Path) -> dict:
    return storage.load(path)


def docker(*args: str, timeout: int = 120, path: Path | None = None) -> str:
    result = subprocess.run(  # noqa: S603 - fixed Docker verbs; no shell or user command strings
        ["/usr/bin/docker", *args], capture_output=True, text=True, timeout=timeout
    )
    if path is not None:
        # Private diagnostics only, never returned through SSH/CI.
        with (path / "operations.log").open("a") as log:
            log.write(result.stdout + result.stderr)
    if result.returncode:
        if path is None and os.environ.get("MAINTENANCE_RUN_ID"):
            folder = ROOT / "runs" / storage.checked_id(os.environ["MAINTENANCE_RUN_ID"])
            if folder.exists():
                with (folder / "operations.log").open("a") as log:
                    log.write(result.stderr)
        raise RuntimeError("Docker maintenance operation failed; inspect private host diagnostics")
    return result.stdout.strip()


def plan(path: Path) -> dict:
    result = json.loads((path / "context/upgrade-plan.json").read_text())
    versions = [item["version"] for item in result["releases"]]
    if not versions or result["target"] != versions[-1] or len(set(versions)) != len(versions):
        raise ValueError("Invalid Temporal upgrade plan")
    for index, version in enumerate(versions):
        if not re.fullmatch(r"1\.\d+\.\d+", version):
            raise ValueError("Invalid Temporal version")
        if index:
            previous = tuple(map(int, versions[index - 1].split(".")))
            current = tuple(map(int, version.split(".")))
            if current <= previous or current[1] not in {previous[1], previous[1] + 1}:
                raise ValueError("Temporal plan skips a minor release or downgrades a patch")
    return result


def member(stack: dict, service: str) -> str:
    return next(row["id"] for row in stack["containers"] if row["service"] == service)


def current_version(stack: dict) -> str:
    text = docker("exec", member(stack, "temporal"), "temporal-server", "--version")
    match = re.search(r"\b(1\.\d+\.\d+)\b", text)
    if not match:
        raise RuntimeError("Cannot establish running Temporal version")
    return match[1]


def schemas(stack: dict) -> tuple[str, str]:
    pg = member(stack, "postgres")
    info = json.loads(docker("inspect", pg))[0]
    user = storage.env(info)["POSTGRES_USER"]
    return tuple(
        docker(
            "exec",
            pg,
            "psql",
            "-XAt",
            "-v",
            "ON_ERROR_STOP=1",
            "-U",
            user,
            "-d",
            db,
            "-c",
            "SELECT curr_version FROM schema_version",
        )
        for db in ("temporal", "temporal_visibility")
    )


def private_data_network(temporal: dict, postgres: dict) -> str:
    common = set(temporal["NetworkSettings"]["Networks"]) & set(postgres["NetworkSettings"]["Networks"])
    # Coolify also attaches its shared application/proxy network. Never run
    # migration servers there: select the one Engine-internal data network.
    private = [
        name for name in common if json.loads(docker("network", "inspect", name))[0].get("Internal") is True
    ]
    if len(private) != 1:
        raise RuntimeError("Cannot establish the private PostgreSQL network")
    return private[0]


def preflight(path: Path, app: str, target: str) -> dict:
    rows = storage.containers()
    stack = storage.inventory(rows, app, target)
    for row in rows:
        owned = {item["id"] for item in stack["containers"]}
        if row["Id"] not in owned and row["State"]["Status"] in {
            "running",
            "paused",
            "restarting",
            "created",
        }:
            if storage.mounted(row) & set(stack["volumes"].values()) or (
                "nautionette.chat" in storage.labels(row)
                and stack["network"] in row.get("NetworkSettings", {}).get("Networks", {})
            ):
                raise RuntimeError("Active agent or external volume writer; finish work before deployment")
    version = current_version(stack)
    release = next((item for item in plan(path)["releases"] if item["version"] == version), None)
    if release is None or schemas(stack) != (release["core"], release["visibility"]):
        raise RuntimeError("Unsupported Temporal version/schema pair; refusing automatic migration")
    temporal = next(row for row in rows if row["Id"] == member(stack, "temporal"))
    postgres = next(row for row in rows if row["Id"] == member(stack, "postgres"))
    stack["data_network"] = private_data_network(temporal, postgres)
    settings = storage.env(temporal)
    if (
        settings.get("DB") != "postgres12"
        or settings.get("DBNAME", "temporal") != "temporal"
        or settings.get("VISIBILITY_DBNAME", "temporal_visibility") != "temporal_visibility"
    ):
        raise RuntimeError("Automatic migration supports the shipped PostgreSQL database layout only")
    settings["NUM_HISTORY_SHARDS"] = settings.get("NUM_HISTORY_SHARDS", "4")
    if not settings["NUM_HISTORY_SHARDS"].isdecimal():
        raise RuntimeError("Invalid history shard count")
    if settings["NUM_HISTORY_SHARDS"] != os.environ.get("MAINTENANCE_SHARDS", "4"):
        raise RuntimeError("Set TEMPORAL_NUM_HISTORY_SHARDS to the existing cluster count before deploying")
    # Persist only the necessary runtime config in a separate root-only env file.
    allowed = {
        "DB",
        "DB_PORT",
        "POSTGRES_USER",
        "POSTGRES_PWD",
        "POSTGRES_SEEDS",
        "NUM_HISTORY_SHARDS",
        "DYNAMIC_CONFIG_FILE_PATH",
        "DEFAULT_NAMESPACE",
        "DEFAULT_NAMESPACE_RETENTION",
    }
    values = {key: value for key, value in settings.items() if key in allowed}
    values.update(
        BIND_ON_IP="0.0.0.0",  # noqa: S104 - private, unpublished maintenance server
        POSTGRES_SEEDS="postgres",
        DB_PORT="5432",
    )
    if any("\n" in value or "\r" in value for value in values.values()):
        raise RuntimeError("Multiline database configuration is not supported")
    (path / "database.env").write_text("".join(f"{key}={value}\n" for key, value in values.items()))
    stack["namespace"] = storage.env(next(row for row in rows if row["Id"] == stack["backend"])).get(
        "TEMPORAL_NAMESPACE", "default"
    )
    return {"stack": stack, "from": version, "shards": settings["NUM_HISTORY_SHARDS"]}


def name(path: Path, kind: str) -> str:
    # Docker accepts long names, but its embedded DNS still needs labels <=63.
    identity = hashlib.sha256(path.name.encode()).hexdigest()[:20]
    return "nt-temporal-" + identity + "-" + kind


def remove(path: Path, kind: str):
    container = name(path, kind)
    if docker("ps", "-aq", "--filter", "name=^/" + container + "$"):
        docker("rm", "-f", container)


def probe(path: Path, stack: dict, server: str, mode: str, version: str, network: str | None = None):
    remove(path, "probe")
    server = docker("inspect", "--format", "{{.Name}}", server).lstrip("/")
    args = [
        "create",
        "--name",
        name(path, "probe"),
        "--network",
        network or stack["data_network"],
        "--user",
        "0:0",
        "--entrypoint",
        "python",
        "--security-opt",
        "no-new-privileges:true",
        "--mount",
        f"type=bind,src={path},dst=/migration",
        "-e",
        f"TEMPORAL_ADDRESS={server}:7233",
        "-e",
        f"TEMPORAL_NAMESPACE={stack['namespace']}",
        "-e",
        "PROBE_ID=" + name(path, "workflow"),
        "-e",
        "PROBE_VERSION=" + version,
        "-e",
        "PROBE_SHARDS=" + load(path)["shards"],
        stack["worker_image"],
        "/migration/temporal_deploy_probe.py",
        mode,
    ]
    docker(*args, path=path)
    docker("start", name(path, "probe"))
    code = docker("wait", name(path, "probe"), timeout=900)
    docker("logs", name(path, "probe"), path=path)
    if code != "0":
        raise RuntimeError("Temporal deployment probe failed; inspect private host diagnostics")
    remove(path, "probe")


def server(path: Path, stack: dict, image: str, network: str | None = None) -> str:
    remove(path, "server")
    container = name(path, "server")
    docker(
        "run",
        "-d",
        "--name",
        container,
        "--network",
        network or stack["data_network"],
        "--env-file",
        str(path / "database.env"),
        image,
        path=path,
    )
    deadline = time.monotonic() + 180
    while True:
        try:
            docker(
                "exec",
                container,
                "temporal",
                "--address",
                "127.0.0.1:7233",
                "--command-timeout",
                "4s",
                "operator",
                "cluster",
                "health",
            )
            return container
        except RuntimeError:
            if time.monotonic() >= deadline:
                docker("logs", container, path=path)
                raise
            time.sleep(2)


def build(path: Path, version: str, kind: str) -> str:
    tag = "nautionette/temporal-maintenance:" + path.name + "-" + kind + "-" + version
    if version.startswith("1.29."):
        # 1.29.7 exists in the deprecated bundle, not the new admin-tools line.
        dockerfile = "Dockerfile.tools-legacy" if kind == "tools" else "Dockerfile.legacy"
    else:
        dockerfile = "Dockerfile.tools" if kind == "tools" else "Dockerfile"
    docker(
        "build",
        "--build-arg",
        "TEMPORAL_VERSION=" + version,
        "-t",
        tag,
        "-f",
        str(path / "context" / dockerfile),
        str(path / "context"),
        timeout=1200,
        path=path,
    )
    return docker("image", "inspect", "--format", "{{.Id}}", tag)


def free_space(path: Path, stack: dict):
    sizes, free = [], []
    code = (
        "import os,json,shutil; print(json.dumps({'size':sum(os.lstat(os.path.join(p,f)).st_size "
        "for p,ds,fs in os.walk('/src') for f in fs),'free':shutil.disk_usage('/src').free}))"
    )
    for volume in stack["volumes"].values():
        info = json.loads(
            docker(
                "run",
                "--rm",
                "--network",
                "none",
                "--user",
                "0:0",
                "--entrypoint",
                "python",
                "--mount",
                f"type=volume,src={volume},dst=/src,readonly",
                stack["copy_image"],
                "-c",
                code,
            )
        )
        sizes.append(info["size"])
        free.append(info["free"])
    import shutil

    needed = sum(sizes) * 2 + 512 * 1024 * 1024
    if min([shutil.disk_usage(path).free, *free]) < needed:
        raise RuntimeError("Insufficient space for migration backup and isolated restore")


def snapshot(path: Path, stack: dict):
    for row in storage.containers():
        if (
            row["State"]["Running"]
            and not row["State"].get("Paused")
            and storage.mounted(row) & set(stack["volumes"].values())
        ):
            raise RuntimeError("A writer appeared before the snapshot")
    storage.copy_datasets(path, stack, "before")


def restore_check(path: Path, stack: dict):
    # A disposable volume and private network: no production services can reach it.
    network, volume = name(path, "restore-net"), name(path, "restore-data")
    docker("network", "create", "--internal", network)
    docker("volume", "create", volume)
    folder = path / "before/postgres-data"
    storage.helper(
        path,
        stack["copy_image"],
        "copy",
        [f"type=bind,src={folder},dst=/src,readonly", f"type=volume,src={volume},dst=/dst"],
    )
    original_pg = json.loads(docker("inspect", member(stack, "postgres")))[0]
    # PGDATA/major/image/role config are preserved. No empty-database fallback.
    pg_env = storage.env(original_pg)
    (path / "restore.env").write_text(
        "".join(
            f"{key}={value}\n"
            for key, value in pg_env.items()
            if key.startswith("POSTGRES_") or key == "PGDATA"
        )
    )
    docker(
        "run",
        "-d",
        "--name",
        name(path, "restore-pg"),
        "--network",
        network,
        "--network-alias",
        "postgres",
        "--env-file",
        str(path / "restore.env"),
        "-v",
        volume + ":/var/lib/postgresql/data",
        stack["postgres_image"],
        path=path,
    )
    server_id = server(path, stack, stack["temporal_image"], network)
    probe(path, stack, server_id, "check", "restored", network)
    remove(path, "server")
    remove(path, "restore-pg")
    docker("network", "rm", network)
    docker("volume", "rm", volume)


def run(path: Path):
    state = load(path)
    stack = state["stack"]
    try:
        # Build everything BEFORE the write pause and establish immutable image IDs.
        releases = plan(path)["releases"]
        start_index = next(index for index, row in enumerate(releases) if row["version"] == state["from"])
        images = {
            row["version"]: {kind: build(path, row["version"], kind) for kind in ("server", "tools")}
            for row in releases[start_index + 1 :]
        }
        if preflight(path, state["app"], state["environment"]) != {
            key: state[key] for key in ("stack", "from", "shards")
        }:
            raise RuntimeError("Stack changed during image preparation")
        state["images"] = images
        state["stopping"] = True
        save(path, state)
        # Preserve non-writer services; broker first. Allow clean worker cancellation.
        writers = {**stack, "containers": [row for row in stack["containers"] if row["service"] in WRITERS]}
        stop(writers)
        probe(path, stack, member(stack, "temporal"), "capture", state["from"])
        # Now stop the remaining stack for a consistent cross-volume backup.
        stop(stack)
        state["status"] = "backing_up"
        save(path, state)
        snapshot(path, stack)
        state["backup_complete"] = True
        save(path, state)
        restore_check(path, stack)
        state["restore_verified"] = True
        save(path, state)
        start(stack, {"postgres"})
        deadline = time.monotonic() + 120
        while True:
            try:
                schemas(stack)
                break
            except RuntimeError:
                if time.monotonic() >= deadline:
                    raise
                time.sleep(2)
        previous = state["from"]
        for row in releases[start_index + 1 :]:
            version = row["version"]
            state["status"] = "migrating-" + version
            # Set BEFORE launching the tool: failure/ack loss is not evidence of no writes.
            state["schema_writes_started"] = True
            save(path, state)
            docker(
                "run",
                "--rm",
                "--name",
                name(path, "schema"),
                "--network",
                stack["data_network"],
                "--env-file",
                str(path / "database.env"),
                images[version]["tools"],
                "upgrade-from-" + previous,
                timeout=600,
                path=path,
            )
            server_id = server(path, stack, images[version]["server"])
            probe(path, stack, server_id, "check", version)
            # Keep each intermediate server processing its private workload for a soak.
            time.sleep(120)
            docker("stop", "--time", "60", server_id, timeout=90)
            remove(path, "server")
            state["verified_version"] = version
            save(path, state)
            previous = version
        final = server(path, stack, images[state["target"]]["server"])
        probe(path, stack, final, "finish", state["target"])
        probe(path, stack, final, "resume", state["target"])
        docker("stop", "--time", "60", final, timeout=90)
        remove(path, "server")
        # The old Temporal and app writers remain stopped until Coolify deploys
        # the new maintained configuration. Never restart auto-setup on new schemas.
        state["status"] = "prepared"
        save(path, state)
    finally:
        if load(path)["status"] != "prepared":
            recover(path)


def recover(path: Path, boot: bool = False):
    state = load(path)
    if state["status"] == "completed" or (state["status"] == "prepared" and not boot):
        return
    helper = "nautionette-refresh-" + path.name
    if docker("ps", "-aq", "--filter", "name=^/" + helper + "$"):
        docker("rm", "-f", helper)
    for kind in ("probe", "server", "schema", "restore-pg"):
        remove(path, kind)
    if state.get("schema_writes_started") or state["status"] == "prepared":
        # Fail closed after any possible schema write, including a host reboot.
        # Do not stop newly deployed containers by name: only the recorded old IDs.
        existing = {row["Id"] for row in storage.containers()}
        original = {
            **state["stack"],
            "containers": [
                row
                for row in state["stack"]["containers"]
                if row["id"] in existing and row["service"] in WRITERS | {"temporal"}
            ],
        }
        stop(original)
        if state["status"] != "prepared":
            state["status"] = "needs_recovery"
        save(path, state)
        return
    if state.get("stopping"):
        start(state["stack"], {"postgres", "temporal"})
        if (path / "probe.json").exists():
            probe(path, state["stack"], member(state["stack"], "temporal"), "resume", state["from"])
        start(state["stack"])
    state["status"] = "failed"
    state["safe_to_retry"] = True
    save(path, state)


def finish(path: Path):
    state = load(path)
    if state["status"] == "completed":
        return
    if state["status"] not in {"prepared", "current"}:
        raise RuntimeError("Temporal migration is not prepared")
    stack = storage.inventory(storage.containers(), state["app"], state["environment"])
    expected = plan(path)["releases"][-1]
    if current_version(stack) != state["target"] or schemas(stack) != (
        expected["core"],
        expected["visibility"],
    ):
        raise RuntimeError("Deployment did not start the maintained Temporal version/schemas")
    temporal = json.loads(docker("inspect", member(stack, "temporal")))[0]
    if storage.env(temporal).get("NUM_HISTORY_SHARDS", "4") != state["shards"]:
        raise RuntimeError("Deployment changed the history shard count")
    backend = json.loads(docker("inspect", stack["backend"]))[0]
    if storage.env(backend).get("APP_VERSION") != state["commit"]:
        raise RuntimeError("Deployed application revision differs from migration candidate")
    # Restore policies even when Compose reused a stopped unchanged service.
    existing = {row["Id"] for row in storage.containers()}
    for item in state["stack"]["containers"]:
        if item["id"] in existing:
            policy = item["restart"]
            value = policy["Name"] or "no"
            if value == "on-failure" and policy.get("MaximumRetryCount"):
                value += ":" + str(policy["MaximumRetryCount"])
            docker("update", "--restart=" + value, item["id"])
    state["status"] = "completed"
    save(path, state)


def launch(path: Path, app: str, environment: str, commit: str):
    if environment not in {"staging", "production"} or not re.fullmatch(r"[0-9a-f]{40}", commit):
        raise ValueError("Invalid deployment identity")
    target = plan(path)["target"]
    try:
        metadata = preflight(path, app, environment)
    except Exception:
        with (path / "preflight.log").open("a") as log:
            traceback.print_exc(file=log)
        save(
            path, {"status": "failed", "safe_to_retry": True, "app": app, "commit": commit, "target": target}
        )
        print(json.dumps({"status": "failed", "safe_to_retry": True}))
        return
    state = {
        **metadata,
        "status": "queued",
        "app": app,
        "environment": environment,
        "commit": commit,
        "target": target,
    }
    active = ROOT / ("active-" + app)
    if active.exists():
        old = load(ROOT / "runs" / storage.checked_id(active.read_text().strip()))
        if old["status"] != "completed" and not old.get("safe_to_retry"):
            raise RuntimeError("Unfinished Temporal migration; poll its existing run")
    # Same host lock and active-refresh fence as staging refresh.
    refresh_active = storage.ROOT / "active-run"
    if refresh_active.exists():
        refresh = storage.load(storage.ROOT / "runs" / storage.checked_id(refresh_active.read_text().strip()))
        if refresh["status"] not in {"completed", "failed", "launch_failed"}:
            raise RuntimeError("Staging refresh is unfinished")
    if metadata["from"] == target:
        state["status"] = "current"
        save(path, state)
        print(json.dumps({"status": "current", "target": target, "shards": metadata["shards"]}))
        return
    storage.health(metadata["stack"], environment, attempts=1)
    # Reserve conservative backup capacity using the existing volume sizing check.
    free_space(path, metadata["stack"])
    save(path, state)
    active.write_text(path.name)
    run(path)


def main():
    if os.geteuid() != 0:
        raise RuntimeError("Maintenance controller requires its scoped root Docker access")
    os.umask(0o077)
    mode, raw = sys.argv[1:3]
    path = Path(raw)
    if path.parent != ROOT / "runs" or storage.checked_id(path.name) != path.name:
        raise ValueError("Invalid Temporal run path")
    if mode == "status":
        state = load(path)
        print(
            json.dumps(
                {
                    key: state[key]
                    for key in ("status", "target", "commit", "shards", "app", "safe_to_retry")
                    if key in state
                }
            )
        )
        return
    storage.ROOT.mkdir(parents=True, exist_ok=True, mode=0o700)
    with (storage.ROOT / "refresh.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | (fcntl.LOCK_NB if mode == "launch" else 0))
        if mode == "launch":
            launch(path, storage.checked_id(sys.argv[3]), sys.argv[4], sys.argv[5])
        elif mode == "run":
            run(path)
        elif mode == "finish":
            finish(path)
            print(json.dumps({"status": "completed"}))
        elif mode in {"recover", "fence"}:
            recover(path, boot=mode == "fence")
        else:
            raise ValueError("Unknown Temporal operation")


if __name__ == "__main__":
    try:
        main()
    except Exception:
        if len(sys.argv) > 2 and sys.argv[1] != "status":
            folder = Path(sys.argv[2])
            if folder.parent == ROOT / "runs" and folder.exists():
                with (folder / "failure.log").open("a") as log:
                    traceback.print_exc(file=log)
        print("Temporal maintenance failed; inspect private host run state/diagnostics.", file=sys.stderr)
        sys.exit(1)
