"""The Docker connection.

Every caller goes through `daemon.client()` rather than holding a reference, so
there is one place the socket is opened and one place to stand in for it.
"""

from __future__ import annotations

import logging

import docker

logging.basicConfig(level=logging.INFO, format="%(asctime)s broker %(levelname)s %(message)s")
log = logging.getLogger("broker")

_client: docker.DockerClient | None = None


def client() -> docker.DockerClient:
    global _client
    if _client is None:
        _client = docker.from_env()
    return _client
