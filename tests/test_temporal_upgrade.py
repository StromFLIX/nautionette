"""Offline safety guards; the real migration/restore rehearsal runs separately in CI."""

# Fixed shell command under test with fixture-controlled inputs.
# ruff: noqa: S603, S607

import json
import os
import subprocess
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
PLAN = {
    "1.29.7": ("1.29.0", "1.18", "1.9", "1.18", "1.9"),
    "1.30.9": ("1.29.7", "1.18", "1.9", "1.18", "1.13"),
    "1.31.5": ("1.30.9", "1.18", "1.13", "1.19", "1.14"),
    "1.32.2": ("1.31.5", "1.19", "1.14", "1.19", "1.14"),
}


@pytest.fixture
def schema_tools(tmp_path):
    state = tmp_path / "state.json"
    calls = tmp_path / "calls.jsonl"
    binary = tmp_path / "tool"
    binary.write_text(
        "#!/usr/bin/env python3\n"
        "import json, os, pathlib, sys\n"
        "state = pathlib.Path(os.environ['TEST_STATE'])\n"
        "data = json.loads(state.read_text())\n"
        "args = sys.argv[1:]\n"
        "if pathlib.Path(sys.argv[0]).name == 'psql':\n"
        "    if os.environ.get('TEST_PSQL_FAIL'): sys.exit(1)\n"
        "    db = args[args.index('--dbname') + 1]\n"
        "    print(data[db])\n"
        "else:\n"
        "    assert os.environ['SQL_PASSWORD'] == 'test-only'\n"
        "    assert 'test-only' not in args\n"
        "    with open(os.environ['TEST_CALLS'], 'a') as f: f.write(json.dumps(args) + '\\n')\n"
        "    db = args[args.index('--db') + 1]\n"
        "    data[db] = os.environ['TEST_CORE_AFTER' if db == 'temporal' else 'TEST_VIS_AFTER']\n"
        "    state.write_text(json.dumps(data))\n"
    )
    binary.chmod(0o755)
    for name in ("psql", "temporal-sql-tool"):
        (tmp_path / name).symlink_to(binary)

    def run(version, command, core=None, visibility=None, **extra):
        _, before_core, before_vis, after_core, after_vis = PLAN.get(version, PLAN["1.32.2"])
        state.write_text(
            json.dumps({"temporal": core or before_core, "temporal_visibility": visibility or before_vis})
        )
        env = {
            **os.environ,
            "PATH": str(tmp_path) + os.pathsep + os.environ["PATH"],
            "POSTGRES_USER": "temporal",
            "POSTGRES_PWD": "test-only",
            "POSTGRES_SEEDS": "postgres",
            "TEMPORAL_TOOLS_VERSION": version,
            "TEST_STATE": str(state),
            "TEST_CALLS": str(calls),
            "TEST_CORE_AFTER": after_core,
            "TEST_VIS_AFTER": after_vis,
            **extra,
        }
        result = subprocess.run(
            ["sh", str(ROOT / "infra/temporal/migrate.sh"), command], env=env, capture_output=True, text=True
        )
        commands = [json.loads(line) for line in calls.read_text().splitlines()] if calls.exists() else []
        return result, commands

    return run


@pytest.mark.parametrize("version", PLAN)
def test_only_reviewed_consecutive_schema_updates_are_allowed(schema_tools, version):
    previous, *_ = PLAN[version]
    result, calls = schema_tools(version, "upgrade-from-" + previous)
    assert result.returncode == 0, result.stderr
    assert len(calls) == 2
    assert all("update-schema" in args for args in calls)
    assert all("create-database" not in args and "setup-schema" not in args for args in calls)
    assert "/etc/temporal/schema/postgresql/v12/temporal/versioned" in calls[0]
    assert "/etc/temporal/schema/postgresql/v12/visibility/versioned" in calls[1]


@pytest.mark.parametrize(
    "version,command,extra",
    [
        ("1.32.2", "upgrade-from-1.29.7", {}),
        ("1.31.5", "upgrade-from-1.30.9", {"visibility": "1.9"}),
        ("1.30.9", "upgrade-from-1.29.7", {"core": "1.19"}),
        ("1.30.9", "upgrade-from-1.29.7", {"TEST_PSQL_FAIL": "1"}),
        ("1.33.0", "upgrade-from-1.32.2", {}),
        ("1.32.2", "initialize-empty", {}),
    ],
)
def test_unsafe_jump_partial_state_outage_or_reinitialization_never_mutates(
    schema_tools, version, command, extra
):
    result, calls = schema_tools(version, command, **extra)
    assert result.returncode != 0
    assert not calls
    assert "test-only" not in result.stderr


def test_maintained_images_have_matching_pins_and_preserve_network_volume_contracts():
    base = yaml.safe_load((ROOT / "docker-compose.yaml").read_text())["services"]
    # Maintained default is pinned by source, not a stale Coolify env default.
    assert base["temporal"]["build"]["context"] == "./infra/temporal"
    assert base["temporal"]["build"]["args"]["TEMPORAL_VERSION"] == "1.32.2"
    assert "postgres-data:/var/lib/postgresql/data" in base["postgres"]["volumes"]
    assert base["temporal"]["networks"] == ["internal", "data"]
    assert "ports" not in base["temporal"]
    assert base["temporal"]["stop_grace_period"] == "60s"
    schema = base["temporal-schema"]
    assert schema["build"]["args"]["TEMPORAL_VERSION"] == "1.32.2"
    assert schema["profiles"] == ["maintenance"]
    assert schema["networks"] == ["data"]
    assert schema["read_only"] is True
    assert "temporal-schema" not in base["temporal"]["depends_on"]
    server = (ROOT / "infra/temporal/Dockerfile").read_text()
    assert "FROM temporalio/server:${TEMPORAL_VERSION}" in server
    assert "COPY --from=tools /usr/local/bin/temporal" in server
    assert "USER temporal" in server
    assert "auto-setup" not in server


def test_ci_rehearses_real_postgres_upgrades_and_restore_without_touching_live_stacks():
    ci = yaml.safe_load((ROOT / ".github/workflows/ci.yml").read_text())
    job = ci["jobs"]["temporal-upgrade"]
    assert any("scripts.check_temporal_upgrade" in step.get("run", "") for step in job["steps"])
    assert "environment" not in job
    source = (ROOT / "scripts/check_temporal_upgrade.py").read_text()
    for version in ("1.29.7", *PLAN):
        assert version in source
    for proof in (
        "replay_workflow",
        "pg_dump",
        "pg_restore",
        "schedule.trigger",
        "schedule.unpause",
        "initialize-empty",
    ):
        assert proof in source
    assert "coolify" not in source.lower()
