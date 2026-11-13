"""Install candidate-pinned native Coolify hooks; GitHub needs no host SSH key."""

from __future__ import annotations

import base64
import gzip
import json
import re
import shlex
import subprocess

MANAGED = ": nautionette-temporal-hook-v1; "


def candidate_files(commit: str) -> dict[str, str]:
    if not re.fullmatch(r"[0-9a-f]{40}", commit):
        raise ValueError("Maintenance needs an exact candidate commit")
    names = subprocess.check_output(  # noqa: S603 - fixed Git operation with validated commit
        ["/usr/bin/git", "ls-tree", "-r", "--name-only", commit, "infra/temporal"], text=True
    ).splitlines()
    paths = {"context/" + name.removeprefix("infra/temporal/"): name for name in names}
    for name in (
        "temporal_deploy_host.py",
        "temporal_deploy_probe.py",
        "temporal_deploy_job.py",
        "temporal_deploy_hook.py",
        "staging_refresh_host.py",
    ):
        paths[name] = "scripts/" + name
    try:
        files = {
            destination: subprocess.check_output(  # noqa: S603 - exact repository source, never a shell
                ["/usr/bin/git", "show", f"{commit}:{source}"], text=True, stderr=subprocess.DEVNULL
            )
            for destination, source in paths.items()
        }
        plan = json.loads(files["context/upgrade-plan.json"])
        if plan["target"] != plan["releases"][-1]["version"]:
            raise ValueError("Invalid candidate Temporal plan")
    except (subprocess.CalledProcessError, KeyError, json.JSONDecodeError):
        raise ValueError(
            "Candidate lacks the automatic Temporal migration contract; refusing deployment"
        ) from None
    return files


def command(files: dict, app: str, target: str, commit: str, shards: str, mode: str) -> str:
    payload = {"app": app, "target": target, "commit": commit, "shards": shards, "mode": mode, "files": files}
    packed = base64.b64encode(gzip.compress(json.dumps(payload).encode(), mtime=0)).decode()
    # One physical line: Coolify normalizes command newlines. No credential is
    # embedded; all runtime DB credentials remain in Docker on the deployment host.
    source = (
        "import base64,gzip,json,sys; "
        f"p=json.loads(gzip.decompress(base64.b64decode({packed!r}))); "
        "n={'__name__':'temporal_hook'}; "
        "exec(compile(p['files']['temporal_deploy_hook.py'],'temporal_hook','exec'),n); "
        "exec(\"try:\\n n['hook'](p)\\nexcept Exception:\\n "
        "print('Temporal hook failed; inspect private controller state.',file=sys.stderr);sys.exit(1)\")"
    )
    result = MANAGED + "python -c " + shlex.quote(source)
    if len(result) > 100_000:
        raise ValueError("Maintenance hook exceeds safe command argument size")
    return result


class TemporalDeployment:
    def __init__(self, api, app: str, target: str, commit: str):
        self.api, self.app, self.target, self.commit = api, app, target, commit
        self.files = candidate_files(commit)

    def prepare(self):
        path = f"/applications/{self.app}"
        details = self.api.call(path)
        for mode in ("pre", "post"):
            existing = details.get(mode + "_deployment_command") or ""
            if existing and not existing.startswith(MANAGED):
                raise RuntimeError(
                    "Existing custom deployment hooks must be composed deliberately; refusing to overwrite"
                )
        values = {
            row.get("key"): row.get("value")
            for row in self.api.call(path + "/envs")
            if not row.get("is_preview", False)
        }
        required = {
            "TEMPORAL_MAINTENANCE_APP": self.app,
            # Coolify passes persisted env keys as CLI --build-arg values; those
            # override even literal Compose build.args. Align with the candidate.
            "TEMPORAL_VERSION": json.loads(self.files["context/upgrade-plan.json"])["target"],
        }
        missing = {key: value for key, value in required.items() if values.get(key) != value}
        if missing:
            self.api.call(
                path + "/envs/bulk",
                "PATCH",
                {
                    "data": [
                        {
                            "key": key,
                            "value": value,
                            "is_buildtime": True,
                            "is_runtime": True,
                            "is_literal": True,
                        }
                        for key, value in missing.items()
                    ]
                },
            )
            verified_env = {
                row.get("key"): row.get("value")
                for row in self.api.call(path + "/envs")
                if not row.get("is_preview", False)
            }
            if any(verified_env.get(key) != value for key, value in required.items()):
                raise RuntimeError("Coolify did not persist the candidate server version/startup fence")
        shards = str(values.get("TEMPORAL_NUM_HISTORY_SHARDS") or "4")
        if not shards.isdecimal() or int(shards) < 1:
            raise ValueError("Invalid Temporal history shard count")
        hooks = {
            "pre_deployment_command": command(
                self.files, self.app, self.target, self.commit, shards, "prepare"
            ),
            "post_deployment_command": command(
                self.files, self.app, self.target, self.commit, shards, "finish"
            ),
            "pre_deployment_command_container": "docker-broker",
            "post_deployment_command_container": "docker-broker",
        }
        self.hooks = hooks
        self.api.call(path, "PATCH", hooks)
        verified = self.api.call(path)
        if any(verified.get(key) != value for key, value in hooks.items()):
            raise RuntimeError("Coolify did not persist the automatic Temporal deployment hooks")
        return shards

    def finish(self):
        # Coolify ignores post-hook errors. Broker health independently requires
        # completed controller state at this exact revision before reaching here.
        actual = self.api.call(f"/applications/{self.app}")
        if any(actual.get(key) != value for key, value in self.hooks.items()):
            raise RuntimeError("Temporal deployment hooks changed during deployment")
