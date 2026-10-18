"""Fixed, bounded ArtCraft release installer; immutable, deployment-owned CLI volumes."""

from __future__ import annotations

import hashlib
import json
import threading
from datetime import UTC, datetime
from typing import Any

from docker.errors import NotFound
from docker.types import Mount
from nautionette.artcraft import installation_id, release, runtime_selection

from . import daemon, images
from .config import WORKFLOWS_VOLUME

_slots = threading.BoundedSemaphore(2)


def volume_name(identifier: str) -> str:
    namespace = hashlib.sha256(WORKFLOWS_VOLUME.encode()).hexdigest()[:12]
    return f"nautionette-artcraft-{namespace}-{installation_id(identifier)}"


def _labels(identifier: str, spec: dict[str, str]) -> dict[str, str]:
    return {
        "nautionette.deployment": WORKFLOWS_VOLUME,
        "nautionette.artcraft": identifier,
        **{f"nautionette.artcraft.{key}": value for key, value in spec.items()},
    }


def mounts(selection: list[dict[str, Any]]) -> list[Mount]:
    result = []
    for item in runtime_selection(selection):
        if not (identifier := item.get("installation_id")):
            continue
        name = volume_name(identifier)
        volume = daemon.client().volumes.get(name)
        expected = _labels(identifier, item["release"])
        if any(volume.attrs.get("Labels", {}).get(key) != value for key, value in expected.items()):
            raise ValueError("ArtCraft artifact ownership/release mismatch")
        result.append(Mount(f"/opt/artcraft/{item['app']}", name, type="volume", read_only=True))
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
        # Do not delete an artifact: verification may have succeeded just before
        # the broker lost its HTTP reply. Active turns may still mount it.


def install(identifier: str, spec: dict[str, str]) -> dict[str, str]:
    spec = release(spec)
    name = volume_name(identifier)
    tag = images.image_tag("default")
    if not images.has_image(tag):
        raise ValueError("Agent image is not ready; retry after the image build completes")
    client = daemon.client()
    if not _slots.acquire(blocking=False):
        raise ValueError("Two ArtCraft installations are already running; retry shortly")
    container = network = volume = None
    succeeded = False
    try:
        if (
            len(
                client.volumes.list(
                    filters={"label": [f"nautionette.deployment={WORKFLOWS_VOLUME}", "nautionette.artcraft"]}
                )
            )
            >= 128
        ):
            raise ValueError("ArtCraft artifact storage limit reached; reclaim unused installations first")
        try:
            client.volumes.get(name)
        except NotFound:
            pass
        else:
            raise ValueError("ArtCraft installation ID already exists; artifacts are never overwritten")
        labels = _labels(identifier, spec)
        volume = client.volumes.create(name=name, labels=labels)
        network = client.networks.create(
            name=name,
            driver="bridge",
            labels=labels,
            options={"com.docker.network.bridge.enable_icc": "false"},
        )
        container = client.containers.create(
            tag,
            entrypoint=["python3", "/usr/local/lib/nautionette/artcraft/install.py"],
            environment={"ARTCRAFT_RELEASE": json.dumps(spec)},
            network=network.name,
            volumes={name: {"bind": "/artifact", "mode": "rw"}},
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
        succeeded = True
        return spec
    finally:
        try:
            for resource in (container, network, volume if not succeeded else None):
                if resource is None:
                    continue
                try:
                    resource.remove(force=True) if resource is container else resource.remove()
                except Exception:
                    daemon.log.exception("Could not clean up an ArtCraft installer resource")
        finally:
            _slots.release()
