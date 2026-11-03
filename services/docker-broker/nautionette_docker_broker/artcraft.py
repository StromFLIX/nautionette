"""Verified immutable CLI volumes, prepared before agent readiness (no runtime downloads)."""

from __future__ import annotations

import hashlib
import json
import tarfile
import tempfile
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack, contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from docker.errors import DockerException, NotFound
from docker.types import Mount
from nautionette.artcraft import (
    asset_name,
    bundled_release,
    catalog,
    installation_id,
    release,
    release_installation_id,
    runtime_selection,
)

from . import daemon, images
from .artcraft_offline import MAX_ARCHIVE
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
    if any((volume.attrs.get("Labels") or {}).get(key) != value for key, value in expected.items()):
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
    _reap_unused_networks()

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
        _volume(identifier, spec)
        name = volume_name(identifier)
        if name not in _verified:
            _helper(identifier, spec, images.image_tag("default"), verify=True)
            _verified.add(name)
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
        # Never delete a possibly published artifact after an ambiguous reply.
    _reap_unused_networks()


def _reap_unused_networks() -> None:
    # Upgrade cleanup only: new installers allocate NO bridge networks. Never
    # prune application networks, other deployments, or immutable artifacts.
    for network in daemon.client().networks.list(
        filters={"label": f"nautionette.deployment={WORKFLOWS_VOLUME}"}
    ):
        labels = network.attrs.get("Labels") or {}
        if labels.get("nautionette.deployment") != WORKFLOWS_VOLUME or not (
            labels.get("nautionette.artcraft")
            or labels.get("nautionette.artcraft.installer_network") == "true"
        ):
            continue
        try:
            network.reload()
        except NotFound:
            continue
        try:
            created = datetime.fromisoformat(network.attrs["Created"].replace("Z", "+00:00"))
        except (KeyError, ValueError):
            continue
        if network.attrs.get("Containers") != {} or (datetime.now(UTC) - created).total_seconds() < 420:
            continue
        try:
            network.remove()
        except DockerException:
            daemon.log.warning("Unused ArtCraft installer network cleanup deferred")


def _download(spec: dict[str, str], target: Path) -> None:
    # The broker handles only fixed HTTPS bytes, never extracts or executes them.
    # Native verification is network-none, so bootstrap works even when the host
    # has exhausted Docker's automatic bridge-address pools.
    url = f"https://github.com/storytold/{spec['app']}/releases/download/v{spec['version']}/" + asset_name(
        spec["app"], spec["version"], spec["arch"]
    )
    for attempt in range(3):
        try:
            request = urllib.request.Request(url, headers={"User-Agent": "Nautionette-ArtCraft"})  # noqa: S310 - fixed upstream and validated release fields
            digest, size = hashlib.sha256(), 0
            with urllib.request.urlopen(request, timeout=60) as incoming, target.open("wb") as out:  # noqa: S310 - fixed upstream
                while chunk := incoming.read(1024 * 1024):
                    size += len(chunk)
                    if size > MAX_ARCHIVE:
                        raise ValueError("Release archive exceeds 256 MiB")
                    digest.update(chunk)
                    out.write(chunk)
            if digest.hexdigest() != spec["sha256"]:
                raise ValueError("Release checksum mismatch")
            return
        except (OSError, TimeoutError) as exc:
            if isinstance(exc, urllib.error.HTTPError) and exc.code in {400, 401, 403, 404, 410}:
                raise ValueError("Pinned ArtCraft release archive is unavailable") from exc
            if attempt == 2:
                raise
            time.sleep(attempt + 1)


@contextmanager
def _staged_archive(spec):
    with tempfile.TemporaryDirectory(prefix="artcraft-download-") as work:
        archive = Path(work) / "release.tar.gz"
        _download(spec, archive)

        # Stream a bounded tar through Docker, not a 256-MiB Python bytes object.
        # The writable artifact volume is accessible before start; /tmp tmpfs is
        # not, so the fixed helper moves staged files there before extraction.
        def root_owned(info):
            info.uid = info.gid = 0
            info.uname = info.gname = ""
            info.mode = 0o600 if info.name == ".release.tar.gz" else 0o644
            return info

        with tempfile.TemporaryFile() as staging:
            with tarfile.open(fileobj=staging, mode="w") as tar:
                tar.add(archive, arcname=".release.tar.gz", recursive=False, filter=root_owned)
                tar.add(
                    Path(__file__).with_name("artcraft_offline.py"),
                    arcname=".install.py",
                    recursive=False,
                    filter=root_owned,
                )
            staging.seek(0)
            yield staging


def _helper(identifier, spec, image, *, verify=False) -> None:
    container = None
    name = volume_name(identifier)
    try:
        # Download before creating the helper, so the existing 420-second
        # stale-helper bound covers only archive upload + bounded native work.
        with ExitStack() as staging_resources:
            staging = staging_resources.enter_context(_staged_archive(spec)) if not verify else None
            container = daemon.client().containers.create(
                image,
                entrypoint=(
                    ["python3", "/usr/local/lib/nautionette/artcraft/install.py", "--verify"]
                    if verify
                    else ["python3", "/artifact/.install.py"]
                ),
                environment={"ARTCRAFT_RELEASE": json.dumps(spec)},
                network="none",
                volumes={name: {"bind": "/artifact", "mode": "ro" if verify else "rw"}},
                read_only=True,
                tmpfs={"/tmp": "size=768m,exec,mode=1777"},  # noqa: S108 - private installer tmpfs
                mem_limit="2g",
                nano_cpus=1_000_000_000,
                pids_limit=128,
                cap_drop=["ALL"],
                security_opt=["no-new-privileges:true"],
                labels={**_labels(identifier, spec), "nautionette.artcraft.installer": "true"},
                log_config={"Type": "json-file", "Config": {"max-size": "1m", "max-file": "1"}},
            )
            if staging is not None and not container.put_archive("/artifact", staging):
                raise ValueError("Could not stage the verified ArtCraft archive")
        container.start()
        status = container.wait(timeout=300)
        raw = container.logs(stdout=True, stderr=False, tail=1).decode().strip()
        if status.get("StatusCode") != 0 or json.loads(raw) != spec:
            raise ValueError("ArtCraft release failed checksum/extraction/headless MCP verification")
    finally:
        if container is not None:
            container.remove(force=True)


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
            try:
                _helper(identifier, spec, tag, verify=True)
            except ValueError:
                # Docker refuses removal of volumes referenced by any container.
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
