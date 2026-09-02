"""Building a job for a cold container, and turning a chat into a workflow file."""

from __future__ import annotations

import ast

import pytest
from nautionette_backend import agent


def messages(*pairs):
    return [{"role": role, "content": content} for role, content in pairs]


def test_only_the_two_roles_a_model_understands_survive():
    history = agent.build_history(messages(("user", "a"), ("system", "b"), ("assistant", "c")))
    assert history == [{"role": "user", "content": "a"}, {"role": "assistant", "content": "c"}]


def test_the_transcript_is_trimmed_from_the_front():
    history = agent.build_history(messages(("user", "old"), ("user", "new")), max_chars=4)
    assert history == [{"role": "user", "content": "new"}]


def test_one_enormous_message_cannot_swallow_the_budget():
    history = agent.build_history(messages(("user", "x" * 100_000)))
    assert len(history[0]["content"]) == agent.MAX_MESSAGE_CHARS


def test_only_the_last_two_hundred_turns_are_considered():
    history = agent.build_history(messages(*[("user", str(i)) for i in range(500)]))
    assert len(history) == agent.MAX_HISTORY_MESSAGES
    assert history[-1]["content"] == "499"


def test_a_job_falls_back_to_the_configured_defaults():
    job = agent.agent_job(prompt="hello")
    assert job["agent_set"] == "default"
    assert job["model"] == "openai/gpt-4o-mini"
    assert job["system_prompt"] == agent.CHAT_SYSTEM_PROMPT
    assert job["tools"] is None
    assert job["timeout_seconds"] == 900


@pytest.mark.parametrize(
    ("title", "expected"),
    [
        ("Release digest", "release_digest"),
        ("  Weekly   report! ", "weekly_report"),
        ("2026 plans", "wf_2026_plans"),
        ("", "new_workflow"),
        ("!!!", "new_workflow"),
    ],
)
def test_a_title_becomes_a_module_name(title, expected):
    assert agent.slugify(title) == expected


def test_a_slug_stays_short_enough_to_be_a_filename():
    assert len(agent.slugify("word " * 40)) <= 60


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("here you go\n```python\nMANIFEST = {}\n```\n", "MANIFEST = {}\n"),
        ("```\nMANIFEST = {}\n```", "MANIFEST = {}\n"),
        ("MANIFEST = {}\n@workflow.defn\nclass A: ...", "MANIFEST = {}\n@workflow.defn\nclass A: ...\n"),
        ("sorry, I cannot do that", None),
    ],
)
def test_code_is_recovered_from_prose(text, expected):
    assert agent.extract_code(text) == expected


def test_the_scaffold_is_a_file_python_can_parse():
    code = agent.scaffold("daily_digest", "Daily digest", "summarise the news", "USER: hi")
    ast.parse(code)
    assert 'MANIFEST' in code
    assert '@workflow.defn(name="daily_digest")' in code
    assert "class DailyDigest:" in code


def test_the_scaffold_quotes_the_conversation_it_came_from():
    assert "USER: summarise the news" in agent.scaffold("d", "D", "x", "USER: summarise the news")


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Summarise this\nand that", "Summarise this"),
        ("   spaced    out   ", "spaced out"),
        ("", "New chat"),
        ("x" * 100, "x" * 60 + "..."),
    ],
)
def test_a_first_message_becomes_a_title(text, expected):
    assert agent.summarise_for_title(text) == expected
