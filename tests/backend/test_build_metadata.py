"""Display metadata is optional and never replaces the deployed revision."""

import pytest
from nautionette_backend.config import Settings, settings

COMMIT = "68c4590f45388cc3d9270c459a5cd30985aba4e4"


@pytest.mark.parametrize("tag", ["", "v1.2.3", "v01.2.3", "v1.2.3-rc.1", "main", "x" * 1000])
def test_release_metadata_does_not_change_raw_revision(monkeypatch, tag):
    monkeypatch.setenv("APP_VERSION", COMMIT)
    monkeypatch.setenv("APP_RELEASE_TAG", tag)
    configured = Settings()
    assert configured.version == COMMIT
    assert configured.build_commit == COMMIT
    assert configured.release_tag == (tag if tag == "v1.2.3" else None)


@pytest.mark.parametrize("version", ["dev", "1.2.3", "v1.2.3", "not-a-commit", "a" * 41])
def test_legacy_versions_are_preserved_without_fabricated_commit(monkeypatch, version):
    monkeypatch.setenv("APP_VERSION", version)
    monkeypatch.delenv("APP_RELEASE_TAG", raising=False)
    configured = Settings()
    assert configured.version == version
    assert configured.build_commit is None
    assert configured.release_tag is None


@pytest.mark.parametrize("path", ["/api/system", "/healthz"])
def test_system_metadata_keeps_the_full_source_revision(client, monkeypatch, path):
    monkeypatch.setattr(settings, "version", COMMIT)
    monkeypatch.setattr(settings, "build_commit", COMMIT)
    monkeypatch.setattr(settings, "release_tag", "v1.2.3")
    state = client.get(path).json()
    assert state["version"] == COMMIT
    assert state["build_commit"] == COMMIT
    assert state["release_tag"] == "v1.2.3"
