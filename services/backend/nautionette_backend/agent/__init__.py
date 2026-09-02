"""Agent calls and chat promotion."""

from __future__ import annotations

from .promote import extract_code, promote_chat, scaffold, slugify, summarise_for_title
from .prompts import CHAT_SYSTEM_PROMPT, DRAFT_SCHEMA, WORKFLOW_AUTHOR_PROMPT
from .runner import (
    DEFAULT_HISTORY_CHARS,
    MAX_HISTORY_MESSAGES,
    MAX_MESSAGE_CHARS,
    agent_job,
    build_history,
    call_agent,
    stream_agent,
)

__all__ = [
    "CHAT_SYSTEM_PROMPT",
    "DEFAULT_HISTORY_CHARS",
    "DRAFT_SCHEMA",
    "MAX_HISTORY_MESSAGES",
    "MAX_MESSAGE_CHARS",
    "WORKFLOW_AUTHOR_PROMPT",
    "agent_job",
    "build_history",
    "call_agent",
    "extract_code",
    "promote_chat",
    "scaffold",
    "slugify",
    "stream_agent",
    "summarise_for_title",
]
