"""Isolated, immutable package artifacts. No caller-selected image/command/mount."""

from __future__ import annotations

import hashlib
import json
import threading
from datetime import UTC, datetime
from typing import Any

from docker.errors import NotFound
from docker.types import Mount
from nautionette.pi_packages import installation_source, revision_id

from . import daemon, images
from .config import WORKFLOWS_VOLUME

_slots = threading.BoundedSemaphore(2)


def volume_name(installation_id: str) -> str:
    namespace = hashlib.sha256(WORKFLOWS_VOLUME.encode()).hexdigest()[:12]
    return f"nautionette-package-{namespace}-{revision_id(installation_id)}"


def reconcile() -> None:
    """Bound orphan installer lifetime even if the broker was restarted mid-call.

    Never delete volumes here: a successful publication may have lost its HTTP
    reply. Artifact reclamation requires backend reference tracking.
    """
    client = daemon.client()
    for container in client.containers.list(
        all=True, filters={"label": [f"nautionette.deployment={WORKFLOWS_VOLUME}", "nautionette.package"]}
    ):
        created = datetime.fromisoformat(container.attrs["Created"].replace("Z", "+00:00"))
        if (datetime.now(UTC) - created).total_seconds() < 330:
            continue
        name = volume_name(container.labels["nautionette.package"])
        container.remove(force=True)
        try:
            client.networks.get(name).remove()
        except NotFound:
            pass


def mounts(installations: list[str]) -> list[Mount]:
    result = []
    for item in sorted(set(installations)):
        name = volume_name(item)
        # Never let Docker silently recreate a missing artifact as an empty volume.
        volume = daemon.client().volumes.get(name)
        if volume.attrs.get("Labels", {}).get("nautionette.package") != item:
            raise ValueError("Package artifact ownership mismatch")
        result.append(Mount(f"/opt/nautionette-packages/{item}", name, type="volume", read_only=True))
    return result


def install(installation_id: str, source: str, allow_scripts: bool = False) -> dict[str, Any]:
    parsed = installation_source(source)
    name = volume_name(installation_id)
    if type(allow_scripts) is not bool:
        raise ValueError("allow_scripts must be a boolean")
    tag = images.image_tag("default")
    if not images.has_image(tag):
        raise ValueError("Agent image is not ready; retry after the image build completes")
    if not _slots.acquire(blocking=False):
        raise ValueError("Two package installations are already running; retry shortly")
    client = daemon.client()
    container = network = volume = None
    succeeded = False
    try:
        try:
            client.volumes.get(name)
        except NotFound:
            pass
        else:
            raise ValueError("Installation ID already exists; artifacts are never overwritten")
        labels = {"nautionette.deployment": WORKFLOWS_VOLUME, "nautionette.package": installation_id}
        volume = client.volumes.create(name=name, labels=labels)
        # One ephemeral egress-only network, with no application containers/DNS.
        # This does not claim to be a general hostile-code network firewall.
        network = client.networks.create(
            name=name,
            driver="bridge",
            labels=labels,
            options={"com.docker.network.bridge.enable_icc": "false"},
        )
        container = client.containers.create(
            tag,
            entrypoint=["node", "/usr/local/lib/nautionette/package-install.mjs"],
            environment={"PACKAGE_SOURCE": json.dumps(parsed), "PACKAGE_SCRIPTS": str(allow_scripts).lower()},
            network=network.name,
            volumes={name: {"bind": "/artifact", "mode": "rw"}},
            read_only=True,
            tmpfs={"/tmp": "size=256m,exec,mode=1777"},  # noqa: S108 - private container tmpfs
            mem_limit="1g",
            nano_cpus=1_000_000_000,
            pids_limit=128,
            cap_drop=["ALL"],
            cap_add=["CHOWN", "SETUID", "SETGID"],
            security_opt=["no-new-privileges:true"],
            labels=labels,
            log_config={"Type": "json-file", "Config": {"max-size": "1m", "max-file": "1"}},
        )
        container.start()
        status = container.wait(timeout=300)
        lines = container.logs(stdout=True, stderr=False, tail=1).decode().strip()
        try:
            metadata = json.loads(lines)
        except (ValueError, UnicodeError) as exc:
            raise ValueError("Installer returned no valid result") from exc
        if status.get("StatusCode") != 0 or metadata.get("error"):
            raise ValueError(
                "Package install failed: verify public source, version, manifest and dependencies"
            )
        root = metadata.get("root", "")
        if not isinstance(root, str) or not root or root.startswith("/") or ".." in root.split("/"):
            raise ValueError("Installer returned an invalid artifact path")
        if not isinstance(metadata.get("resolved"), str) or len(metadata["resolved"]) > 500:
            raise ValueError("Installer returned an invalid resolution")
        succeeded = True
        return {key: metadata.get(key) for key in ("root", "resolved", "integrity", "resources")}
    finally:
        try:
            for resource in (container, network, volume if not succeeded else None):
                if resource is None:
                    continue
                try:
                    resource.remove(force=True) if resource is container else resource.remove()
                except Exception:
                    daemon.log.exception("Could not clean up a package installer resource")
        finally:
            _slots.release()
