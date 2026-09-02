"""The HTTP surface, one router per thing the app is about.

`frontend` is last on purpose: its catch-all would otherwise swallow the rest.
"""

from __future__ import annotations

from . import chats, frontend, gateway, internal, runs, settings, system, workflows

ROUTERS = (
    system.router,
    settings.router,
    gateway.router,
    chats.router,
    workflows.router,
    runs.router,
    internal.router,
    frontend.router,
)

__all__ = ["ROUTERS"]
