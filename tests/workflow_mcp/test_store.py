"""The file store: live workflows next to the drafts waiting for approval."""

from __future__ import annotations

import pytest
from nautionette_workflow_mcp import store

from .conftest import GOOD_WORKFLOW


@pytest.mark.parametrize("name", ["url_digest", "abc", "a_1_b"])
def test_a_usable_name_is_accepted(name):
    assert store.check_name(name) == name


@pytest.mark.parametrize("name", ["", "ab", "Url_Digest", "url-digest", "1digest", "../escape", "a" * 65])
def test_a_name_that_could_not_be_a_module_is_refused(name):
    with pytest.raises(store.StoreError):
        store.check_name(name)


def test_a_draft_carries_the_diff_a_human_approves(workflows):
    draft = store.write_draft("url_digest", GOOD_WORKFLOW, message="promoted from chat abc")
    assert draft["is_new"] is True
    assert "+++ b/url_digest.py" in draft["diff"]
    assert draft["meta"]["message"] == "promoted from chat abc"
    assert draft["manifest"]["title"] == "URL digest"
    assert draft["classes"] == ["UrlDigest"]


def test_publishing_moves_the_draft_onto_the_live_file(workflows):
    store.write_draft("url_digest", GOOD_WORKFLOW)
    result = store.publish_draft("url_digest")
    assert result["published"] is True
    assert (workflows / "url_digest.py").read_text() == GOOD_WORKFLOW
    assert store.list_drafts() == []
    assert [entry["name"] for entry in store.list_workflows()] == ["url_digest"]


def test_a_second_draft_diffs_against_what_is_live(workflows):
    store.write_draft("url_digest", GOOD_WORKFLOW)
    store.publish_draft("url_digest")
    draft = store.write_draft("url_digest", GOOD_WORKFLOW.replace("[:200]", "[:400]"))
    assert draft["is_new"] is False
    assert "-        return {\"summary\": page[\"body\"][:200]}" in draft["diff"]


def test_discarding_a_draft_leaves_the_live_file_alone(workflows):
    store.write_draft("url_digest", GOOD_WORKFLOW)
    store.publish_draft("url_digest")
    store.write_draft("url_digest", "broken")
    assert store.discard_draft("url_digest") == {"name": "url_digest", "discarded": True}
    assert store.read_workflow("url_digest")["code"] == GOOD_WORKFLOW


def test_reading_something_that_is_not_there_says_which(workflows):
    with pytest.raises(store.StoreError, match="workflow 'nope' does not exist"):
        store.read_workflow("nope")
    with pytest.raises(store.StoreError, match="draft 'nope' does not exist"):
        store.read_draft("nope")
    with pytest.raises(store.StoreError, match="does not exist"):
        store.delete_workflow("nope")
    with pytest.raises(store.StoreError, match="does not exist"):
        store.publish_draft("nope")


def test_a_private_file_is_not_a_workflow(workflows):
    (workflows / "_helpers.py").write_text("x = 1")
    (workflows / "url_digest.py").write_text(GOOD_WORKFLOW)
    assert [entry["name"] for entry in store.list_workflows()] == ["url_digest"]


def test_a_file_that_cannot_be_read_is_listed_with_its_problem(workflows):
    (workflows / "broken.py").write_text("def (:\n")
    entry = store.list_workflows()[0]
    assert entry["manifest"] is None
    assert entry["title"] == "broken"
    assert entry["problems"] and "line 1" in entry["problems"][0]


def test_a_manifest_the_schema_rejects_is_reported_not_hidden(workflows):
    (workflows / "wrong.py").write_text('MANIFEST = {"schema": 1, "name": "wrong"}\n')
    entry = store.list_workflows()[0]
    assert any("outputs" in problem for problem in entry["problems"])


def test_deleting_a_workflow_removes_the_file(workflows):
    (workflows / "url_digest.py").write_text(GOOD_WORKFLOW)
    assert store.delete_workflow("url_digest") == {"name": "url_digest", "deleted": True}
    assert not (workflows / "url_digest.py").exists()
