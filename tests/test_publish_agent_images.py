from __future__ import annotations

import subprocess

import pytest

from scripts import publish_agent_images as publish

IMAGE = "ghcr.io/stromflix/nautionette/pi-base:abcdef123456"


def fake_docker(monkeypatch, results):
    calls, sleeps = [], []
    responses = iter(results)

    def run(args, **kwargs):
        calls.append(args)
        if args[1] == "tag":
            assert kwargs["check"] is True
            return subprocess.CompletedProcess(args, 0)
        assert args[1] == "push"
        assert kwargs["timeout"] == 600
        response = next(responses)
        if isinstance(response, Exception):
            raise response
        code, output = response
        return subprocess.CompletedProcess(args, code, stdout=output)

    monkeypatch.setattr(publish.subprocess, "run", run)
    monkeypatch.setattr(publish.time, "sleep", sleeps.append)
    return calls, sleeps


def test_success_needs_no_retry(monkeypatch):
    calls, sleeps = fake_docker(monkeypatch, [(0, "digest: sha256:abc\n")])
    publish.push_image(IMAGE)
    assert calls == [["/usr/bin/docker", "push", IMAGE]]
    assert sleeps == []


@pytest.mark.parametrize(
    "output",
    ["unknown blob", "blob unknown", "BLOB_UNKNOWN", "503 Service Unavailable", "429 Too Many Requests"],
)
def test_transient_push_retries_the_exact_tested_image(monkeypatch, output):
    calls, sleeps = fake_docker(monkeypatch, [(1, output), (0, "Pushed\n")])
    publish.push_image(IMAGE)
    assert calls == [["/usr/bin/docker", "push", IMAGE]] * 2
    assert sleeps == [10]


def test_exhausted_retries_still_fail_ci(monkeypatch):
    calls, sleeps = fake_docker(monkeypatch, [(1, "unknown blob")] * 3)
    with pytest.raises(RuntimeError, match="deployment remains blocked"):
        publish.push_image(IMAGE)
    assert len(calls) == 3
    assert sleeps == [10, 30]


@pytest.mark.parametrize(
    "output",
    [
        "denied: permission denied",
        "unauthorized: authentication required",
        "forbidden: 503",
        "invalid reference",
    ],
)
def test_permanent_failures_are_not_retried(monkeypatch, output):
    calls, sleeps = fake_docker(monkeypatch, [(1, output)])
    with pytest.raises(RuntimeError, match="deployment remains blocked"):
        publish.push_image(IMAGE)
    assert len(calls) == 1
    assert sleeps == []


def test_push_timeout_can_be_retried_safely(monkeypatch):
    calls, sleeps = fake_docker(monkeypatch, [subprocess.TimeoutExpired("docker", 600), (0, "Pushed\n")])
    publish.push_image(IMAGE)
    assert len(calls) == 2
    assert sleeps == [10]


def configure(monkeypatch):
    monkeypatch.setenv("REPOSITORY", "StromFLIX/nautionette")
    monkeypatch.setenv("BASE_HASH", "abcdef123456")
    monkeypatch.setenv("DEFAULT_HASH", "123456abcdef")


def test_both_images_must_be_published_from_local_tested_tags(monkeypatch):
    configure(monkeypatch)
    calls, sleeps = fake_docker(monkeypatch, [(0, "Pushed\n"), (0, "Pushed\n")])
    publish.main()
    default = "ghcr.io/stromflix/nautionette/pi-agent-default:123456abcdef"
    assert calls == [
        ["/usr/bin/docker", "tag", "nautionette/pi-base:dev", IMAGE],
        ["/usr/bin/docker", "push", IMAGE],
        ["/usr/bin/docker", "tag", "nautionette/pi-agent-default:dev", default],
        ["/usr/bin/docker", "push", default],
    ]
    assert sleeps == []


def test_second_image_failure_is_not_reported_as_success(monkeypatch):
    configure(monkeypatch)
    calls, _ = fake_docker(
        monkeypatch, [(0, "Pushed\n"), (1, "unknown blob"), (1, "unknown blob"), (1, "unknown blob")]
    )
    with pytest.raises(RuntimeError, match="pi-agent-default"):
        publish.main()
    assert len(calls) == 6


@pytest.mark.parametrize(
    ("name", "value"),
    [("REPOSITORY", "bad/repo:tag"), ("BASE_HASH", ""), ("DEFAULT_HASH", "latest")],
)
def test_invalid_fingerprints_cannot_publish_mutable_tags(monkeypatch, name, value):
    configure(monkeypatch)
    monkeypatch.setenv(name, value)
    calls, _ = fake_docker(monkeypatch, [])
    with pytest.raises(ValueError, match="exact image source fingerprints"):
        publish.main()
    assert calls == []
