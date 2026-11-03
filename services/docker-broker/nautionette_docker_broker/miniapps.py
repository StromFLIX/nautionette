"""Fixed mini-app builder/runtime. No caller-selected commands, mounts or networks.

The app has a per-app *internal* network with only this broker. Runtime root is
read-only. Exactly three durable SQLite files are writable, each under RLIMIT_FSIZE;
there is no writable persistent directory, unbounded log or external egress route.
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import hashlib
import hmac
import io
import json
import os
import re
import secrets
import shutil
import socket
import sqlite3
import tarfile
import threading
import time
from pathlib import Path
from typing import Any
from weakref import WeakValueDictionary

import httpx
from docker.errors import APIError, DockerException, NotFound
from docker.types import Ulimit
from fastapi import HTTPException
from requests.exceptions import RequestException

from . import daemon, image_context
from .config import AGENT_IMAGE_REGISTRY_PREFIX, INTERNAL_TOKEN, WORKFLOWS_VOLUME

ROOT = Path(os.environ.get("MINIAPPS_DIR", "/miniapps"))
BUILDER_ROOT = Path(os.environ.get("MINIAPP_BUILDER_CONTEXT", "/miniapp-sdk"))
BUILDER_HASH = image_context.bundled_hash("miniapp") or image_context.context_hash(BUILDER_ROOT)
BUILDER = f"nautionette/miniapp-builder:{BUILDER_HASH}"
BACKEND = os.environ.get("AGENT_BACKEND_URL", "http://backend:8080").rstrip("/")
ID = re.compile(r"[a-z][a-z0-9-]{0,47}")
FILE_LIMIT = 16 * 1024 * 1024
BODY_LIMIT = 65536
_namespace = hashlib.sha256(WORKFLOWS_VOLUME.encode()).hexdigest()[:12]
_build_slot = threading.Lock()
_locks: WeakValueDictionary = WeakValueDictionary()
_lock_index = threading.Lock()
_calls = threading.BoundedSemaphore(16)
_cap_calls = threading.BoundedSemaphore(8)
_last_used: dict[str, float] = {}


class CompilerUnavailable(RuntimeError):
    """Fixed diagnostics only: never return arbitrary Docker/registry/auth messages."""

    def __init__(self, stage: str):
        self.detail = {
            "code": "compiler_unavailable",
            "stage": stage,
            "sdk_fingerprint": BUILDER_HASH,
            "message": "Mini-app compiler unavailable; check the published SDK image and broker health",
        }
        super().__init__(self.detail["message"])


def checked(app_id: str) -> str:
    if not isinstance(app_id, str) or not ID.fullmatch(app_id):
        raise ValueError("Invalid mini-app ID")
    return app_id


def lock(app_id: str):
    # No striped-lock collisions: compiling one app must not freeze another.
    checked(app_id)
    with _lock_index:
        guard = _locks.get(app_id)
        if guard is None:
            guard = threading.RLock()
            _locks[app_id] = guard
        return guard


def root(app_id: str) -> Path:
    return ROOT / checked(app_id)


def load(app_id: str) -> dict:
    try:
        return json.loads((root(app_id) / "control.json").read_text())
    except FileNotFoundError:
        return {}


def save(app_id: str, config: dict) -> None:
    folder = root(app_id)
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / "control.tmp"
    path.write_text(json.dumps(config))
    path.chmod(0o600)
    # Persist the journal before changing Docker resources, not just Python state.
    with path.open("rb") as handle:
        os.fsync(handle.fileno())
    path.replace(folder / "control.json")
    fd = os.open(folder, os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def labels(app_id: str) -> dict[str, str]:
    return {"nautionette.miniapp": checked(app_id), "nautionette.deployment": WORKFLOWS_VOLUME}


def name(app_id: str, suffix: str) -> str:
    return f"nautionette-app-{_namespace}-{checked(app_id)}-{suffix}"


def owned_container(app_id: str, config: dict):
    try:
        container = daemon.client().containers.get(name(app_id, str(config["revision"])))
    except NotFound:
        return None
    if any(container.labels.get(k) != v for k, v in labels(app_id).items()):
        raise RuntimeError("Mini-app container ownership mismatch")
    return container


def host_path(path: Path) -> str:
    """Resolve only our deployment-owned volume; never accept a host path from a request."""
    me = daemon.client().containers.get(socket.gethostname())
    mount = next((m for m in me.attrs.get("Mounts", []) if m["Destination"] == str(ROOT)), None)
    if not mount or mount.get("Type") != "volume":
        raise RuntimeError("Mini-app persistent volume must be mounted on the broker")
    return str(Path(mount["Source"]) / path.relative_to(ROOT))


def network(app_id: str):
    client = daemon.client()
    # internal:true alone still permits access to services on the host bridge!
    # Docker 28's isolated gateway removes that host address. Fail closed on older engines.
    if int(client.version()["Version"].split(".")[0]) < 28:
        raise ValueError("Mini-app runtimes require Docker Engine 28+ for isolated gateway networks")
    net_name = name(app_id, "net")
    isolated = {"com.docker.network.bridge.gateway_mode_ipv4": "isolated"}
    try:
        net = client.networks.get(net_name)
    except NotFound:
        net = client.networks.create(
            net_name,
            driver="bridge",
            internal=True,
            enable_ipv6=False,
            options=isolated,
            labels=labels(app_id),
        )
    if (
        net.attrs.get("Internal") is not True
        or net.attrs.get("EnableIPv6")
        or any(net.attrs.get("Options", {}).get(k) != v for k, v in isolated.items())
        or any(net.attrs.get("Labels", {}).get(k) != v for k, v in labels(app_id).items())
    ):
        raise RuntimeError("Mini-app network ownership mismatch")
    me = client.containers.get(socket.gethostname())
    me.reload()
    if net_name not in me.attrs["NetworkSettings"]["Networks"]:
        net.connect(me, aliases=["miniapp-host"])
    return net


def data_files(app_id: str, revision: int, previous: dict | None) -> None:
    folder = root(app_id) / str(revision)
    folder.mkdir(mode=0o755, parents=True, exist_ok=False)
    # Enter persistent WAL mode while the parent is still writable. Switching
    # an existing rollback-mode header to WAL needs a transient rollback journal,
    # which the runtime's read-only /data intentionally cannot create.
    if not previous:
        with contextlib.closing(sqlite3.connect(folder / "state.db")) as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA user_version=1")
            conn.commit()
    for suffix in ("", "-wal", "-shm"):
        target = folder / ("state.db" + suffix)
        source = root(app_id) / str(previous["revision"]) / target.name if previous else None
        if source:
            if source.stat().st_size > FILE_LIMIT:
                raise ValueError("SQLite file exceeds the app storage limit")
            shutil.copyfile(source, target)
        else:
            # SQLite may unlink the bootstrap WAL/SHM on close; recreate the
            # fixed mount files without truncating the initialized main DB.
            target.touch()
        target.chmod(0o666)


def runtime_seccomp() -> str:
    # Docker's default allowlist (moby/profiles seccomp/v0.1.0, Apache-2.0),
    # tightened for this SDK. RLIMIT_FSIZE alone does NOT constrain allocated
    # extents: fallocate(KEEP_SIZE) and filesystem ioctls can reserve past EOF.
    profile = json.loads(Path(__file__).with_name("miniapp-seccomp.json").read_text())
    forbidden = {
        "fallocate",
        "ioctl",
        "io_uring_setup",
        "io_uring_enter",
        "io_uring_register",
        "setxattr",
        "lsetxattr",
        "fsetxattr",  # extended attributes bypass ordinary file-size limits too
    }
    for group in profile["syscalls"]:
        group["names"] = [name for name in group["names"] if name not in forbidden]
    return "seccomp=" + json.dumps(profile, separators=(",", ":"))


def create_runtime(app_id: str, config: dict):
    client = daemon.client()
    image = client.images.get(config["image"])
    if any(image.labels.get(k) != v for k, v in labels(app_id).items()):
        raise RuntimeError("Mini-app image ownership mismatch")
    net = network(app_id)
    files = root(app_id) / str(config["revision"])
    mounts = {
        host_path(files / ("state.db" + suffix)): {"bind": "/data/state.db" + suffix, "mode": "rw"}
        for suffix in ("", "-wal", "-shm")
    }
    return client.containers.create(
        config["image"],
        name=name(app_id, str(config["revision"])),
        environment={"MINIAPP_TOKEN": config["token"]},
        user="65532:65532",
        network=net.name,
        sysctls={"net.ipv6.conf.all.disable_ipv6": "1", "net.ipv6.conf.default.disable_ipv6": "1"},
        volumes=mounts,
        read_only=True,
        tmpfs={"/tmp": "size=8m,noexec,nosuid,nodev,mode=1777"},  # noqa: S108 - private container tmpfs
        mem_limit=f"{config['memory_mb']}m",
        memswap_limit=f"{config['memory_mb']}m",
        nano_cpus=250_000_000,
        pids_limit=32,
        cap_drop=["ALL"],
        security_opt=["no-new-privileges:true", runtime_seccomp()],
        ulimits=[
            Ulimit(name="fsize", soft=FILE_LIMIT, hard=FILE_LIMIT),
            Ulimit(name="nofile", soft=128, hard=128),
        ],
        labels=labels(app_id),
        log_config={"Type": "json-file", "Config": {"max-size": "256k", "max-file": "1"}},
    )


def runtime_call(app_id: str, config: dict, path: str, payload: dict | None = None):
    container = owned_container(app_id, config)
    if container is None:
        container = create_runtime(app_id, config)
    container.reload()
    if container.status != "running":
        container.start()
        container.reload()
    ip = container.attrs["NetworkSettings"]["Networks"][name(app_id, "net")]["IPAddress"]
    status, body = bounded_http(
        "GET" if payload is None else "POST",
        f"http://{ip}:8080{path}",
        {"Authorization": f"Bearer {config['token']}"},
        payload,
        timeout=5 if path == "/healthz" else 30,
    )
    if status != 200:
        raise ValueError("App health or request failed")
    return body


def ready(app_id: str, config: dict) -> None:
    deadline = time.monotonic() + 15
    while True:
        try:
            if runtime_call(app_id, config, "/healthz") == {"status": "ok"}:
                return
        except (httpx.HTTPError, ValueError, TimeoutError):
            pass
        if time.monotonic() >= deadline:
            raise ValueError("Candidate health check failed; previous release retained")
        time.sleep(0.25)


def stop(app_id: str, config: dict, *, remove: bool = False) -> None:
    container = owned_container(app_id, config)
    if container:
        if remove:
            container.remove(force=True)
        else:
            container.stop(timeout=5)


def build(app_id: str, revision: int, source: str, *, validate_only: bool = False) -> str | dict:
    if not isinstance(source, str) or not 1 <= len(source.encode()) <= BODY_LIMIT:
        raise ValueError("Rust module must be 1..65536 bytes")
    if not _build_slot.acquire(blocking=False):
        raise ValueError("Another mini-app build is running; retry after it completes")
    container = None
    tag = name(app_id, f"release-{revision}")
    stage = "docker"
    try:
        client = daemon.client()
        stage = "image"
        # Builder identity is derived from the bundled SDK, never caller-selected.
        try:
            client.images.get(BUILDER)
        except NotFound:
            if AGENT_IMAGE_REGISTRY_PREFIX:
                image = client.images.pull(f"{AGENT_IMAGE_REGISTRY_PREFIX}/miniapp-builder:{BUILDER_HASH}")
                repository, _, version = BUILDER.rpartition(":")
                if not image.tag(repository, version):
                    raise CompilerUnavailable(stage) from None
            else:
                client.images.build(path=str(BUILDER_ROOT), tag=BUILDER, rm=True)
        stage = "container"
        container = client.containers.create(
            BUILDER,
            network_mode="none",
            read_only=True,
            user="65532:65532",
            environment={"APP_SOURCE": base64.b64encode(source.encode()).decode()},
            tmpfs={"/tmp": "size=768m,exec,nosuid,nodev,mode=1777"},  # noqa: S108 - private compiler tmpfs
            mem_limit="1g",
            memswap_limit="1g",
            nano_cpus=1_000_000_000,
            pids_limit=128,
            cap_drop=["ALL"],
            security_opt=["no-new-privileges:true"],
            labels={**labels(app_id), "nautionette.miniapp.build": "true"},
            log_config={"Type": "json-file", "Config": {"max-size": "1m", "max-file": "1"}},
        )
        container.start()
        deadline = time.monotonic() + 180
        while time.monotonic() < deadline:
            container.reload()
            if container.status != "running":
                diagnostic = container.logs(stdout=False, stderr=True, tail=30).decode("utf-8", "replace")[
                    -6000:
                ]
                raise ValueError(f"Rust compilation failed:\n{diagnostic}")
            # Docker archive/cp cannot read a container's private tmpfs. These
            # fixed execs run inside its mount namespace, without a shell or
            # caller-selected arguments. A marker file also cannot be forged
            # by compiler warning text as a stdout readiness marker could.
            try:
                if container.exec_run(["test", "-f", "/tmp/build.ready"]).exit_code == 0:  # noqa: S108
                    break
            except APIError:
                pass  # The compiler may have exited between reload and exec.
            time.sleep(0.5)
        else:
            raise ValueError("Rust build exceeded 180 seconds")
        stage = "artifact"
        binary = bytearray()
        result = container.exec_run(
            ["cat", "/tmp/target/release/nautionette-miniapp"],  # noqa: S108 - private compiler tmpfs
            stream=True,
            stderr=False,
        )
        for chunk in result.output:
            binary.extend(chunk)
            if len(binary) > FILE_LIMIT:
                raise ValueError("Rust artifact exceeds 16 MiB")
        if not binary.startswith(b"\x7fELF"):
            raise ValueError("Compiler did not produce an ELF executable")
        if validate_only:
            return {"valid": True, "artifact_bytes": len(binary), "sdk_fingerprint": BUILDER_HASH}
        context = io.BytesIO()
        with tarfile.open(fileobj=context, mode="w") as tar:
            dockerfile = (
                b'FROM scratch\nCOPY app /app\nCOPY data /data\nUSER 65532:65532\nENTRYPOINT ["/app"]\n'
            )
            for filename, content in (("Dockerfile", dockerfile), ("app", binary)):
                member = tarfile.TarInfo(filename)
                member.size = len(content)
                member.mode = 0o755 if filename == "app" else 0o644
                tar.addfile(member, io.BytesIO(content))
            member = tarfile.TarInfo("data")
            member.type = tarfile.DIRTYPE
            member.mode = 0o555
            tar.addfile(member)
        context.seek(0)
        stage = "runtime_image"
        image, _ = client.images.build(
            fileobj=context, custom_context=True, tag=tag, network_mode="none", rm=True, labels=labels(app_id)
        )
        return image.id
    except (DockerException, RequestException) as exc:
        raise CompilerUnavailable(stage) from exc
    finally:
        if container:
            with contextlib.suppress(Exception):
                container.remove(force=True)
        _build_slot.release()


def recover(app_id: str, config: dict) -> dict:
    """An interrupted candidate is never implicitly promoted after a broker crash."""
    building = config.pop("building", None)
    if building:
        with contextlib.suppress(NotFound):
            daemon.client().images.remove(name(app_id, f"release-{building}"))
        save(app_id, config)
    retired = config.get("retiring")
    if retired:
        if retired["revision"] == (config.get("active") or {}).get("revision"):
            raise RuntimeError("Refusing to retire the active release")
        stop(app_id, retired, remove=True)
        with contextlib.suppress(NotFound):
            daemon.client().images.remove(name(app_id, f"release-{retired['revision']}"))
        with contextlib.suppress(FileNotFoundError):
            shutil.rmtree(root(app_id) / str(retired["revision"]))
        config.pop("retiring")
        save(app_id, config)
    candidate = config.get("candidate")
    if candidate:
        stop(app_id, candidate, remove=True)
        with contextlib.suppress(NotFound):
            daemon.client().images.remove(name(app_id, f"release-{candidate['revision']}"))
        with contextlib.suppress(FileNotFoundError):
            shutil.rmtree(root(app_id) / str(candidate["revision"]))
        config.pop("candidate")
        save(app_id, config)
    return config


def deploy(app_id: str, revision: int, source: str, memory_mb: int, mode: str) -> dict:
    checked(app_id)
    if not INTERNAL_TOKEN:
        raise ValueError("Configure INTERNAL_TOKEN before running mini-app backends")
    if (
        type(revision) is not int
        or not 1 <= revision <= 1000000
        or memory_mb not in (64, 128, 256)
        or mode not in ("on-demand", "background")
    ):
        raise ValueError("Invalid mini-app runtime configuration")
    with lock(app_id):
        current = recover(app_id, load(app_id))
        old = current.get("active")
        if old and old["revision"] == revision:
            return snapshot(app_id)
        if old and revision <= old["revision"]:
            raise ValueError("Runtime revisions must increase")
        # Compilation is journaled too: a crash just after tagging must not
        # leak an unreferenced release image or implicitly promote it.
        save(app_id, {**current, "building": revision})
        try:
            image = build(app_id, revision, source)
        except Exception:
            recover(app_id, load(app_id))
            raise
        candidate = {
            "revision": revision,
            "image": image,
            "token": secrets.token_urlsafe(32),
            "memory_mb": memory_mb,
            "mode": mode,
        }
        # Journal BEFORE stopping/snapshotting the old app. Its data is never
        # modified by the candidate, even by a broken generated health function.
        save(app_id, {**current, "candidate": candidate})
        try:
            if old:
                stop(app_id, old)
            data_files(app_id, revision, old)
            ready(app_id, candidate)
        except Exception:
            recover(app_id, load(app_id))
            if old:
                ready(app_id, old)
            raise
        # A failed/ambiguous fsync here must not blindly roll back an activation
        # already visible on disk. Reconciliation reads the journal instead.
        save(app_id, {"active": candidate, **({"retiring": old} if old else {})})
        # The activation commit records retirement before removing old resources.
        # A crash or failed unlink cannot leak one old data/image set per publish.
        recover(app_id, load(app_id))
        _last_used[app_id] = time.monotonic()
        return snapshot(app_id)


def snapshot(app_id: str) -> dict:
    checked(app_id)
    with lock(app_id):
        config = load(app_id).get("active")
        if not config:
            return {"revision": None, "status": "absent"}
        container = owned_container(app_id, config)
        state = "suspended"
        memory = 0
        cpu_percent = None
        pids = 0
        runtime_state = {}
        if container:
            container.reload()
            runtime_state = container.attrs.get("State", {})
            state = "running" if container.status == "running" else "suspended"
            if state == "running":
                stats = container.stats(stream=False)
                memory = stats.get("memory_stats", {}).get("usage", 0)
                cpu = stats.get("cpu_stats", {})
                before = stats.get("precpu_stats", {})
                delta = cpu.get("cpu_usage", {}).get("total_usage", 0) - before.get("cpu_usage", {}).get(
                    "total_usage", 0
                )
                system = cpu.get("system_cpu_usage", 0) - before.get("system_cpu_usage", 0)
                if system > 0 and delta >= 0:
                    cpu_percent = round(100 * delta / system * cpu.get("online_cpus", 1), 2)
                pids = stats.get("pids_stats", {}).get("current", 0)
        size = sum(p.stat().st_size for p in (root(app_id) / str(config["revision"])).glob("state.db*"))
        return {
            "revision": config["revision"],
            "status": state,
            "memory_bytes": memory,
            "cpu_percent": cpu_percent,
            "cpu_limit": 0.25,
            "pids": pids,
            "pid_limit": 32,
            "oom_killed": bool(runtime_state.get("OOMKilled")),
            "exit_code": runtime_state.get("ExitCode"),
            "started_at": runtime_state.get("StartedAt"),
            "sampled_at": time.time(),
            "image_bytes": daemon.client().images.get(config["image"]).attrs.get("Size", 0),
            "data_bytes": size,
            "persistent_limit_bytes": 3 * FILE_LIMIT,
            "memory_limit_bytes": config["memory_mb"] * 1024 * 1024,
            "mode": config["mode"],
        }


def request(app_id: str, revision: int, payload: dict) -> Any:
    if not _calls.acquire(blocking=False):
        raise ValueError("Mini-app request capacity reached")
    try:
        with lock(app_id):
            config = load(app_id).get("active")
            if load(app_id).get("disabled") or not config or config["revision"] != revision:
                raise ValueError("Mini-app release is not active")
            _last_used[app_id] = time.monotonic()
            ready(app_id, config)
            return runtime_call(app_id, config, "/rpc", payload)
    finally:
        _calls.release()


def bounded_http(method: str, url: str, headers: dict, payload: dict | None, *, timeout: float):
    async def perform():
        # Wall-clock limit covers headers and drip responses, not just idle IO.
        async with asyncio.timeout(timeout):
            async with httpx.AsyncClient(trust_env=False, timeout=timeout) as client:
                async with client.stream(method, url, headers=headers, json=payload) as reply:
                    data = bytearray()
                    async for block in reply.aiter_bytes():
                        data.extend(block)
                        if len(data) > BODY_LIMIT:
                            raise ValueError("Mini-app response exceeds 64 KiB")
                    return reply.status_code, json.loads(data)

    return asyncio.run(perform())


def capability(token: str, payload: dict) -> Any:
    """Runtime tokens reach exactly one backend capability adapter, never fixed admin verbs."""
    if not _cap_calls.acquire(blocking=False):
        raise HTTPException(429, "Mini-app capability capacity reached")
    try:
        return _capability(token, payload)
    finally:
        _cap_calls.release()


def _capability(token: str, payload: dict) -> Any:
    if not token or len(token) > 128:
        raise HTTPException(401, "Invalid mini-app credential")
    # No app lock here: the app may be waiting for this while holding its RPC slot.
    for file in ROOT.glob("*/control.json"):
        config = load(file.parent.name)
        for runtime in (config.get("active"), config.get("candidate")):
            if runtime and not config.get("disabled") and hmac.compare_digest(token, runtime["token"]):
                status, result = bounded_http(
                    "POST",
                    f"{BACKEND}/internal/miniapps/{file.parent.name}/capability",
                    {"X-Internal-Token": INTERNAL_TOKEN},
                    {"revision": runtime["revision"], "request": payload},
                    timeout=25,
                )
                if status != 200:
                    raise HTTPException(status, "Capability denied or unavailable")
                return result
    raise HTTPException(401, "Mini-app credential revoked")


def remove(app_id: str) -> dict:
    with lock(app_id):
        config = recover(app_id, load(app_id))
        active = config.get("active")
        # Revoke first, including if container cleanup is interrupted.
        save(app_id, {**config, "disabled": True, "deleting": True})
        if active:
            stop(app_id, active, remove=True)
            with contextlib.suppress(NotFound):
                daemon.client().images.remove(name(app_id, f"release-{active['revision']}"))
        with contextlib.suppress(NotFound):
            net = daemon.client().networks.get(name(app_id, "net"))
            net.disconnect(socket.gethostname(), force=True)
            net.remove()
        # A failed unlink is NOT a successful deletion. Retain the tombstone
        # and backend source record so the same removal can be retried.
        shutil.rmtree(root(app_id))
        _last_used.pop(app_id, None)
        return {"removed": True}


def control(app_id: str, action: str) -> dict:
    if action not in {"suspend", "resume", "restart"}:
        raise ValueError("Unknown mini-app control")
    with lock(app_id):
        saved = recover(app_id, load(app_id))
        config = saved.get("active")
        if not config:
            return snapshot(app_id)
        stop(app_id, config, remove=True)
        config["token"] = secrets.token_urlsafe(32)
        saved["disabled"] = action == "suspend"
        save(app_id, saved)
        if action != "suspend":
            ready(app_id, config)
        _last_used[app_id] = time.monotonic()
        return snapshot(app_id)


_reconciler: threading.Thread | None = None
_reconcile_lock = threading.Lock()


def start_reconcile() -> None:
    global _reconciler
    with _reconcile_lock:
        if _reconciler is None or not _reconciler.is_alive():
            _reconciler = threading.Thread(target=reconcile, name="miniapp-maintenance", daemon=True)
            _reconciler.start()


def reconcile() -> None:
    if not ROOT.exists():
        return
    # Old compiler containers can survive a hard broker crash. Own the global
    # build slot so this cannot remove a compiler used by the current process.
    if _build_slot.acquire(blocking=False):
        try:
            filters = {
                "label": [
                    f"{key}={value}"
                    for key, value in labels("unused").items()
                    if key != "nautionette.miniapp"
                ]
            }
            filters["label"].append("nautionette.miniapp.build=true")
            for compiler in daemon.client().containers.list(all=True, filters=filters):
                compiler.remove(force=True)
        except Exception:
            daemon.log.warning("Mini-app compiler cleanup pending")
        finally:
            _build_slot.release()
    for folder in ROOT.iterdir():
        if not folder.is_dir() or not ID.fullmatch(folder.name):
            continue
        app_id = folder.name
        # Don't hold up worker/container reconciliation behind a compiler or app RPC.
        guard = lock(app_id)
        if not guard.acquire(blocking=False):
            continue
        try:
            saved = recover(app_id, load(app_id))
            if saved.get("deleting"):
                remove(app_id)
                continue
            config = saved.get("active")
            if saved.get("disabled") or not config:
                continue
            if config["mode"] == "background":
                ready(app_id, config)
            elif time.monotonic() - _last_used.get(app_id, 0) > 300:
                stop(app_id, config)
        except Exception:
            daemon.log.warning("Mini-app reconciliation failed: %s", app_id)
        finally:
            guard.release()
