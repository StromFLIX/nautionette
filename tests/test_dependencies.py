"""Keep duplicated runtime pins and patched image wrappers from drifting apart."""

import json
import re
from pathlib import Path

import pytest
import yaml

from scripts.summarize_dependency_artifacts import summarize

ROOT = Path(__file__).resolve().parents[1]


def test_artifact_summary_keeps_versions_fix_states_and_every_report(tmp_path):
    sbom = tmp_path / "sbom-example"
    sbom.mkdir()
    (sbom / "sbom.json").write_text(
        json.dumps({"packages": [{"name": "unowned-binary", "versionInfo": "1"}]})
    )
    match = {
        "artifact": {"name": "stdlib", "version": "go1.27.3", "type": "go-module"},
        "vulnerability": {
            "id": "GO-2026-6859",
            "severity": "Unknown",
            "namespace": "govulndb:language:go",
            "fix": {"state": "fixed", "versions": ["1.27.4"]},
        },
    }
    for name in ("one", "two"):
        report = tmp_path / f"vulnerabilities-{name}"
        report.mkdir()
        (report / "report.json").write_text(json.dumps({"matches": [match, match]}))
    result = summarize(tmp_path, "https://github.com/example/repo/actions/runs/1")
    assert result["inventories"]["sbom-example"]["package_count"] == 1
    assert len(result["artifacts"]) == 3
    assert all(len(artifact["sha256"]) == 64 for artifact in result["artifacts"])
    assert len(result["findings"]) == 1
    finding = result["findings"][0]
    assert finding["fixed_versions"] == ("1.27.4",)
    assert finding["fix_state"] == "fixed"
    assert finding["severity"] == "Unknown"  # Never hide findings based on severity.
    assert finding["version"] == "go1.27.3"
    assert finding["reports"] == ["vulnerabilities-one/report.json", "vulnerabilities-two/report.json"]


def test_artifact_summary_refuses_empty_or_incomplete_downloads(tmp_path):
    with pytest.raises(ValueError, match="check artifact downloads"):
        summarize(tmp_path, "https://github.com/example/repo/actions/runs/1")


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


def test_gateway_version_follows_the_tested_dockerfile_not_a_persisted_deployment_variable():
    compose = (ROOT / "docker-compose.yaml").read_text()
    gateway = yaml.safe_load(compose)["services"]["agentgateway"]
    assert gateway["build"]["context"] == "./services/agentgateway"
    assert "AGENTGATEWAY_VERSION" not in json.dumps(gateway)
    assert not re.search(r"^AGENTGATEWAY_VERSION\s*=", (ROOT / ".env.example").read_text(), re.MULTILINE)
    dockerfile = (ROOT / "services/agentgateway/Dockerfile").read_text()
    pin = re.search(r"^ARG AGENTGATEWAY_VERSION=(v\d+\.\d+\.\d+)$", dockerfile, re.MULTILINE)[1]
    ci = (ROOT / ".github/workflows/ci.yml").read_text()
    assert f"releases/download/{pin}/agentgateway-linux-amd64" in ci


def test_mcp_schema_search_experiment_has_no_deployment_switch():
    compose = (ROOT / "docker-compose.yaml").read_text()
    example = (ROOT / ".env.example").read_text()
    broker = (ROOT / "services/docker-broker/nautionette_docker_broker/config.py").read_text()
    assert "AGENT_MCP_SCHEMA_MODE" not in compose
    assert not re.search(r"^AGENT_MCP_SCHEMA_MODE\s*=", example, re.MULTILINE)
    assert "NAUTIONETTE_MCP_SCHEMA_MODE" not in broker


def test_browser_and_pi_runtime_guards_match_their_actual_image_pins():
    package = json.loads((ROOT / "services/frontend/package.json").read_text())
    lock = json.loads((ROOT / "services/frontend/package-lock.json").read_text())
    dockerfile = (ROOT / "images/pi-base/Dockerfile").read_text()
    browser = re.search(r"ARG PLAYWRIGHT_VERSION=([\d.]+)", dockerfile)[1]
    assert package["devDependencies"]["@playwright/test"].removeprefix("^") == browser
    assert lock["packages"]["node_modules/playwright"]["version"] == browser
    pi = re.search(r"@earendil-works/pi-coding-agent@([\d.]+)", dockerfile)[1]
    assert f"manifest.version, '{pi}'" in (ROOT / "images/pi-base/verify-pi-runtime.mjs").read_text()


def test_image_smokes_install_workspace_packages_and_dev_test_tools():
    workflow = yaml.safe_load((ROOT / ".github/workflows/ci.yml").read_text())
    smoke = next(
        step
        for step in workflow["jobs"]["images"]["steps"]
        if step.get("name") == "Real patched MaxIO storage compatibility"
    )
    script = smoke["run"]
    assert script.index("uv sync --locked --all-packages") < script.index("uv run --locked pytest")


def test_image_inventory_covers_maintenance_and_recovery_dockerfiles_too():
    workflow = yaml.safe_load((ROOT / ".github/workflows/ci.yml").read_text())
    matrix = workflow["jobs"]["images"]["strategy"]["matrix"]["include"]
    covered = {item["file"] for item in matrix}
    # The controller must inventory the same bundled source as the deployment
    # hook, not a convenient directory containing a different Dockerfile/context.
    controller = next(item for item in matrix if item["name"] == "temporal-controller")
    assert controller["context"] == ".dependency-controller"
    step = next(
        step
        for step in workflow["jobs"]["images"]["steps"]
        if step.get("name") == "Materialize the exact deployment controller build context"
    )
    assert "candidate_files(os.environ['GITHUB_SHA'])" in step["run"]
    covered.remove(controller["file"])
    covered.add("infra/temporal/Dockerfile.controller")
    # These are built, exercised and inventoried in the separate agent-images job.
    covered.update(
        {
            "services/miniapp-sdk/Dockerfile",
            "images/pi-base/Dockerfile",
            "images/agent-sets/default/Dockerfile",
        }
    )
    actual = {
        str(path.relative_to(ROOT)) for path in ROOT.glob("**/Dockerfile*") if ".venv" not in path.parts
    }
    assert covered == actual


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
