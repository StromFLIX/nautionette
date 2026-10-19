"""The HTTP surface, one router per thing the app is about.

`frontend` is last on purpose: its catch-all would otherwise swallow the rest.
"""

from __future__ import annotations

from . import (
    agents,
    artcraft,
    chat_inspection,
    chat_outputs,
    chats,
    frontend,
    gateway,
    github_setup,
    internal,
    pi_packages,
    projects,
    runs,
    settings,
    storage,
    system,
    workflows,
)

ROUTERS = (
    system.router,
    settings.router,
    storage.router,
    agents.router,
    pi_packages.router,
    artcraft.router,
    gateway.router,
    chats.router,
    chat_outputs.router,
    chat_inspection.router,
    workflows.router,
    runs.router,
    internal.router,
    projects.router,
    github_setup.router,
    frontend.router,
)

__all__ = ["ROUTERS"]
