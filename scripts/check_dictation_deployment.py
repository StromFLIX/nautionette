"""Verify private streaming dictation through the deployed HTTPS/WebSocket route.

Only the public JFK sample is used. Prints timings/counts, never tokens/transcripts.
Run with APP_URL and APP_TOKEN, like the other staging deployment checks.
"""

import asyncio
import hashlib
import json
import os
import struct
import time
import urllib.error
import urllib.request
import wave
from io import BytesIO
from urllib.parse import urlsplit, urlunsplit

from websockets.asyncio.client import connect
from websockets.exceptions import WebSocketException

SAMPLE_URL = "https://raw.githubusercontent.com/ggml-org/whisper.cpp/master/samples/jfk.wav"
SAMPLE_HASH = "59dfb9a4acb36fe2a2affc14bacbee2920ff435cb13cc314a08c13f66ba7860e"


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None  # Never forward application credentials to a redirect target.


def request(url, token, body=None, origin=""):
    parsed = urlsplit(url)
    if (
        parsed.scheme not in {"https", "http"}
        or (parsed.scheme == "http" and parsed.hostname not in {"localhost", "127.0.0.1"})
        or parsed.username
        or parsed.password
    ):
        raise ValueError("Only credential-free HTTPS or loopback URLs are permitted")
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    if origin:
        headers["Origin"] = origin
    if body is not None:
        headers["Content-Type"] = "application/json"
    with urllib.request.build_opener(NoRedirect).open(
        urllib.request.Request(  # noqa: S310 - HTTPS/loopback checked above; redirects disabled.
            url, data=json.dumps(body).encode() if body is not None else None, headers=headers
        ),
        timeout=15,
    ) as response:
        return json.load(response)


async def check(url, token, sample, ready_timeout=300):
    parsed = urlsplit(url)
    if (
        parsed.scheme not in {"https", "http"}
        or (parsed.scheme != "https" and parsed.hostname not in {"localhost", "127.0.0.1"})
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("Dictation deployment checks require HTTPS or loopback")
    origin = urlunsplit((parsed.scheme, parsed.netloc, "", "", ""))
    socket_url = urlunsplit(
        (
            "wss" if parsed.scheme == "https" else "ws",
            parsed.netloc,
            parsed.path.rstrip("/") + "/api/speech/stream",
            "",
            "",
        )
    )
    with wave.open(BytesIO(sample)) as audio:
        if audio.getnchannels() != 1 or audio.getsampwidth() != 2:
            raise ValueError("Test sample must be mono PCM16")
        rate = audio.getframerate()
        pcm = audio.readframes(audio.getnframes())
    capability = await asyncio.to_thread(request, url + "/api/speech", token)
    if capability.get("languages") != ["en", "de"]:
        raise RuntimeError("Self-hosted English/German streaming is not available")
    deadline = time.monotonic() + ready_timeout
    # Retry readiness only, before any audio has been sent. Never replay a recording.
    while True:
        socket = None
        try:
            ticket = await asyncio.to_thread(
                request, url + "/api/speech/stream-ticket", token, {"language": "en"}, origin
            )
            socket = await connect(
                socket_url,
                origin=origin,
                open_timeout=10,
                close_timeout=1,
                max_size=20000,
                compression=None,
                proxy=None,
            )
            await socket.send(json.dumps({"type": "auth", "ticket": ticket["ticket"]}))
            if json.loads(await asyncio.wait_for(socket.recv(), 15)) != {"type": "ready"}:
                raise RuntimeError("Private recognizer is not ready")
            break
        except (OSError, RuntimeError, TimeoutError, WebSocketException) as error:
            if socket is not None:
                await socket.close()
            if isinstance(error, urllib.error.HTTPError) and error.code in {401, 403}:
                raise RuntimeError("Dictation check authentication failed") from None
            if time.monotonic() >= deadline:
                raise RuntimeError("Private dictation service did not become ready") from None
            await asyncio.sleep(3)
    partials = 0
    stopping = False
    before_stop = 0

    async def receive():
        nonlocal partials, before_stop
        revision = -1
        while True:
            event = json.loads(await socket.recv())
            if (
                event.get("type") not in {"transcript", "done"}
                or not isinstance(event.get("text"), str)
                or type(event.get("revision")) is not int
                or event["revision"] <= revision
            ):
                raise RuntimeError("Invalid streaming dictation response")
            revision = event["revision"]
            if event["type"] == "done":
                if not stopping or not event["text"].strip():
                    raise RuntimeError("Dictation did not return a completed transcript")
                return len(event["text"])
            if event["text"].strip():
                partials += 1
                if not stopping:
                    before_stop += 1

    receiver = asyncio.create_task(receive())
    try:
        await socket.send(json.dumps({"type": "format", "sample_rate": rate}))
        chunk_bytes = int(rate * 0.128) * 2
        started = time.monotonic()
        sequence = 0
        for offset in range(0, len(pcm), chunk_bytes):
            if receiver.done():
                receiver.result()
                raise RuntimeError("Dictation ended before Stop")
            chunk = pcm[offset : offset + chunk_bytes]
            await asyncio.sleep(max(0, started + (offset + len(chunk)) / (rate * 2) - time.monotonic()))
            await socket.send(struct.pack("<I", sequence) + chunk)
            sequence += 1
        stopping = True
        stopped = time.monotonic()
        await socket.send(json.dumps({"type": "end", "sequence": sequence}))
        characters = await asyncio.wait_for(receiver, 15)
        if not before_stop:
            raise RuntimeError("No live text was received before Stop")
        return {
            "languages": capability["languages"],
            "audio_frames": sequence,
            "live_updates": partials,
            "updates_before_stop": before_stop,
            "final_characters": characters,
            "stop_wait_ms": round((time.monotonic() - stopped) * 1000),
        }
    finally:
        receiver.cancel()
        await asyncio.gather(receiver, return_exceptions=True)
        await socket.close()


def main():
    url = os.environ.get("APP_URL", "").rstrip("/")
    token = os.environ.get("APP_TOKEN", "")
    if not url:
        raise RuntimeError("APP_URL is required")
    with urllib.request.urlopen(SAMPLE_URL, timeout=30) as response:
        sample = response.read(1024 * 1024)
    if hashlib.sha256(sample).hexdigest() != SAMPLE_HASH:
        raise RuntimeError("Public test sample hash changed")
    try:
        print(json.dumps(asyncio.run(check(url, token, sample))))
    except Exception:
        # No websocket error payload, credential-bearing URL or private text in CI logs.
        raise RuntimeError("Self-hosted dictation deployment check failed") from None


if __name__ == "__main__":
    main()
