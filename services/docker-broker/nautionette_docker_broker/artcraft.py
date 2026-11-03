"""Verified immutable CLI volumes, prepared before agent readiness (no runtime downloads)."""

from __future__ import annotations

import hashlib
import json
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from typing import Any

from docker.errors import DockerException, NotFound
from docker.types import Mount
from nautionette.artcraft import (
    bundled_release,
    catalog,
    installation_id,
    release,
    release_installation_id,
    runtime_selection,
)

from . import daemon, images
from .config import WORKFLOWS_VOLUME

_slots = threading.BoundedSemaphore(2)
_install_lock = threading.Lock()
_installing: set[str] = set()
_verified: set[str] = set()


def volume_name(identifier: str) -> str:
    namespace = hashlib.sha256(WORKFLOWS_VOLUME.encode()).hexdigest()[:12]
    return f"nautionette-artcraft-{namespace}-{installation_id(identifier)}"


def _labels(identifier: str, spec: dict[str, str]) -> dict[str, str]:
    return {
        "nautionette.deployment": WORKFLOWS_VOLUME,
        "nautionette.artcraft": identifier,
        **{f"nautionette.artcraft.{key}": value for key, value in spec.items()},
    }


def _volume(identifier: str, spec: dict[str, str]):
    volume = daemon.client().volumes.get(volume_name(identifier))
    expected = _labels(identifier, spec)
    if any(volume.attrs.get("Labels", {}).get(key) != value for key, value in expected.items()):
        raise ValueError("ArtCraft artifact ownership/release mismatch")
    return volume


def bundled_ready() -> bool:
    try:
        for app in catalog():
            spec = bundled_release(app)
            identifier = release_installation_id(spec)
            if volume_name(identifier) not in _verified:
                return False
            _volume(identifier, spec)
        return True
    except (DockerException, ValueError):
        return False


def ensure_bundled(*, image: str | None = None) -> None:
    """Reuse verified artifacts offline; a missing default is installed at deployment."""

    def prepare(app):
        spec = bundled_release(app)
        identifier = release_installation_id(spec)
        name = volume_name(identifier)
        if name in _verified:
            try:
                _volume(identifier, spec)
                return
            except NotFound:
                _verified.discard(name)
        install(identifier, spec, image=image)

    with ThreadPoolExecutor(max_workers=2, thread_name_prefix="artcraft-prepare") as pool:
        list(pool.map(prepare, catalog()))


def mounts(selection: list[dict[str, Any]]) -> list[Mount]:
    result = []
    for item in runtime_selection(selection):
        spec = item["release"]
        identifier = item.get("installation_id") or release_installation_id(spec)
        _volume(identifier, spec)  # get, never implicitly create a missing volume
        name = volume_name(identifier)
        if name not in _verified:
            # Retained overrides/older queued defaults also need an offline
            # manifest/interface check on first use after broker restart.
            _helper(identifier, spec, images.image_tag("default"), verify=True)
            _verified.add(name)
        result.append(
            Mount(f"/opt/artcraft/{item['app']}", volume_name(identifier), type="volume", read_only=True)
        )
    return result


def reconcile() -> None:
    client = daemon.client()
    for container in client.containers.list(
        all=True,
        filters={
            "label": [f"nautionette.deployment={WORKFLOWS_VOLUME}", "nautionette.artcraft.installer=true"]
        },
    ):
        if getattr(container, "labels", {}).get("nautionette.artcraft.installer") != "true":
            continue
        created = datetime.fromisoformat(container.attrs["Created"].replace("Z", "+00:00"))
        if (datetime.now(UTC) - created).total_seconds() < 420:
            continue
        name = volume_name(container.labels["nautionette.artcraft"])
        container.remove(force=True)
        try:
            client.networks.get(name).remove()
        except NotFound:
            pass
        # Never delete a possibly published artifact after an ambiguous reply.


def _helper(identifier, spec, image, *, verify=False) -> None:
    client, container, network = daemon.client(), None, None
    name = volume_name(identifier)
    labels = _labels(identifier, spec)
    try:
        if not verify:
            network = client.networks.create(
                name=name,
                driver="bridge",
                labels=labels,
                options={"com.docker.network.bridge.enable_icc": "false"},
            )
        container = client.containers.create(
            image,
            entrypoint=[
                "python3",
                "/usr/local/lib/nautionette/artcraft/install.py",
                *(["--verify"] if verify else []),
            ],
            environment={"ARTCRAFT_RELEASE": json.dumps(spec)},
            network="none" if verify else network.name,
            volumes={name: {"bind": "/artifact", "mode": "ro" if verify else "rw"}},
            read_only=True,
            tmpfs={"/tmp": "size=768m,exec,mode=1777"},  # noqa: S108 - private installer tmpfs
            mem_limit="2g",
            nano_cpus=1_000_000_000,
            pids_limit=128,
            cap_drop=["ALL"],
            security_opt=["no-new-privileges:true"],
            labels={**labels, "nautionette.artcraft.installer": "true"},
            log_config={"Type": "json-file", "Config": {"max-size": "1m", "max-file": "1"}},
        )
        container.start()
        status = container.wait(timeout=300)
        raw = container.logs(stdout=True, stderr=False, tail=1).decode().strip()
        if status.get("StatusCode") != 0 or json.loads(raw) != spec:
            raise ValueError("ArtCraft release failed checksum/extraction/headless MCP verification")
    finally:
        # Do not remove a network/volume if helper cleanup fails: the helper may
        # still be writing. The stale-helper reconciler can retry its removal.
        if container is not None:
            container.remove(force=True)
        if network is not None:
            network.remove()


def install(identifier: str, spec: dict[str, str], *, image: str | None = None) -> dict[str, str]:
    spec = release(spec)
    name = volume_name(identifier)
    tag = image or images.image_tag("default")
    if not images.has_image(tag):
        raise ValueError("Agent image is not ready; retry after the image build completes")
    with _install_lock:
        if name in _installing:
            raise ValueError("This ArtCraft artifact is already being prepared; retry shortly")
        if not _slots.acquire(blocking=False):
            raise ValueError("Two ArtCraft installations are already running; retry shortly")
        _installing.add(name)
    volume, succeeded = None, False
    try:
        client = daemon.client()
        try:
            existing = _volume(identifier, spec)
        except NotFound:
            existing = None
        if existing is not None:
            # Labels alone are not proof: a crash can leave a partly extracted
            # volume. Verify the manifest and native interface without network.
            try:
                _helper(identifier, spec, tag, verify=True)
            except ValueError:
                # Docker refuses to remove volumes referenced by any container.
                # Never rewrite one in place or disrupt an existing turn.
                existing.remove()
                _verified.discard(name)
            else:
                _verified.add(name)
                return spec
        if (
            len(
                client.volumes.list(
                    filters={"label": [f"nautionette.deployment={WORKFLOWS_VOLUME}", "nautionette.artcraft"]}
                )
            )
            >= 128
        ):
            raise ValueError("ArtCraft artifact storage limit reached; reclaim unused installations first")
        volume = client.volumes.create(name=name, labels=_labels(identifier, spec))
        _helper(identifier, spec, tag)
        succeeded = True
        _verified.add(name)
        return spec
    finally:
        if volume is not None and not succeeded:
            try:
                volume.remove()
            except Exception:
                daemon.log.exception("Could not clean up an incomplete ArtCraft artifact")
        with _install_lock:
            _installing.discard(name)
            _slots.release()
