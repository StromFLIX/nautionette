"""Regression coverage for the live digest's stale-placeholder extraction bug."""

import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from temporalio.exceptions import ApplicationError

PATH = Path(__file__).resolve().parents[2] / "workflows" / "github_weekly_activity_digest.py"
spec = importlib.util.spec_from_file_location("github_digest", PATH)
digest = importlib.util.module_from_spec(spec)
spec.loader.exec_module(digest)
SUMMARY = "# GitHub activity\n\n" + "A real report with dated activity and source links. " * 3


def test_final_report_overrides_stale_placeholder():
    answer = {
        "ok": True,
        "output": {"summary": "placeholder"},
        "text": 'Enough data.```json\n{"summary":"placeholder"}\n```\nCompiling now.'
        + json.dumps({"summary": SUMMARY}),
    }
    assert digest.digest_summary(answer) == SUMMARY.strip()


@pytest.mark.parametrize(
    "text",
    [
        json.dumps({"summary": SUMMARY}),
        "```json\n" + json.dumps({"summary": SUMMARY}) + "\n```",
        "",
    ],
)
def test_valid_final_report(text):
    answer = {"ok": True, "text": text, "output": {"summary": SUMMARY}}
    assert digest.digest_summary(answer) == SUMMARY.strip()


@pytest.mark.parametrize(
    "answer",
    [
        {"ok": False, "output": {"summary": SUMMARY}},
        {"ok": True, "output": {"summary": "placeholder"}},
        {"ok": True, "output": {"summary": " "}},
        {"ok": True, "output": {"summary": [SUMMARY]}},
        {"ok": True, "output": {"summary": SUMMARY}, "text": '{"summary": "unfinished'},
        {"ok": True, "text": "```json\n" + json.dumps({"summary": SUMMARY}) + '\n```\n{"wrong":true}'},
    ],
)
def test_invalid_answers_fail_instead_of_publishing(answer):
    with pytest.raises(ApplicationError) as error:
        digest.digest_summary(answer)
    assert error.value.non_retryable


@pytest.mark.asyncio
async def test_placeholder_never_reaches_save_artifact(monkeypatch):
    from datetime import UTC, datetime

    execute = AsyncMock(return_value={"ok": True, "output": {"summary": "placeholder"}})
    monkeypatch.setattr(digest.workflow, "execute_activity", execute)
    monkeypatch.setattr(digest.workflow, "now", lambda: datetime(2026, 9, 7, tzinfo=UTC))
    monkeypatch.setattr(digest.workflow, "info", lambda: SimpleNamespace(workflow_id="test"))
    with pytest.raises(ApplicationError):
        await digest.GithubWeeklyActivityDigest().run({})
    assert execute.await_count == 1
    assert execute.call_args.args[0] == "agent_call"
    assert "output_schema" not in execute.call_args.args[1]


@pytest.mark.asyncio
async def test_raw_final_answer_is_validated_and_saved(monkeypatch):
    from datetime import UTC, datetime

    execute = AsyncMock(
        side_effect=[
            {"ok": True, "output": None, "text": json.dumps({"summary": SUMMARY})},
            {"bytes": len(SUMMARY)},
        ]
    )
    monkeypatch.setattr(digest.workflow, "execute_activity", execute)
    monkeypatch.setattr(digest.workflow, "now", lambda: datetime(2026, 9, 7, tzinfo=UTC))
    monkeypatch.setattr(digest.workflow, "info", lambda: SimpleNamespace(workflow_id="test"))
    assert await digest.GithubWeeklyActivityDigest().run({}) == {"summary": SUMMARY.strip()}
    first, saved = execute.await_args_list
    assert "output_schema" not in first.args[1]
    assert "2026-08-31" in first.args[1]["prompt"]
    assert saved.args[0] == "save_artifact"
    assert SUMMARY.strip() in saved.args[1]["content"]
