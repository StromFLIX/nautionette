import hashlib
import json
from unittest.mock import Mock

import pytest
from nautionette.artcraft import bundled_release
from nautionette_docker_broker import artcraft_offline as offline


@pytest.fixture
def staged(tmp_path):
    target = tmp_path / "artifact"
    target.mkdir()
    (target / ".install.py").write_text("fixed helper")
    (target / ".release.tar.gz").write_bytes(b"archive")
    spec = {**bundled_release("cadcraft"), "sha256": hashlib.sha256(b"archive").hexdigest()}
    installer = Mock()
    installer.validate.side_effect = lambda value: value

    def extract(archive, dest, app):
        assert archive.read_bytes() == b"archive"
        assert not list(dest.iterdir())
        (dest / "bin").mkdir()
        (dest / "bin/cadcraft-cli").write_bytes(b"native CLI")

    installer.extract.side_effect = extract
    return spec, target, installer


def test_offline_helper_rechecks_bytes_removes_staging_and_publishes_only_after_probe(staged):
    spec, target, installer = staged
    assert offline.install_offline(spec, target, installer) == spec
    assert json.loads((target / "manifest.json").read_text()) == spec
    assert not list(target.glob(".*"))
    installer.validate.assert_called_once_with(spec)
    installer.probe.assert_called_once_with(target, "cadcraft")


@pytest.mark.parametrize("failure", ["checksum", "size", "symlink", "probe", "nonempty"])
def test_offline_helper_never_publishes_invalid_or_incompatible_artifacts(staged, failure, monkeypatch):
    spec, target, installer = staged
    if failure == "checksum":
        spec["sha256"] = "0" * 64
    elif failure == "size":
        monkeypatch.setattr(offline, "MAX_ARCHIVE", 2)
    elif failure == "symlink":
        (target / ".release.tar.gz").unlink()
        (target / ".release.tar.gz").symlink_to(target / ".install.py")
    elif failure == "nonempty":
        (target / "unexpected").write_bytes(b"not an empty artifact")
    else:
        installer.probe.side_effect = ValueError("unsupported native CLI")
    with pytest.raises(ValueError):
        offline.install_offline(spec, target, installer)
    assert not (target / "manifest.json").exists()
    if failure != "probe":
        installer.extract.assert_not_called()
