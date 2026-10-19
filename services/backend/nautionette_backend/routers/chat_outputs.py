"""Authenticated output presentation plus a narrowly scoped agent transport."""

from __future__ import annotations

import json
from typing import Any
from urllib.parse import quote

import jsonschema
from anyio import CapacityLimiter
from anyio.lowlevel import RunVar
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import Response, StreamingResponse
from starlette.concurrency import run_in_threadpool

from .. import chat_outputs, output_storage
from ..db import db
from ..events import bus
from ..security import require_user

router = APIRouter(dependencies=[Depends(require_user)])
# Per event loop, not per request or a semaphore tied to a stale test/startup loop.
# Image decoding must not consume every thread or an unbounded amount of memory.
_publication_slots: RunVar[CapacityLimiter] = RunVar("chat_output_publication_slots")


def _limiter() -> CapacityLimiter:
    try:
        return _publication_slots.get()
    except LookupError:
        limiter = CapacityLimiter(2)
        _publication_slots.set(limiter)
        return limiter


def _parse(raw: bytearray, schema: dict[str, Any]) -> dict[str, Any]:
    try:
        payload = json.loads(raw)
        jsonschema.validate(payload, schema)
    except (ValueError, jsonschema.ValidationError) as exc:
        # Validation errors can contain the supplied capability or file bytes.
        # Neither may appear in logs, tool results or model context.
        raise output_storage.failure(
            "invalid_output_request", "Invalid output request; check the tool's schema and limits.", 422
        ) from exc
    return payload


async def _payload(request: Request, schema: dict[str, Any], limit: int) -> dict[str, Any]:
    raw = bytearray()
    async for chunk in request.stream():
        if len(raw) + len(chunk) > limit:
            raise output_storage.failure(
                "output_request_too_large",
                "Output control request exceeds its transport safety limit. "
                "Rebuild the agent image to use metadata-only binary transfers.",
                413,
            )
        raw.extend(chunk)
    # Large JSON/base64 payloads must not stall the chat streaming loop.
    return await run_in_threadpool(_parse, raw, schema)


def _body(schema: dict[str, Any]) -> dict[str, Any]:
    return {"requestBody": {"required": True, "content": {"application/json": {"schema": schema}}}}


@router.post("/api/chat-outputs/prepare", openapi_extra=_body(chat_outputs.PREPARE_SCHEMA))
async def prepare_chat_output(request: Request) -> dict[str, Any]:
    """Private output control transport: reserve quota and prepare binary uploads.

    Transfer URLs and capabilities must stay inside the local publish_output tool.
    """
    async with _limiter():
        payload = await _payload(request, chat_outputs.PREPARE_SCHEMA, 65_536)
        return await run_in_threadpool(chat_outputs.prepare, payload)


@router.post("/api/chat-outputs/publish", openapi_extra=_body(chat_outputs.PUBLISH_SCHEMA))
async def publish_chat_output(request: Request) -> dict[str, Any]:
    """Private transport for publish_output; requires a live chat/turn capability.

    Use the local publish_output tool to read sandbox files. Never put file bytes
    or capabilities into model-facing tool arguments.
    """
    async with _limiter():
        payload = await _payload(request, chat_outputs.PUBLISH_SCHEMA, chat_outputs.MAX_REQUEST_BYTES)
        chat_id, _ = chat_outputs.grant_scope(payload["grant"])
        output = await run_in_threadpool(chat_outputs.publish, payload)
    # The event bus owns asyncio.Queue subscribers: notify on the event loop,
    # after the worker has committed and released the database lock.
    bus.publish(
        "chat.output.published",
        {"chat_id": chat_id, "output_id": output["id"], "revision": output["revision"]},
    )
    return output


@router.post("/api/chat-outputs/read", openapi_extra=_body(chat_outputs.READ_SCHEMA))
async def read_chat_output(request: Request) -> dict[str, Any]:
    """Private transport for read_output, scoped to the caller's live chat/turn."""
    payload = await _payload(request, chat_outputs.READ_SCHEMA, 4096)
    return await run_in_threadpool(chat_outputs.read, payload)


@router.get("/api/chats/{chat_id}/outputs")
def list_chat_outputs(chat_id: str) -> dict[str, Any]:
    if not db.get_chat(chat_id):
        raise HTTPException(404, "Chat not found")
    return {"outputs": db.list_chat_outputs(chat_id)}


@router.get("/api/chats/{chat_id}/outputs/{output_id}/revisions")
def list_output_revisions(chat_id: str, output_id: str) -> dict[str, Any]:
    return {"revisions": chat_outputs.revisions(chat_id, output_id)}


@router.get("/api/chats/{chat_id}/outputs/{output_id}/revisions/{revision}/files/{file_id}")
def get_output_file(
    chat_id: str, output_id: str, revision: int, file_id: str, preview: bool = False
) -> Response:
    stored = chat_outputs.file_metadata(chat_id, output_id, revision, file_id)
    if preview and stored["preview"] is None and not stored["preview_key"]:
        raise HTTPException(404, "This file has no image preview")
    # Never redirect user downloads to S3. Preserve chat authorization and the
    # download-only boundary even when the original is HTML/SVG/PDF.
    headers = {
        "Cache-Control": "private, no-store",
        "X-Content-Type-Options": "nosniff",
        "Content-Security-Policy": "default-src 'none'; sandbox",
        "Content-Disposition": "inline"
        if preview
        else f"attachment; filename*=UTF-8''{quote(stored['name'], safe='')}",
    }
    media_type = "image/png" if preview else "application/octet-stream"
    key = stored["preview_key"] if preview else stored["object_key"]
    if not key:
        return Response(
            stored["preview"] if preview else stored["data"], media_type=media_type, headers=headers
        )
    body, size = output_storage.get_store().open(key)

    def chunks():
        try:
            while chunk := body.read(1024 * 1024):
                yield chunk
        finally:
            body.close()

    return StreamingResponse(
        chunks(), media_type=media_type, headers={**headers, "Content-Length": str(size)}
    )
