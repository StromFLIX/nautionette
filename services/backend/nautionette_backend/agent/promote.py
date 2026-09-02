"""Turning a chat into a workflow draft a human can approve."""

from __future__ import annotations

import re
import textwrap
from typing import Any

from ..clients import authoring
from .prompts import DRAFT_SCHEMA, WORKFLOW_AUTHOR_PROMPT
from .runner import agent_job, call_agent


def slugify(text: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", (text or "").lower()).strip("_")
    slug = re.sub(r"_+", "_", slug)[:48]
    if not slug or not slug[0].isalpha():
        slug = f"wf_{slug}" if slug else "new_workflow"
    return slug[:60]


def summarise_for_title(text: str) -> str:
    first = (text or "").strip().splitlines()[0] if text else ""
    first = re.sub(r"\s+", " ", first)
    return (first[:60] + "...") if len(first) > 60 else (first or "New chat")


def extract_code(text: str) -> str | None:
    fenced = re.search(r"```(?:python)?\s*\n(.*?)```", text, re.S)
    if fenced:
        return fenced.group(1).strip() + "\n"
    if "MANIFEST" in text and "workflow.defn" in text:
        return text.strip() + "\n"
    return None


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


def _author(prompt: str, agent_set: str | None) -> dict[str, Any]:
    return agent_job(
        prompt=prompt,
        mode="activity",
        system_prompt=WORKFLOW_AUTHOR_PROMPT,
        output_schema=DRAFT_SCHEMA,
        agent_set=agent_set,
        timeout_seconds=600,
    )


async def promote_chat(chat: dict[str, Any], messages: list[dict[str, Any]]) -> dict[str, Any]:
    """Read a transcript, write a workflow draft, hand back a diff to approve."""
    transcript = "\n\n".join(
        f"{m['role'].upper()}: {m['content']}" for m in messages if m.get("content")
    )
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
        response = await call_agent(_author(prompt, chat.get("agent_set")))
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
            _author(
                "Your previous workflow file failed validation. Fix it and return the "
                f"complete file again.\n\nProblems:\n{problems}\n\nFile:\n```python\n{code}\n```",
                chat.get("agent_set"),
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
