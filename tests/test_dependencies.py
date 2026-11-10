"""Keep duplicated runtime pins and patched image wrappers from drifting apart."""

import json
import re
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]


def test_python_services_copy_uv_into_the_same_patch_pinned_python_runtime():
    dockerfiles = [
        ROOT / "services" / name / "Dockerfile"
        for name in ("backend", "worker", "workflow-mcp", "docker-broker", "stt")
    ]
    bases = {path.read_text().splitlines()[0] for path in dockerfiles}
    assert len(bases) == 1
    assert re.fullmatch(r"FROM python:3\.13\.\d+-slim-bookworm", bases.pop())
    uv_pins = set()
    for path in [
        *dockerfiles[:-1],
        ROOT / "images/pi-base/Dockerfile",
        ROOT / "services/agentgateway/Dockerfile",
    ]:
        match = re.search(r"COPY --from=ghcr.io/astral-sh/uv:([\d.]+) /uv /uvx", path.read_text())
        assert match, path
        uv_pins.add(match[1])
    assert len(uv_pins) == 1


def test_browser_and_pi_runtime_guards_match_their_actual_image_pins():
    package = json.loads((ROOT / "services/frontend/package.json").read_text())
    lock = json.loads((ROOT / "services/frontend/package-lock.json").read_text())
    dockerfile = (ROOT / "images/pi-base/Dockerfile").read_text()
    browser = re.search(r"ARG PLAYWRIGHT_VERSION=([\d.]+)", dockerfile)[1]
    assert package["devDependencies"]["@playwright/test"].removeprefix("^") == browser
    assert lock["packages"]["node_modules/playwright"]["version"] == browser
    pi = re.search(r"@earendil-works/pi-coding-agent@([\d.]+)", dockerfile)[1]
    assert f"manifest.version, '{pi}'" in (ROOT / "images/pi-base/verify-pi-runtime.mjs").read_text()


def test_patched_storage_images_keep_the_existing_volume_and_non_root_contracts():
    services = yaml.safe_load((ROOT / "docker-compose.yaml").read_text())["services"]
    postgres = services["postgres"]
    assert postgres["build"]["context"] == "./infra/postgres"
    assert "postgres-data:/var/lib/postgresql/data" in postgres["volumes"]
    dockerfile = (ROOT / "infra/postgres/Dockerfile").read_text()
    assert "FROM postgres:17-alpine" in dockerfile
    assert "COPY --from=gosu /out/gosu /usr/local/bin/gosu" in dockerfile
    assert "USER" not in dockerfile  # Upstream entrypoint must still start as root and drop privileges.
    for name, user in (("temporal", "temporal"), ("maxio", "maxio:maxio")):
        dockerfile = (ROOT / "infra" / name / "Dockerfile").read_text()
        assert "USER root" in dockerfile
        assert f"USER {user}" in dockerfile
