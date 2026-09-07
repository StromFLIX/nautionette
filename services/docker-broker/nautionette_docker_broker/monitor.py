"""Keep fixed execution resources present even after external Docker cleanup."""

from __future__ import annotations

import threading

from . import daemon, images, workers
from .config import RECONCILE_SECONDS


def run(stopping: threading.Event) -> None:
    while not stopping.is_set():
        for reconcile in (images.reconcile, workers.reconcile):
            try:
                reconcile()
            except Exception:
                daemon.log.exception("container reconciliation failed")
        if stopping.wait(RECONCILE_SECONDS):
            return