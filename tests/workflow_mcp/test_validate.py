"""The check chain a write goes through, in order."""

from __future__ import annotations

import pytest
from nautionette_workflow_mcp.validate import MAX_SOURCE_BYTES, run_checks

from .conftest import GOOD_WORKFLOW


def steps(report):
    return {step["step"]: step["ok"] for step in report["steps"]}


@pytest.mark.slow
def test_a_good_file_passes_every_step():
    report = run_checks("url_digest", GOOD_WORKFLOW)
    assert report["valid"] is True, report["errors"]
    assert steps(report) == {
        "manifest": True,
        "workflow_class": True,
        "dependencies": True,
        "import": True,
    }
    assert report["classes"] == ["UrlDigest"]
    assert report["manifest"]["timeout_minutes"] == 30


def test_the_committed_workflows_still_validate(repo_root):
    files = sorted((repo_root / "workflows").glob("*.py"))
    assert files, "there should be workflows committed to the repository"
    for path in files:
        report = run_checks(path.stem, path.read_text(encoding="utf-8"))
        assert report["valid"], f"{path.name}: {report['errors']}"


def test_an_empty_file_stops_at_the_first_step():
    report = run_checks("url_digest", "   ")
    assert report == {
        "valid": False,
        "errors": ["file is empty"],
        "warnings": [],
        "manifest": None,
        "steps": [{"step": "source", "ok": False, "detail": "file is empty"}],
    }


def test_a_file_nobody_should_have_to_read_is_refused():
    report = run_checks("url_digest", "# " + "x" * MAX_SOURCE_BYTES)
    assert report["valid"] is False
    assert "larger than" in report["errors"][0]


def test_a_manifest_that_does_not_parse_stops_the_chain():
    report = run_checks("url_digest", "x = 1\n")
    assert report["valid"] is False
    assert steps(report) == {"manifest": False}


def test_the_name_in_the_manifest_has_to_be_the_name_on_the_file():
    report = run_checks("other_name", GOOD_WORKFLOW)
    assert report["valid"] is False
    assert any("expected 'other_name'" in error for error in report["errors"])


def test_a_file_with_no_workflow_class_is_not_a_workflow():
    code = 'MANIFEST = {"schema": 1, "name": "empty_flow", "inputs": {"type": "object"},' \
        ' "outputs": {"type": "object"}}\n'
    report = run_checks("empty_flow", code)
    assert report["valid"] is False
    assert steps(report)["workflow_class"] is False


def test_a_declared_dependency_that_is_not_a_package_is_refused():
    code = '# /// script\n# dependencies = ["--index-url=http://evil"]\n# ///\n' + GOOD_WORKFLOW
    report = run_checks("url_digest", code)
    assert report["valid"] is False
    assert steps(report)["dependencies"] is False


def test_a_workflow_that_does_nothing_says_so():
    code = '''MANIFEST = {
    "schema": 1,
    "name": "idle_flow",
    "inputs": {"type": "object"},
    "outputs": {"type": "object"},
}

from temporalio import workflow


@workflow.defn(name="idle_flow")
class IdleFlow:
    @workflow.run
    async def run(self, params: dict) -> dict:
        return {}
'''
    report = run_checks("idle_flow", code)
    assert "workflow calls no activities; it will do nothing on its own" in report["warnings"]


def test_a_workflow_that_is_all_model_is_nudged_towards_code():
    code = GOOD_WORKFLOW.replace('"http_fetch"', '"agent_call"')
    warnings = " ".join(run_checks("url_digest", code)["warnings"])
    assert "check whether http_fetch, mcp_call or plain" in warnings
    assert "output_schema" in warnings


def test_several_agent_calls_are_counted_as_several_chances_to_drift():
    code = GOOD_WORKFLOW.replace('"http_fetch"', '"agent_call"') + '\n# "agent_call" "agent_call"\n'
    warnings = " ".join(run_checks("url_digest", code)["warnings"])
    assert "3 agent calls in one workflow" in warnings
