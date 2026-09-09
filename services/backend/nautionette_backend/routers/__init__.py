"""The HTTP surface, one router per thing the app is about.

`frontend` is last on purpose: its catch-all would otherwise swallow the rest.
"""

from __future__ import annotations

from . import (
    agents,
    chats,
    frontend,
    gateway,
    github_setup,
    internal,
    pi_packages,
    projects,
    runs,
    settings,
    system,
    workflows,
)

ROUTERS = (
    system.router,
    settings.router,
    agents.router,
    pi_packages.router,
    gateway.router,
    chats.router,
    workflows.router,
    runs.router,
    internal.router,
    projects.router,
    github_setup.router,
    frontend.router,
)

__all__ = ["ROUTERS"]
