"""Prepare real deployment artifacts, then emit Docker mount arguments for offline CI.

Run on the Docker host with `uv run python scripts/prepare_artcraft_test.py`.
The image must already exist. The broker fetches pinned archives; extraction and
native probes run only in isolated, network-none installer containers.
"""

from nautionette.artcraft import bundled_selection, catalog
from nautionette_docker_broker import artcraft

image = "nautionette/pi-agent-default:dev"
artcraft.ensure_bundled(image=image)
# Simulate broker restart: reuse must verify persisted manifests/CLIs OFFLINE,
# not download the releases again or trust volume labels without a manifest.
artcraft._verified.clear()
artcraft.ensure_bundled(image=image)
assert artcraft.bundled_ready()
for mount in artcraft.mounts([bundled_selection(app) for app in catalog()]):
    print(f"--mount=type=volume,source={mount['Source']},target={mount['Target']},readonly")
