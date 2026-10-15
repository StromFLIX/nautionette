"""Single-process entrypoint with chat draining *before* HTTP/MCP shutdown.

A lifespan shutdown hook alone is too late: Uvicorn has already closed the
listener, preventing active agents from using their backend MCP tools.
"""

from __future__ import annotations

import asyncio
import logging
import socket
from types import FrameType

import uvicorn

from . import background
from .config import settings
from .conversations import resume_chat_queues

log = logging.getLogger("nautionette")


class DrainingServer(uvicorn.Server):
    def __init__(self, config: uvicorn.Config) -> None:
        super().__init__(config)
        self._expedite = asyncio.Event()

    async def startup(self, sockets: list[socket.socket] | None = None) -> None:
        await super().startup(sockets=sockets)
        # Recovery has classified uncertain turns in lifespan startup. Start
        # untouched queues only once backend tools can reach the HTTP listener.
        resume_chat_queues()

    def handle_exit(self, sig: int, frame: FrameType | None) -> None:
        # Fence admission immediately, including requests awaiting model discovery.
        background.begin_draining()
        if self.should_exit:
            # A second signal skips the remaining drain window, but not cleanup.
            # Uvicorn's force_exit would skip the application's lifespan teardown.
            self._expedite.set()
            return
        super().handle_exit(sig, frame)

    async def shutdown(self, sockets: list[socket.socket] | None = None) -> None:
        background.begin_draining()
        log.info("Draining active chat turns for up to %ss", settings.backend_chat_drain_seconds)
        turns = asyncio.create_task(background.wait_for_chat_turns())
        expedite = asyncio.create_task(self._expedite.wait())
        try:
            done, _ = await asyncio.wait(
                {turns, expedite},
                timeout=settings.backend_chat_drain_seconds,
                return_when=asyncio.FIRST_COMPLETED,
            )
            if turns not in done:
                log.warning("Chat drain deadline or second signal reached; stopping remaining work safely")
                await background.drain()
        finally:
            # Cancelling these waiters must never cancel the turns themselves.
            turns.cancel()
            expedite.cancel()
            await asyncio.gather(turns, expedite, return_exceptions=True)
        await super().shutdown(sockets=sockets)


def main() -> None:
    DrainingServer(
        uvicorn.Config(
            "nautionette_backend.main:app",
            host="0.0.0.0",  # noqa: S104 - container entrypoint; host binding is set by Compose.
            port=8080,
            workers=1,
            proxy_headers=True,
            # Chat/sidebar SSE subscriptions can remain open indefinitely.
            # They must not hold shutdown open after the chat drain has finished.
            timeout_graceful_shutdown=5,
        )
    ).run()


if __name__ == "__main__":
    main()
