"""Running one agent call.

Every agent call is one container run. Nothing is remembered inside the
container, so the history the model sees is exactly what we hand over here.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

from ..clients import broker
from ..config import settings
from .prompts import CHAT_SYSTEM_PROMPT

MAX_HISTORY_MESSAGES = 200
# Characters of transcript handed to a cold container. Overridable in settings;
# ~200k chars is roughly 50k tokens, which every current model has room for.
DEFAULT_HISTORY_CHARS = 200_000
MAX_MESSAGE_CHARS = 16_000
TOOL_RESULT_CHARS = 4_000


class Timeline:
    """The ordered story of one answer: the prose, and the tool calls between it.

    Kept so a reloaded chat shows the same interleaving the live stream did.
    """

    def __init__(self) -> None:
        self.steps: list[dict[str, Any]] = []

    def add_text(self, text: str) -> None:
        if not text:
            return
        if self.steps and self.steps[-1]["kind"] == "text":
            self.steps[-1]["text"] += text
        else:
            self.steps.append({"kind": "text", "text": text})

    def start_tool(self, event: dict[str, Any]) -> dict[str, Any]:
        step = {
            "kind": "tool",
            "id": event.get("id") or f"call-{len(self.steps)}",
            "name": event.get("name") or "?",
            "args": event.get("args"),
            "ok": None,
            "result": "",
        }
        self.steps.append(step)
        return step

    def finish_tool(self, event: dict[str, Any]) -> None:
        step = self._pending(event.get("id")) or self.start_tool(event)
        step["ok"] = not event.get("error")
        step["result"] = (event.get("result") or "")[:TOOL_RESULT_CHARS]

    def _pending(self, call_id: str | None) -> dict[str, Any] | None:
        for step in reversed(self.steps):
            if step["kind"] != "tool" or step["ok"] is not None:
                continue
            if call_id is None or step["id"] == call_id:
                return step
        return None

    @property
    def text(self) -> str:
        return "".join(s["text"] for s in self.steps if s["kind"] == "text").strip()

    @property
    def tools(self) -> list[str]:
        return [s["name"] for s in self.steps if s["kind"] == "tool"]


def build_history(
    messages: list[dict[str, Any]], max_chars: int = DEFAULT_HISTORY_CHARS
) -> list[dict[str, str]]:
    """Trim a transcript to something a cold container can be handed."""
    trimmed = [m for m in messages if m.get("role") in {"user", "assistant"}]
    trimmed = trimmed[-MAX_HISTORY_MESSAGES:]
    total = 0
    out: list[dict[str, str]] = []
    for message in reversed(trimmed):
        content = (message.get("content") or "")[:MAX_MESSAGE_CHARS]
        total += len(content)
        if total > max_chars:
            break
        out.append({"role": message["role"], "content": content})
    return list(reversed(out))


def agent_job(
    *,
    prompt: str,
    mode: str = "interactive",
    system_prompt: str | None = None,
    history: list[dict[str, str]] | None = None,
    output_schema: dict[str, Any] | None = None,
    agent_set: str | None = None,
    model: str | None = None,
    tools: list[str] | None = None,
    run_id: str = "",
    timeout_seconds: int = 900,
) -> dict[str, Any]:
    return {
        "mode": mode,
        "prompt": prompt,
        "system_prompt": system_prompt or CHAT_SYSTEM_PROMPT,
        "history": history or [],
        "output_schema": output_schema,
        "agent_set": agent_set or settings.default_agent_set,
        "model": model or settings.agent_model,
        # None means every federated tool; a list narrows the agent to those names.
        "tools": tools,
        "run_id": run_id,
        "timeout_seconds": timeout_seconds,
    }


async def stream_agent(job: dict[str, Any]) -> AsyncIterator[dict[str, Any]]:
    async for event in broker.run_agent(job, timeout=job.get("timeout_seconds", 900) + 30):
        yield event


async def call_agent(job: dict[str, Any]) -> dict[str, Any]:
    """Collect a whole agent run into one object. This is what an activity gets."""
    text_parts: list[str] = []
    result: dict[str, Any] | None = None
    error: str | None = None
    tools: list[str] = []

    async for event in stream_agent(job):
        kind = event.get("type")
        if kind == "delta":
            text_parts.append(event.get("text", ""))
        elif kind == "tool":
            tools.append(event.get("name", "?"))
        elif kind == "result":
            result = event
        elif kind == "error":
            error = event.get("message", "agent failed")

    if result is None:
        return {
            "ok": False,
            "text": "".join(text_parts),
            "output": None,
            "error": error or "agent produced no result",
            "tools": tools,
        }
    return {
        "ok": bool(result.get("ok", True)),
        "text": result.get("text") or "".join(text_parts),
        "output": result.get("output"),
        "error": result.get("error"),
        "tools": tools,
        "usage": result.get("usage"),
    }
