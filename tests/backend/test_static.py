"""Artifacts, seeding and the single door in front of the SPA."""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest
from nautionette_backend import main
from nautionette_backend.config import settings


@pytest.fixture
def artifact() -> Path:
    path = Path(settings.artifacts_dir) / "digest.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("# Digest\n", encoding="utf-8")
    yield path
    path.unlink(missing_ok=True)


def test_an_artifact_is_served_as_text(client, artifact):
    response = client.get("/api/artifacts/digest.md")
    assert response.status_code == 200
    assert response.text == "# Digest\n"


def test_an_artifact_name_can_never_leave_its_directory(client, artifact):
    outside = Path(settings.artifacts_dir).parent / "secret.txt"
    outside.write_text("private", encoding="utf-8")
    try:
        assert client.get("/api/artifacts/..%2Fsecret.txt").status_code == 404
        assert client.get("/api/artifacts/..%2F..%2Fetc%2Fpasswd").status_code == 404
        assert client.get("/api/artifacts/%2Fetc%2Fpasswd").status_code == 404
    finally:
        outside.unlink()


def test_an_artifact_that_was_never_written_is_a_404(client):
    assert client.get("/api/artifacts/nothing.md").status_code == 404


def test_committed_workflows_are_seeded_once(tmp_path, monkeypatch):
    source, target = tmp_path / "seed", tmp_path / "workflows"
    source.mkdir()
    (source / "hello_world.py").write_text("original\n")
    monkeypatch.setattr(settings, "seed_dir", str(source))
    monkeypatch.setattr(settings, "workflows_dir", str(target))

    main.seed_workflows()
    assert (target / "hello_world.py").read_text() == "original\n"
    assert (target / ".drafts").is_dir()

    # A workflow edited in place is never overwritten by the image's copy.
    (target / "hello_world.py").write_text("edited\n")
    main.seed_workflows()
    assert (target / "hello_world.py").read_text() == "edited\n"


def test_anything_else_is_handed_to_the_frontend(client, monkeypatch):
    async def serve(self, method, url, **kwargs):
        assert url == "http://frontend.test:80/assets/app.js"
        return httpx.Response(
            200, text="console.log(1)", headers={"content-type": "application/javascript"}
        )

    monkeypatch.setattr(httpx.AsyncClient, "request", serve)
    response = client.get("/assets/app.js")
    assert response.status_code == 200
    assert response.text == "console.log(1)"


def test_a_frontend_that_is_not_up_says_so_rather_than_looking_broken(client, monkeypatch):
    async def refuse(self, method, url, **kwargs):
        raise httpx.ConnectError("connection refused")

    monkeypatch.setattr(httpx.AsyncClient, "request", refuse)
    response = client.get("/")
    assert response.status_code == 502
    assert "frontend unavailable" in response.text
