"""Retry network setup, but keep persistent failures and app test failures red."""

import os
import subprocess
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/install_playwright_ci.sh"


@pytest.mark.parametrize(
    ("failures", "expected_status", "attempts"), [(0, 0, 1), (1, 0, 2), (2, 0, 3), (3, 42, 3)]
)
@pytest.mark.parametrize(
    "arguments", [["install", "--with-deps", "chromium", "webkit"], ["install-deps", "chromium"]]
)
def test_installation_retries_are_bounded_and_preserve_arguments(
    tmp_path, failures, expected_status, attempts, arguments
):
    npx = tmp_path / "npx"
    npx.write_text(
        "#!/usr/bin/env bash\n"
        'printf "%s\\n" "$*" >> "$CALLS"\n'
        'count=$(wc -l < "$CALLS")\n'
        "if (( count <= FAILURES )); then exit 42; fi\n"
    )
    npx.chmod(0o755)
    sleep = tmp_path / "sleep"
    sleep.write_text('#!/usr/bin/env bash\nprintf "%s\\n" "$*" >> "$SLEEPS"\n')
    sleep.chmod(0o755)
    calls = tmp_path / "calls"
    sleeps = tmp_path / "sleeps"
    result = subprocess.run(  # noqa: S603 — repository script and fixed test arguments
        ["/bin/bash", str(SCRIPT), *arguments],
        env={
            **os.environ,
            "PATH": f"{tmp_path}:{os.environ['PATH']}",
            "CALLS": str(calls),
            "SLEEPS": str(sleeps),
            "FAILURES": str(failures),
            "GITHUB_ACTIONS": "false",
        },
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == expected_status, result.stderr
    assert calls.read_text().splitlines() == ["--no-install playwright " + " ".join(arguments)] * attempts
    assert (sleeps.read_text().splitlines() if sleeps.exists() else []) == ["15", "30"][: attempts - 1]


@pytest.mark.parametrize("arguments", [[], ["test"], ["build"]])
def test_wrapper_cannot_retry_application_tests(arguments):
    result = subprocess.run(  # noqa: S603 — repository script and fixed test arguments
        ["/bin/bash", str(SCRIPT), *arguments], capture_output=True, text=True, timeout=10
    )
    assert result.returncode == 2
    assert "Usage:" in result.stderr


@pytest.mark.parametrize("github_actions", ["true", "false"])
@pytest.mark.parametrize("mirror_exists", [True, False])
def test_official_mirrors_are_configured_only_on_hosted_ci(tmp_path, github_actions, mirror_exists):
    mirror = tmp_path / "apt-mirrors.txt"
    original = "http://azure.archive.ubuntu.com/ubuntu/\n"
    if mirror_exists:
        mirror.write_text(original)
    sudo = tmp_path / "sudo"
    sudo.write_text('#!/bin/bash\nexec "$@"\n')
    sudo.chmod(0o755)
    npx = tmp_path / "npx"
    npx.write_text("#!/bin/bash\nexit 0\n")
    npx.chmod(0o755)
    result = subprocess.run(  # noqa: S603 — repository script and test-owned paths
        ["/bin/bash", str(SCRIPT), "install-deps", "chromium"],
        env={
            **os.environ,
            "PATH": f"{tmp_path}:{os.environ['PATH']}",
            "GITHUB_ACTIONS": github_actions,
            "APT_MIRROR_LIST": str(mirror),
        },
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 0, result.stderr
    if not mirror_exists:
        assert not mirror.exists()
    elif github_actions == "true":
        assert mirror.read_text().splitlines() == [
            "https://archive.ubuntu.com/ubuntu/",
            "https://security.ubuntu.com/ubuntu/",
        ]
    else:
        assert mirror.read_text() == original


def test_workflows_bound_setup_time_and_run_tests_outside_the_retry_wrapper():
    ci = yaml.safe_load((ROOT / ".github/workflows/ci.yml").read_text())
    desktop = yaml.safe_load((ROOT / ".github/workflows/desktop.yml").read_text())
    for workflow, job, name, arguments in [
        (ci, "frontend", "Install test browsers", "install --with-deps chromium webkit"),
        (desktop, "desktop", "Install Linux renderer dependencies", "install-deps chromium"),
    ]:
        steps = workflow["jobs"][job]["steps"]
        install = next(step for step in steps if step.get("name") == name)
        assert install["timeout-minutes"] == 15
        assert install["run"] == f"bash ../../scripts/install_playwright_ci.sh {arguments}"
        assert sum("install_playwright_ci.sh" in step.get("run", "") for step in steps) == 1
    smoke = next(
        step
        for step in desktop["jobs"]["desktop"]["steps"]
        if step.get("name") == "Test packaged Linux renderer"
    )
    assert smoke["run"] == "xvfb-run -a node scripts/smoke-desktop.mjs"
    assert "tests/test_playwright_ci.py" in next(
        step["run"]
        for step in ci["jobs"]["python"]["steps"]
        if step.get("name", "").startswith("Deployment, snapshot")
    )
