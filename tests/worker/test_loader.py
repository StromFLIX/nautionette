"""Loading what an agent wrote: a broken file is reported, never fatal."""

from __future__ import annotations

from nautionette_worker.loader import install_dependencies, load_workflows

WORKFLOW = '''from temporalio import workflow

MANIFEST = {{"schema": 1, "name": "{name}"}}


@workflow.defn(name="{name}")
class {cls}:
    @workflow.run
    async def run(self, params: dict) -> dict:
        return {{}}
'''


def write(directory, name, cls, body=None):
    path = directory / f"{name}.py"
    path.write_text(body if body is not None else WORKFLOW.format(name=name, cls=cls))
    return path


def test_a_good_file_is_loaded_and_reported(tmp_path):
    write(tmp_path, "url_digest", "UrlDigest")
    workflows, report = load_workflows(str(tmp_path))
    assert [cls.__name__ for cls in workflows] == ["UrlDigest"]
    assert report == [{"file": "url_digest.py", "workflows": ["UrlDigest"], "error": None}]


def test_a_broken_file_is_skipped_with_its_error(tmp_path):
    write(tmp_path, "good", "Good")
    write(tmp_path, "broken", "Broken", body="raise RuntimeError('boom')\n")
    workflows, report = load_workflows(str(tmp_path))
    assert [cls.__name__ for cls in workflows] == ["Good"]
    broken = next(item for item in report if item["file"] == "broken.py")
    assert broken["error"] == "RuntimeError: boom"


def test_a_file_with_a_broken_header_is_never_imported(tmp_path):
    write(tmp_path, "url_digest", "UrlDigest")
    workflows, report = load_workflows(str(tmp_path), {"url_digest.py": "the script block is not valid TOML"})
    assert workflows == []
    assert report[0]["error"] == "the script block is not valid TOML"


def test_a_private_file_is_not_a_workflow(tmp_path):
    write(tmp_path, "_helpers", "Helpers", body="x = 1\n")
    assert load_workflows(str(tmp_path)) == ([], [])


def test_two_files_claiming_one_name_keep_the_first(tmp_path):
    write(tmp_path, "a_flow", "First", body=WORKFLOW.format(name="shared", cls="First"))
    write(tmp_path, "b_flow", "Second", body=WORKFLOW.format(name="shared", cls="Second"))
    workflows, _ = load_workflows(str(tmp_path))
    assert [cls.__name__ for cls in workflows] == ["First"]


def test_a_missing_directory_is_created_rather_than_fatal(tmp_path):
    assert load_workflows(str(tmp_path / "not-there-yet")) == ([], [])
    assert (tmp_path / "not-there-yet").is_dir()


def test_a_declared_dependency_that_is_already_present_installs_nothing(tmp_path):
    write(tmp_path, "url_digest", "UrlDigest", body="# /// script\n# dependencies = [\"httpx\"]\n# ///\n")
    assert install_dependencies(str(tmp_path)) == (False, {})


def test_a_header_that_does_not_parse_is_reported_not_installed(tmp_path):
    write(tmp_path, "url_digest", "UrlDigest", body="# /// script\n# dependencies = [\n# ///\n")
    installed, bad = install_dependencies(str(tmp_path))
    assert installed is False
    assert "url_digest.py" in bad


def test_a_dependency_that_could_be_a_flag_is_refused_before_uv_sees_it(tmp_path, monkeypatch):
    def never(*_args, **_kwargs):
        raise AssertionError("uv must not be called with an unvetted spec")

    monkeypatch.setattr("subprocess.run", never)
    write(
        tmp_path,
        "url_digest",
        "UrlDigest",
        body='# /// script\n# dependencies = ["--index-url=http://evil"]\n# ///\n',
    )
    installed, bad = install_dependencies(str(tmp_path))
    assert installed is False
    assert "not a plain package requirement" in bad["url_digest.py"]
