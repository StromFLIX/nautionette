"""Canonical registry identities survive deployment-time Dockerfile rewriting."""

import json
from pathlib import Path

import pytest
from nautionette_docker_broker import image_context, images


def test_bundled_hashes_ignore_injected_build_arguments(tmp_path, monkeypatch):
    root = tmp_path / "images"
    sdk = tmp_path / "sdk"
    for folder in [root / "pi-base", root / "agent-sets/default", sdk]:
        folder.mkdir(parents=True)
        (folder / "Dockerfile").write_text("FROM scratch\n")
    canonical = image_context.source_fingerprints(root, sdk)
    lock = tmp_path / "fingerprints.json"
    lock.write_text(json.dumps(canonical))
    monkeypatch.setattr(image_context, "BUNDLED_LOCK", lock)
    monkeypatch.setattr(images, "AGENT_IMAGES_DIR", str(root))
    for file in [root / "pi-base/Dockerfile", root / "agent-sets/default/Dockerfile", sdk / "Dockerfile"]:
        file.write_text(file.read_text() + "ARG COOLIFY_RESOURCE_UUID\nARG APP_TOKEN\n")
    assert image_context.source_fingerprints(root, sdk) != canonical
    assert images.base_hash() == canonical["base"]
    assert images._agent_set_hash("default") == canonical["agent_sets"]["default"]
    assert image_context.bundled_hash("miniapp") == canonical["miniapp"]


def test_python_import_caches_do_not_change_source_identities(tmp_path):
    root = tmp_path / "context"
    root.mkdir()
    (root / "module.py").write_text("print('source')\n")
    original = image_context.context_hash(root)
    (root / "__pycache__").mkdir()
    (root / "__pycache__/module.cpython-313.pyc").write_bytes(b"generated import cache")
    (root / "module.pyc").write_bytes(b"legacy generated import cache")
    (root / "module.pyo").write_bytes(b"optimized generated import cache")
    assert image_context.context_hash(root) == original


def test_missing_lock_retains_local_source_hashing(tmp_path, monkeypatch):
    monkeypatch.setattr(image_context, "BUNDLED_LOCK", tmp_path / "absent.json")
    assert image_context.bundled_hash("miniapp") is None


@pytest.mark.parametrize(
    "value",
    [
        None,
        [],
        {},
        {"schema": 1},
        {
            "schema": 1,
            "base": "not-a-hash",
            "agent_sets": {},
            "miniapp": "123456789abc",
        },
        {"schema": 1, "base": "123456789abc", "agent_sets": [], "miniapp": "123456789abc"},
    ],
)
def test_invalid_packaged_lock_fails_closed(tmp_path, monkeypatch, value):
    lock = tmp_path / "fingerprints.json"
    lock.write_text(json.dumps(value))
    monkeypatch.setattr(image_context, "BUNDLED_LOCK", lock)
    with pytest.raises(ValueError, match="Invalid bundled"):
        image_context.bundled_hash("miniapp")


def test_checked_in_lock_matches_all_canonical_contexts():
    root = Path(__file__).resolve().parents[2]
    assert json.loads((root / image_context.SOURCE_LOCK).read_text()) == image_context.source_fingerprints(
        root / "images", root / "services/miniapp-sdk"
    )
