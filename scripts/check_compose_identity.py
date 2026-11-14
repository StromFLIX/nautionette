"""Resolve Compose without starting containers: new defaults vs existing identities."""

import json
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def verify(config: dict, namespace: str, queue: str, bucket: str) -> None:
    assert config["name"] == namespace
    for service in ("backend", "docker-broker", "worker"):
        assert config["services"][service]["environment"]["TEMPORAL_TASK_QUEUE"] == queue
    assert config["services"]["backend"]["environment"]["OUTPUT_S3_BUCKET"] == bucket
    for volume in ("workflows", "projects"):
        assert config["volumes"][volume]["name"] == namespace + "-" + volume
    assert config["networks"]["agents"]["name"] == namespace + "-agent-sandboxes"


def main() -> None:
    docker, standalone = shutil.which("docker"), shutil.which("docker-compose")
    if not docker and not standalone:
        raise RuntimeError("Docker Compose CLI is required; this check does not need a daemon")
    command = [docker, "compose"] if docker else [standalone]
    source = (ROOT / "docker-compose.yaml").read_text()
    configured = set(re.findall(r"\$\{([A-Z][A-Z0-9_]*)", source))
    environment = {key: value for key, value in os.environ.items() if key not in configured}
    legacy = (
        "MAXIO_ACCESS_KEY=nautionette-outputs\nOUTPUT_S3_BUCKET=nautionette-outputs\n"
        "MAXIO_SECRET_KEY=synthetic-compose-identity-check\n"
    )
    cases = (
        (
            "new",
            (ROOT / ".env.example").read_text() + "\nMAXIO_SECRET_KEY=synthetic-compose-identity-check\n",
            "denkzeug",
            "denkzeug",
            "denkzeug-outputs",
        ),
        (
            "explicit-existing",
            legacy + "STACK_NAMESPACE=nautionette\nTEMPORAL_TASK_QUEUE=nautionette\n",
            "nautionette",
            "nautionette",
            "nautionette-outputs",
        ),
        # Old .env files may predate namespace/queue settings. Their defaults
        # MUST stay legacy, otherwise upgrading mounts new empty volumes.
        ("implicit-existing", legacy, "nautionette", "nautionette", "nautionette-outputs"),
    )
    with tempfile.TemporaryDirectory(prefix="denkzeug-compose-") as temporary:
        for name, text, namespace, queue, bucket in cases:
            path = Path(temporary) / (name + ".env")
            path.write_text(text)
            result = subprocess.run(  # noqa: S603 - fixed read-only Compose config operation
                [
                    *command,
                    "--env-file",
                    str(path),
                    "--profile",
                    "images",
                    "--profile",
                    "debug",
                    "--profile",
                    "observability",
                    "config",
                    "--format",
                    "json",
                ],
                cwd=ROOT,
                env=environment,
                check=True,
                capture_output=True,
                text=True,
                timeout=30,
            )
            verify(json.loads(result.stdout), namespace, queue, bucket)
            print(f"PASS {name}: namespace={namespace}, queue={queue}, bucket={bucket}")


if __name__ == "__main__":
    main()
