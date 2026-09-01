"""Agent calls and chat promotion.

Every agent call is one container run. Nothing is remembered inside the
container, so the history the model sees is exactly what we hand over here.
"""

from __future__ import annotations

import json
import re
import textwrap
from collections.abc import AsyncIterator
from typing import Any

from .clients import authoring, broker
from .config import settings

MAX_HISTORY_MESSAGES = 30
MAX_HISTORY_CHARS = 24000

CHAT_SYSTEM_PROMPT = """\
You are the agent inside Nautionette, a chat app where a conversation can become a
scheduled, durable workflow.

How to behave:
- Answer directly and concisely. Prefer doing over describing.
- You have tools from the gateway when they are available; use them instead of guessing.
- When the user describes something repeatable ("every morning", "whenever X happens"),
  offer to turn it into a workflow and say what its inputs would be.
- When the user asks for a workflow, write one with the `write_workflow` tool. There is no
  button for this: asking you is how it happens. Say which inputs you chose and that the
  draft is waiting under Flows.
- `write_workflow` only ever creates a draft. Never claim a workflow is live or scheduled;
  a human approves the diff first.
"""

WORKFLOW_AUTHOR_PROMPT = """\
You turn a conversation into a Temporal workflow file for Nautionette.

Rules for the file you produce:
- Plain Python, one module, no imports beyond `datetime`, `typing` and `temporalio`.
- A module level `MANIFEST` dict literal: schema=1, name (snake_case), title, description,
  inputs and outputs as JSON Schema objects with "type": "object", agent_set.
- Exactly one class decorated with `@workflow.defn(name=<same name as MANIFEST>)` and one
  method decorated with `@workflow.run` taking `(self, params: dict) -> dict`.
- Call activities by string name only. Available activities:
  * "agent_call" -> {"prompt": str, "system_prompt": str?, "output_schema": dict?, "agent_set": str?}
    returns {"text": str, "output": dict|None}
  * "http_fetch" -> {"url": str, "method": str?, "headers": dict?, "json": dict?}
    returns {"status": int, "body": str, "json": any}
  * "emit_event" -> {"kind": str, "payload": dict} returns {"ok": true}
  * "save_artifact" -> {"name": str, "content": str} returns {"path": str}
- Always pass `start_to_close_timeout=timedelta(minutes=...)` to execute_activity.
- Anything the chat fixed (a date, a repo, a customer) becomes a key in MANIFEST["inputs"]
  and is read from `params`.
- Return a dict that matches MANIFEST["outputs"].

This is the shape. Follow it exactly; only the names, the schemas and the body change.

```python
from datetime import timedelta

from temporalio import workflow

MANIFEST = {
    "schema": 1,
    "name": "release_digest",
    "title": "Release digest",
    "description": "Summarise release notes for one product.",
    "inputs": {
        "type": "object",
        "properties": {"url": {"type": "string"}},
        "required": ["url"],
    },
    "outputs": {"type": "object", "properties": {"summary": {"type": "string"}}},
    "agent_set": "default",
    "source": "chat",
}


@workflow.defn(name="release_digest")
class ReleaseDigest:
    @workflow.run
    async def run(self, params: dict) -> dict:
        page = await workflow.execute_activity(
            "http_fetch",
            {"url": params["url"]},
            start_to_close_timeout=timedelta(minutes=5),
        )
        answer = await workflow.execute_activity(
            "agent_call",
            {
                "prompt": f"Summarise these release notes:\\n\\n{page['body'][:4000]}",
                "output_schema": {
                    "type": "object",
                    "properties": {"summary": {"type": "string"}},
                    "required": ["summary"],
                },
            },
            start_to_close_timeout=timedelta(minutes=10),
        )
        return answer.get("output") or {"summary": answer.get("text", "")}
```

Do not write a plain script. Do not use `requests`, `schedule`, `time.sleep` or `__main__`:
scheduling and retries belong to Temporal, not to the file.
"""


def build_history(messages: list[dict[str, Any]]) -> list[dict[str, str]]:
    """Trim a transcript to something a cold container can be handed."""
    trimmed = [m for m in messages if m.get("role") in {"user", "assistant"}]
    trimmed = trimmed[-MAX_HISTORY_MESSAGES:]
    total = 0
    out: list[dict[str, str]] = []
    for message in reversed(trimmed):
        content = (message.get("content") or "")[:4000]
        total += len(content)
        if total > MAX_HISTORY_CHARS:
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


# --------------------------------------------------------------------- promote

DRAFT_SCHEMA = {
    "type": "object",
    "properties": {
        "name": {"type": "string"},
        "title": {"type": "string"},
        "description": {"type": "string"},
        "code": {"type": "string"},
    },
    "required": ["name", "code"],
}


def slugify(text: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", (text or "").lower()).strip("_")
    slug = re.sub(r"_+", "_", slug)[:48]
    if not slug or not slug[0].isalpha():
        slug = f"wf_{slug}" if slug else "new_workflow"
    return slug[:60]


def scaffold(name: str, title: str, description: str, transcript: str) -> str:
    """A workflow the user can read and finish by hand when no model answered."""
    quoted = textwrap.indent(transcript[:1500].strip(), "    ")
    class_name = "".join(part.title() for part in name.split("_")) or "PromotedChat"
    return f'''"""{title}

Drafted from a chat. Edit freely: this is a normal Python file.

Transcript excerpt:
{quoted}
"""

from datetime import timedelta

from temporalio import workflow

MANIFEST = {{
    "schema": 1,
    "name": "{name}",
    "title": "{title}",
    "description": {description!r},
    "inputs": {{
        "type": "object",
        "properties": {{
            "topic": {{"type": "string", "description": "What this run is about"}}
        }},
        "required": [],
    }},
    "outputs": {{
        "type": "object",
        "properties": {{"summary": {{"type": "string"}}}},
    }},
    "agent_set": "default",
    "source": "chat",
}}


@workflow.defn(name="{name}")
class {class_name}:
    @workflow.run
    async def run(self, params: dict) -> dict:
        topic = params.get("topic") or {description!r}
        answer = await workflow.execute_activity(
            "agent_call",
            {{
                "prompt": f"{{topic}}\\n\\nAnswer in a short paragraph.",
                "output_schema": {{
                    "type": "object",
                    "properties": {{"summary": {{"type": "string"}}}},
                    "required": ["summary"],
                }},
            }},
            start_to_close_timeout=timedelta(minutes=10),
        )
        output = answer.get("output") or {{"summary": answer.get("text", "")}}
        await workflow.execute_activity(
            "emit_event",
            {{
                "kind": "workflow.note",
                "payload": {{"workflow": "{name}", "summary": output.get("summary", "")[:200]}},
            }},
            start_to_close_timeout=timedelta(minutes=1),
        )
        return output
'''


def extract_code(text: str) -> str | None:
    fenced = re.search(r"```(?:python)?\s*\n(.*?)```", text, re.S)
    if fenced:
        return fenced.group(1).strip() + "\n"
    if "MANIFEST" in text and "workflow.defn" in text:
        return text.strip() + "\n"
    return None


async def promote_chat(chat: dict[str, Any], messages: list[dict[str, Any]]) -> dict[str, Any]:
    """Read a transcript, write a workflow draft, hand back a diff to approve."""
    transcript = "\n\n".join(f"{m['role'].upper()}: {m['content']}" for m in messages if m.get("content"))
    title = chat.get("title") or "Promoted chat"
    name = slugify(title)

    prompt = (
        "Turn this conversation into a workflow file.\n\n"
        f"Conversation:\n{transcript[:12000]}\n\n"
        "Return JSON with keys name, title, description and code. `code` is the complete "
        "Python file."
    )

    generated: dict[str, Any] | None = None
    agent_error: str | None = None
    try:
        response = await call_agent(
            agent_job(
                prompt=prompt,
                mode="activity",
                system_prompt=WORKFLOW_AUTHOR_PROMPT,
                output_schema=DRAFT_SCHEMA,
                agent_set=chat.get("agent_set"),
                timeout_seconds=600,
            )
        )
        if response.get("ok"):
            generated = response.get("output") or {}
            if not generated.get("code"):
                code = extract_code(response.get("text") or "")
                generated = {"name": name, "code": code} if code else None
        else:
            agent_error = response.get("error")
    except Exception as exc:  # noqa: BLE001 - promotion must degrade, not explode
        agent_error = str(exc)

    if generated and generated.get("code"):
        name = slugify(generated.get("name") or name)
        code = generated["code"]
        origin = "agent"
    else:
        code = scaffold(name, title, (transcript[:200] or title).replace("\n", " "), transcript)
        origin = "scaffold"

    report = await authoring.validate(name, code)
    if not report.get("valid") and origin == "agent":
        problems = "\n".join(f"- {p}" for p in report.get("errors", []))
        repair = await call_agent(
            agent_job(
                prompt=(
                    "Your previous workflow file failed validation. Fix it and return the "
                    f"complete file again.\n\nProblems:\n{problems}\n\nFile:\n```python\n{code}\n```"
                ),
                mode="activity",
                system_prompt=WORKFLOW_AUTHOR_PROMPT,
                output_schema=DRAFT_SCHEMA,
                agent_set=chat.get("agent_set"),
                timeout_seconds=600,
            )
        )
        repaired = (repair.get("output") or {}).get("code") or extract_code(repair.get("text") or "")
        if repaired:
            code = repaired
            report = await authoring.validate(name, code)

    if not report.get("valid"):
        code = scaffold(name, title, (transcript[:200] or title).replace("\n", " "), transcript)
        origin = "scaffold"
        report = await authoring.validate(name, code)

    draft = await authoring.write_draft(name, code, message=f"promoted from chat {chat['id']}")
    draft["origin"] = origin
    draft["agent_error"] = agent_error
    draft["validation"] = report
    return draft


def summarise_for_title(text: str) -> str:
    first = (text or "").strip().splitlines()[0] if text else ""
    first = re.sub(r"\s+", " ", first)
    return (first[:60] + "...") if len(first) > 60 else (first or "New chat")


def json_or_none(text: str) -> Any:
    try:
        return json.loads(text)
    except (TypeError, ValueError):
        return None
