"""Opt-in smoke using a rebuilt agent image and an explicitly chosen public package."""

import json
import os
import uuid
from contextlib import ExitStack

import docker
import pytest
from nautionette_docker_broker import packages

pytestmark = pytest.mark.skipif(
    os.environ.get("NAUTIONETTE_DOCKER_TESTS") != "1"
    or not os.environ.get("NAUTIONETTE_PACKAGE_TEST_SOURCE"),
    reason="Needs Docker, a rebuilt agent image, and NAUTIONETTE_PACKAGE_TEST_SOURCE (public npm/GitHub)",
)


def test_real_install_is_immutable_and_runtime_mount_is_read_only(monkeypatch):
    client = docker.from_env()
    installation_id = uuid.uuid4().hex
    with ExitStack() as cleanup:
        cleanup.callback(client.close)
        monkeypatch.setattr(packages.daemon, "client", lambda: client)
        monkeypatch.setattr(packages, "WORKFLOWS_VOLUME", "package-smoke-" + uuid.uuid4().hex)
        metadata = packages.install(installation_id, os.environ["NAUTIONETTE_PACKAGE_TEST_SOURCE"])
        volume = client.volumes.get(packages.volume_name(installation_id))
        cleanup.callback(volume.remove)
        assert metadata["root"] and metadata["resolved"]
        assert not client.containers.list(
            all=True, filters={"label": f"nautionette.package={installation_id}"}
        )
        assert not client.networks.list(names=[volume.name])
        with pytest.raises(ValueError, match="never overwritten"):
            packages.install(installation_id, os.environ["NAUTIONETTE_PACKAGE_TEST_SOURCE"])
        root = f"/opt/nautionette-packages/{installation_id}"
        script = (
            'const fs = require("node:fs"); '
            f"if (!fs.statSync({json.dumps(root + '/' + metadata['root'])}).isDirectory()) process.exit(2); "
            f"try {{ fs.writeFileSync({json.dumps(root + '/mutation')}, 'bad'); process.exit(3); }} "
            'catch (error) { if (error.code !== "EROFS") throw error; }'
        )
        output = client.containers.run(
            packages.images.image_tag("default"),
            entrypoint=["node", "-e", script],
            mounts=packages.mounts([installation_id]),
            network_mode="none",
            user="1000:1000",
            read_only=True,
            cap_drop=["ALL"],
            security_opt=["no-new-privileges:true"],
            mem_limit="256m",
            pids_limit=64,
            remove=True,
        )
        assert output == b""
