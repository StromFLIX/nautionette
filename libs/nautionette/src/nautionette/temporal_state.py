"""Bounded, read-only completion receipts for deployment maintenance containers.

Uses a Docker container object's fixed archive/exec operations without importing
Docker or exposing a generic execution surface. Receipts contain no credentials.
"""

from __future__ import annotations

import io
import json
import re
import tarfile

COMPLETED_RECEIPT = "/opt/maintenance/completed.json"
MAX_RECEIPT_BYTES = 16 * 1024
MAX_ARCHIVE_BYTES = 64 * 1024
PUBLIC_FIELDS = ("status", "commit", "target", "shards", "safe_to_retry")


def _project(state: dict, container) -> dict:
    commit = container.labels.get("nautionette.temporal.commit", "")
    if not re.fullmatch(r"[0-9a-f]{40}", commit) or not isinstance(state, dict):
        raise ValueError("Invalid maintenance receipt identity")
    if state.get("commit") != commit or not isinstance(state.get("status"), str):
        raise ValueError("Maintenance receipt does not match its controller")
    return {key: state[key] for key in PUBLIC_FIELDS if key in state}


def completed_receipt(container) -> dict | None:
    """Read the small container-local receipt, also when the container is stopped."""
    try:
        chunks, _ = container.get_archive(COMPLETED_RECEIPT)
    except Exception as error:
        if getattr(getattr(error, "response", None), "status_code", None) == 404:
            return None
        raise
    data = bytearray()
    for chunk in chunks:
        if len(data) + len(chunk) > MAX_ARCHIVE_BYTES:
            raise ValueError("Maintenance receipt archive exceeds its size limit")
        data.extend(chunk)
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:") as archive:
        members = archive.getmembers()
        if len(members) != 1:
            raise ValueError("Unexpected maintenance receipt archive members")
        member = members[0]
        if not member.isfile() or member.name != "completed.json" or member.size > MAX_RECEIPT_BYTES:
            raise ValueError("Invalid maintenance receipt archive member")
        file = archive.extractfile(member)
        if file is None:
            raise ValueError("Missing maintenance receipt content")
        raw = file.read(MAX_RECEIPT_BYTES + 1)
    if len(raw) > MAX_RECEIPT_BYTES:
        raise ValueError("Maintenance receipt exceeds its size limit")
    state = _project(json.loads(raw), container)
    if state["status"] != "completed":
        raise ValueError("Receipt does not prove completed maintenance")
    return state


def controller_state(container) -> dict:
    """Prefer the immutable completion receipt; only live unfinished jobs use exec."""
    container.reload()
    receipt = completed_receipt(container)
    if receipt is not None:
        return receipt
    if container.status != "running":
        raise RuntimeError("Stopped maintenance controller has no verified receipt")
    # Backward compatible with pre-receipt controllers during the rollout.
    result = container.exec_run(["python", "/opt/maintenance/temporal_deploy_job.py", "status"])
    if result.exit_code or len(result.output) > MAX_RECEIPT_BYTES:
        raise RuntimeError("Cannot inspect durable Temporal maintenance state")
    return _project(json.loads(result.output), container)
