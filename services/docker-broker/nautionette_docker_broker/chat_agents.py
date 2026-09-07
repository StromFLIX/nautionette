"""Fixed chat-agent inventory, scoped to this deployment's workflows volume.

Chat/turn IDs can be copied into staging. A label alone is never enough: even
legacy containers must mount this broker's workflows volume at /workflows.
Workflow agents have no chat/turn pair and are never included.
"""

from __future__ import annotations

from typing import Any

from . import daemon
from .config import WORKFLOWS_VOLUME


def containers(chat_id: str = "", turn_id: str = "", *, include_stopped: bool = True) -> list[Any]:
    labels = [
        f"nautionette.chat={chat_id}" if chat_id else "nautionette.chat",
        f"nautionette.turn={turn_id}" if turn_id else "nautionette.turn",
    ]
    return [
        container
        for container in daemon.client().containers.list(all=include_stopped, filters={"label": labels})
        if container.labels.get("nautionette.chat")
        and container.labels.get("nautionette.turn")
        and (not chat_id or container.labels["nautionette.chat"] == chat_id)
        and (not turn_id or container.labels["nautionette.turn"] == turn_id)
        and any(
            mount.get("Type") == "volume"
            and mount.get("Name") == WORKFLOWS_VOLUME
            and mount.get("Destination") == "/workflows"
            for mount in container.attrs.get("Mounts", [])
        )
    ]
