"""The manifest schema and the source reader every service shares."""

from __future__ import annotations

import pytest
from nautionette.manifest import (
    SCHEMA_VERSION,
    input_problems,
    normalise_manifest,
    validate_manifest,
)
from nautionette.source import (
    SourceError,
    find_workflow_classes,
    parse_dependencies,
    parse_manifest,
    unified_diff,
)

VALID = {
    "schema": 1,
    "name": "url_digest",
    "inputs": {"type": "object", "properties": {"url": {"type": "string"}}, "required": ["url"]},
    "outputs": {"type": "object"},
}


def test_a_complete_manifest_has_no_problems():
    assert validate_manifest(VALID) == []


def test_a_manifest_must_be_a_dict():
    assert validate_manifest("MANIFEST") == ["manifest: must be a dict named MANIFEST at module level"]


@pytest.mark.parametrize("key", ["schema", "name", "inputs", "outputs"])
def test_every_required_key_is_required(key):
    assert validate_manifest({k: v for k, v in VALID.items() if k != key})


def test_a_name_must_be_usable_as_a_module_name():
    assert validate_manifest({**VALID, "name": "Url Digest"})


def test_inputs_must_be_a_json_schema_object():
    assert validate_manifest({**VALID, "inputs": {"type": "array"}})


def test_a_key_the_runtime_does_not_know_must_announce_itself():
    assert validate_manifest({**VALID, "surprise": 1})
    assert validate_manifest({**VALID, "x_surprise": 1}) == []


def test_a_file_from_a_newer_runtime_says_so():
    problems = validate_manifest({**VALID, "schema": SCHEMA_VERSION + 1})
    assert any("this runtime understands" in problem for problem in problems)


def test_defaults_are_filled_in_without_dropping_anything():
    normalised = normalise_manifest({"name": "url_digest", "x_owner": "me"})
    assert normalised["title"] == "Url Digest"
    assert normalised["agent_set"] == "default"
    assert normalised["timeout_minutes"] == 30
    assert normalised["source"] == "hand-written"
    assert normalised["x_owner"] == "me"


def test_a_declared_title_survives_normalisation():
    assert normalise_manifest({"name": "a_b", "title": "Mine"})["title"] == "Mine"


# ---------------------------------------------------------------- run inputs


def test_a_missing_required_input_is_caught_before_the_run_starts():
    assert input_problems(VALID["inputs"], {}) == ["'url' is a required property"]


def test_a_wrong_type_is_reported_with_its_field():
    assert input_problems(VALID["inputs"], {"url": 7}) == ["url: 7 is not of type 'string'"]


def test_a_workflow_without_an_input_schema_takes_anything():
    assert input_problems(None, {"anything": True}) == []


def test_input_has_to_be_an_object():
    assert input_problems(VALID["inputs"], ["a"]) == ["input must be a JSON object"]


# -------------------------------------------------------------------- source


SOURCE = '''
# /// script
# dependencies = ["feedparser", "python-dateutil>=2.9"]
# ///
from temporalio import workflow

MANIFEST = {"schema": 1, "name": "url_digest"}


@workflow.defn(name="url_digest")
class UrlDigest:
    pass


@defn
class AlsoCounted:
    pass


class NotAWorkflow:
    pass
'''


def test_a_manifest_is_read_without_importing_the_file():
    assert parse_manifest(SOURCE) == {"schema": 1, "name": "url_digest"}


def test_a_file_with_no_manifest_says_so():
    with pytest.raises(SourceError, match="no module level MANIFEST"):
        parse_manifest("x = 1")


def test_a_manifest_that_is_not_a_literal_is_refused():
    with pytest.raises(SourceError, match="literal"):
        parse_manifest("MANIFEST = dict(name='x')")


def test_a_file_that_does_not_parse_names_the_line():
    with pytest.raises(SourceError, match="line 1"):
        parse_manifest("def (:")


def test_workflow_classes_are_found_by_their_decorator():
    assert find_workflow_classes(SOURCE) == ["UrlDigest", "AlsoCounted"]


def test_declared_dependencies_are_read_from_the_pep_723_header():
    assert parse_dependencies(SOURCE) == ["feedparser", "python-dateutil>=2.9"]


def test_a_file_without_a_header_declares_nothing():
    assert parse_dependencies("MANIFEST = {}") == []


@pytest.mark.parametrize(
    "declared",
    [
        '"--index-url=http://evil"',  # never let a spec become a uv flag
        '"; rm -rf /"',
        ", ".join(f'"pkg{index}"' for index in range(21)),  # more than the ceiling
    ],
)
def test_a_dependency_that_is_not_a_plain_requirement_is_refused(declared):
    source = f"# /// script\n# dependencies = [{declared}]\n# ///\n"
    with pytest.raises(SourceError):
        parse_dependencies(source)


def test_a_header_that_is_not_toml_says_so():
    with pytest.raises(SourceError, match="not valid TOML"):
        parse_dependencies("# /// script\n# dependencies = [\n# ///\n")


def test_a_diff_is_something_a_human_can_read():
    diff = unified_diff("a\n", "b\n", "url_digest.py")
    assert "--- a/url_digest.py" in diff
    assert "-a" in diff and "+b" in diff
    assert unified_diff("a\n", "a\n", "x.py") == ""
