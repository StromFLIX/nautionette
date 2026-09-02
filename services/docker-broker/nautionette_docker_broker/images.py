"""Building the agent images, once at startup and never during a call.

An image is tagged by the hash of the context that built it, so a changed agent
set is simply a different image rather than something anyone has to invalidate.
"""

from __future__ import annotations

import hashlib
import os
import threading
import time
from pathlib import Path
from typing import Any

from docker.errors import DockerException, ImageNotFound

from . import daemon
from .config import AGENT_IMAGES_DIR, BASE_IMAGE, IMAGE_PREFIX

state_lock = threading.Lock()
image_state: dict[str, Any] = {"status": "pending", "images": {}, "log": [], "error": None}

_build_thread: threading.Thread | None = None


def note(message: str) -> None:
    daemon.log.info(message)
    with state_lock:
        image_state["log"] = (image_state["log"] + [message])[-40:]


def snapshot() -> dict[str, Any]:
    with state_lock:
        return dict(image_state)


def discovered_agent_sets() -> list[str]:
    sets_dir = os.path.join(AGENT_IMAGES_DIR, "agent-sets")
    if not os.path.isdir(sets_dir):
        return []
    return sorted(
        name
        for name in os.listdir(sets_dir)
        if os.path.isfile(os.path.join(sets_dir, name, "Dockerfile"))
    )


def _context_hash(path: str) -> str:
    digest = hashlib.sha256()
    root = Path(path)
    for file in sorted(p for p in root.rglob("*") if p.is_file()):
        digest.update(str(file.relative_to(root)).encode())
        digest.update(file.read_bytes())
    return digest.hexdigest()[:12]


def base_hash() -> str:
    return _context_hash(os.path.join(AGENT_IMAGES_DIR, "pi-base"))


def _agent_set_hash(agent_set: str) -> str:
    directory = os.path.join(AGENT_IMAGES_DIR, "agent-sets", agent_set)
    if not os.path.isdir(directory):
        return "missing"
    return hashlib.sha256((_context_hash(directory) + base_hash()).encode()).hexdigest()[:12]


def image_tag(agent_set: str) -> str:
    return f"{IMAGE_PREFIX}agent-{agent_set}:{_agent_set_hash(agent_set)}"


def has_image(tag: str) -> bool:
    try:
        daemon.client().images.get(tag)
        return True
    except (ImageNotFound, DockerException):
        return False


def _build(path: str, tag: str) -> None:
    note(f"building {tag}")
    started = time.time()
    _, logs = daemon.client().images.build(path=path, tag=tag, rm=True, pull=False)
    for chunk in logs:
        stream = (chunk.get("stream") or "").strip()
        if stream:
            daemon.log.debug(stream)
    note(f"built {tag} in {time.time() - started:.0f}s")


def ensure_images(force: bool = False) -> None:
    """Built at startup, and again whenever a call finds one missing. A call never waits."""
    with state_lock:
        image_state["status"] = "building"
        image_state["error"] = None
    try:
        base_tag = f"{IMAGE_PREFIX}base:{base_hash()}"
        if force or not has_image(base_tag):
            _build(os.path.join(AGENT_IMAGES_DIR, "pi-base"), base_tag)
        else:
            note(f"{base_tag} already present")
        # Agent set Dockerfiles say FROM <BASE_IMAGE>, so point that alias here.
        repository, _, alias = BASE_IMAGE.rpartition(":")
        daemon.client().images.get(base_tag).tag(repository, alias)

        images: dict[str, Any] = {"base": base_tag}
        for agent_set in discovered_agent_sets():
            tag = image_tag(agent_set)
            if force or not has_image(tag):
                _build(os.path.join(AGENT_IMAGES_DIR, "agent-sets", agent_set), tag)
            else:
                note(f"{tag} already present")
            images[agent_set] = tag
        with state_lock:
            image_state["images"] = images
            image_state["status"] = "ready"
    except Exception as exc:  # noqa: BLE001 - report, never crash the broker
        daemon.log.exception("image build failed")
        with state_lock:
            image_state["status"] = "failed"
            image_state["error"] = str(exc)[:500]


def start_build(force: bool = False) -> bool:
    """Single-flight: False when a build is already running."""
    global _build_thread
    with state_lock:
        if _build_thread is not None and _build_thread.is_alive():
            return False
        # Marked here, not in the thread, so a caller waiting on us never reads
        # the outcome of the previous build as if it were this one's.
        image_state["status"] = "building"
        image_state["error"] = None
        _build_thread = threading.Thread(
            target=ensure_images, kwargs={"force": force}, name="image-build", daemon=True
        )
        _build_thread.start()
    return True
