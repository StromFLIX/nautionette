"""Private, ephemeral streaming dictation. No cloud providers or transcript storage."""

from __future__ import annotations

import asyncio
import json
import secrets
import time
from contextlib import suppress

from fastapi import APIRouter, Depends, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from websockets.asyncio.client import connect
from websockets.exceptions import WebSocketException

from ..security import require_user

router = APIRouter()
# Deliberately private and fixed: dictation cannot be redirected to a cloud API.
STT_SOCKET = "ws://stt:8000/v1/audio/stream"
MAX_BYTES = 12 * 1024 * 1024
MAX_FRAME = 65540
LANGUAGES = ["en", "de"]
_tickets: dict[str, tuple[float, str, str]] = {}
_slot = asyncio.Semaphore(1)
_handshakes = asyncio.Semaphore(16)


@router.get("/api/speech", dependencies=[Depends(require_user)], operation_id="speech_capabilities")
async def capabilities() -> dict:
    return {"languages": LANGUAGES, "max_seconds": 120, "max_bytes": MAX_BYTES}


class TicketRequest(BaseModel):
    language: str = Field(max_length=2)


@router.post(
    "/api/speech/stream-ticket", dependencies=[Depends(require_user)], operation_id="speech_stream_ticket"
)
async def stream_ticket(request: Request, body: TicketRequest):
    if body.language not in LANGUAGES:
        raise HTTPException(422, "Choose English or German for local dictation.")
    now = time.monotonic()
    for ticket, (expires, _, _) in list(_tickets.items()):
        if expires <= now:
            del _tickets[ticket]
    if len(_tickets) >= 128:
        raise HTTPException(429, "Too many dictation requests. Please try again shortly.")
    ticket = secrets.token_urlsafe(32)
    _tickets[ticket] = (now + 30, request.headers.get("origin", ""), body.language)
    # Ticket is bearer-equivalent but single-use and short-lived. Never place it in a URL.
    return JSONResponse({"ticket": ticket}, headers={"Cache-Control": "no-store"})


async def relay(client: WebSocket, upstream):
    """Bound both directions, finish only on the recognizer's final acknowledgement."""

    async def audio():
        size = sequence = 0
        rate = None
        ended = False
        while True:
            message = await asyncio.wait_for(client.receive(), timeout=30)
            if message["type"] == "websocket.disconnect":
                return
            data = message.get("bytes")
            if data is not None:
                if ended or rate is None or not 6 <= len(data) <= MAX_FRAME or len(data) % 2:
                    raise ValueError("Invalid audio frame")
                if int.from_bytes(data[:4], "little") != sequence or sequence >= 4096:
                    raise ValueError("Invalid audio sequence")
                sequence += 1
                size += len(data) - 4
                if size > MAX_BYTES or size > rate * 2 * 120:
                    raise ValueError("Recording exceeds limits")
                await upstream.send(data)
            else:
                text = message.get("text", "")
                if len(text) > 256:
                    raise ValueError("Invalid control frame")
                event = json.loads(text)
                if not isinstance(event, dict):
                    raise ValueError("Invalid control frame")
                if event.get("type") == "format" and rate is None and not ended:
                    rate = event.get("sample_rate")
                    if type(rate) is not int or not 8000 <= rate <= 48000:
                        raise ValueError("Invalid sample rate")
                elif (
                    event.get("type") == "end"
                    and not ended
                    and type(event.get("sequence")) is int
                    and event["sequence"] == sequence
                ):
                    ended = True
                else:
                    raise ValueError("Invalid control frame")
                await upstream.send(text)

    async def transcripts():
        revision = -1
        while True:
            raw = await upstream.recv()
            if not isinstance(raw, str) or len(raw) > 20000:
                raise ValueError("Invalid transcript")
            event = json.loads(raw)
            if not isinstance(event, dict) or event.get("type") not in {"transcript", "done"}:
                raise ValueError("Invalid transcript")
            if (
                not isinstance(event.get("text"), str)
                or len(event["text"]) > 16000
                or type(event.get("revision")) is not int
                or event["revision"] <= revision
            ):
                raise ValueError("Invalid transcript")
            revision = event["revision"]
            await client.send_json(event)
            if event["type"] == "done":
                return

    tasks = [asyncio.create_task(audio()), asyncio.create_task(transcripts())]
    try:
        done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        for task in done:
            task.result()
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


@router.websocket("/api/speech/stream")
async def stream(client: WebSocket):
    # Unauthenticated sockets have their own bounded budget, never the inference slot.
    if _handshakes.locked():
        await client.close(code=1013)
        return
    try:
        async with _handshakes:
            await client.accept()
            first = await asyncio.wait_for(client.receive(), timeout=5)
            if first["type"] == "websocket.disconnect":
                return
            raw = first.get("text")
            if not isinstance(raw, str) or len(raw) > 256:
                await client.close(code=1008)
                return
            auth = json.loads(raw)
            ticket = auth.get("ticket") if isinstance(auth, dict) else None
            saved = _tickets.pop(ticket, None) if isinstance(ticket, str) else None
            if (
                not saved
                or saved[0] <= time.monotonic()
                or saved[1] != client.headers.get("origin", "")
                or auth.get("type") != "auth"
            ):
                await client.close(code=1008)
                return
        if _slot.locked():
            await client.close(code=1013)
            return
        async with _slot:
            async with connect(
                STT_SOCKET,
                open_timeout=5,
                close_timeout=1,
                max_size=20000,
                max_queue=4,
                write_limit=65536,
                compression=None,
                proxy=None,
            ) as upstream:
                await upstream.send(json.dumps({"type": "start", "language": saved[2]}))
                ready = json.loads(await asyncio.wait_for(upstream.recv(), timeout=10))
                if ready != {"type": "ready"}:
                    raise ValueError("Speech service not ready")
                await client.send_json(ready)
                async with asyncio.timeout(180):
                    await relay(client, upstream)
    except WebSocketDisconnect:
        pass
    except (OSError, TimeoutError, WebSocketException, ValueError, TypeError, RuntimeError):
        # Never surface upstream messages, audio, transcripts, URLs or credentials.
        with suppress(RuntimeError, WebSocketDisconnect):
            await client.send_json({"type": "error"})
    finally:
        with suppress(RuntimeError, WebSocketDisconnect):
            await client.close()
