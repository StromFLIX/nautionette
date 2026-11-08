"""Private CPU streaming recognizer. Model downloads at startup; inference stays local."""

import asyncio
import contextvars
import json
import os
from contextlib import asynccontextmanager, suppress
from functools import partial
from pathlib import Path

import numpy as np
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from moonshine_voice import ModelArch, Transcriber, get_model_for_language
from moonshine_voice.errors import MoonshineError
from moonshine_voice.transcriber import Error, LineCompleted, LineTextChanged

MAX_BYTES = 12 * 1024 * 1024
MAX_SECONDS = 120
MAX_FRAME = 65540
LANGUAGES = ("en", "de")
slot = asyncio.Semaphore(1)


async def native(function, *args):
    """Never free model state/release capacity while cancelled C++ work is still running."""
    # An executor Future is not an independently cancellable asyncio Task during
    # loop shutdown; only the actual native operation completes it.
    task = asyncio.get_running_loop().run_in_executor(
        None, contextvars.copy_context().run, partial(function, *args)
    )
    cancelled = False
    while True:
        try:
            result = await asyncio.shield(task)
            break
        except asyncio.CancelledError:
            if task.cancelled():
                raise
            cancelled = True
            # A second cancellation must not cancel the join while C++ is still
            # using model state. Keep shielding until the actual operation ends.
    if cancelled:
        # A cancelled constructor has not yet handed ownership to its caller.
        if isinstance(result, Session):
            await native(result.close)
        elif isinstance(result, dict) and all(isinstance(model, Transcriber) for model in result.values()):
            for model in result.values():
                await native(model.close)
        raise asyncio.CancelledError
    return result


def load_models(cache_root=Path("/models")):
    # A fixed CPU policy, not a dictation mode: serial inference must not create
    # dozens of competing ORT worker pools for two resident language models.
    os.environ["MOONSHINE_ORT_SINGLE_THREAD"] = "1"
    models = {}
    try:
        for language in LANGUAGES:
            path, arch = get_model_for_language(
                language, ModelArch.SMALL_STREAMING, cache_root=cache_root, on_progress=lambda *_: None
            )
            models[language] = Transcriber(model_path=path, model_arch=arch, update_interval=0.5)
        return models
    except Exception:
        for model in models.values():
            model.close()
        raise


@asynccontextmanager
async def lifespan(app):
    app.state.models = await native(load_models)
    try:
        yield
    finally:
        for model in app.state.models.values():
            await native(model.close)


app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None)


@app.get("/healthz")
async def health():
    return {"ready": True, "languages": list(LANGUAGES)}


class AudioBudget:
    def __init__(self):
        self.rate = None
        self.sequence = 0
        self.size = 0
        self.ended = False

    def control(self, event):
        if not isinstance(event, dict):
            raise ValueError("Invalid control")
        if event.get("type") == "format" and self.rate is None and not self.ended:
            rate = event.get("sample_rate")
            if type(rate) is not int or not 8000 <= rate <= 48000:
                raise ValueError("Invalid sample rate")
            self.rate = rate
            return False
        if (
            event.get("type") == "end"
            and not self.ended
            and type(event.get("sequence")) is int
            and event["sequence"] == self.sequence
        ):
            self.ended = True
            return True
        raise ValueError("Invalid control")

    def audio(self, frame):
        if self.ended or self.rate is None or not 6 <= len(frame) <= MAX_FRAME or len(frame) % 2:
            raise ValueError("Invalid audio")
        if int.from_bytes(frame[:4], "little") != self.sequence or self.sequence >= 4096:
            raise ValueError("Invalid sequence")
        self.sequence += 1
        self.size += len(frame) - 4
        if self.size > MAX_BYTES or self.size > self.rate * 2 * MAX_SECONDS:
            raise ValueError("Recording exceeds limits")
        return np.frombuffer(frame, dtype="<i2", offset=4).astype(np.float32) / 32768.0


class Session:
    def __init__(self, model):
        self.lines = {}
        self.errors = []
        self.revision = 0
        self.last_text = ""
        self.stream = model.create_stream(update_interval=0.5)
        try:
            self.stream.add_listener(self.event)
            self.stream.start()
        except Exception:
            self.stream.close()
            raise

    def event(self, event):
        if isinstance(event, Error):
            self.errors.append(event.error)
        elif isinstance(event, (LineTextChanged, LineCompleted)):
            line = event.line
            # Completed lines cannot be rewritten by a later hypothesis.
            previous = self.lines.get(line.line_id)
            if previous and previous[1]:
                return
            if line.line_id not in self.lines and len(self.lines) >= 256:
                self.errors.append(ValueError("Transcript exceeds limits"))
                return
            self.lines[line.line_id] = (line.text.strip(), isinstance(event, LineCompleted))

    def result(self, final=False):
        if self.errors:
            raise RuntimeError("Recognition failed") from self.errors[0]
        text = " ".join(text for text, _ in self.lines.values() if text)
        if len(text) > 16000:
            raise ValueError("Transcript exceeds limits")
        if not final and text == self.last_text:
            return None
        self.last_text = text
        self.revision += 1
        return {"type": "done" if final else "transcript", "revision": self.revision, "text": text}

    def append(self, samples, rate):
        # Moonshine resamples statefully when the webview supplies another sample rate.
        self.stream.add_audio(samples.tolist(), rate)
        return self.result()

    def finish(self):
        result = self.stream.stop()
        if result is None:
            raise RuntimeError("Recognition failed")
        return self.result(final=True)

    def close(self):
        self.stream.close()
        self.lines.clear()
        self.errors.clear()


@app.websocket("/v1/audio/stream")
async def stream(socket: WebSocket):
    if slot.locked():
        await socket.close(code=1013)
        return
    async with slot:
        await socket.accept()
        session = None
        try:
            first = await asyncio.wait_for(socket.receive(), timeout=5)
            if first["type"] == "websocket.disconnect":
                return
            raw = first.get("text")
            if not isinstance(raw, str) or len(raw) > 256:
                raise ValueError("Invalid start")
            start = json.loads(raw)
            if (
                not isinstance(start, dict)
                or start.get("type") != "start"
                or start.get("language") not in LANGUAGES
            ):
                raise ValueError("Unsupported language")
            session = await native(Session, app.state.models[start["language"]])
            await socket.send_json({"type": "ready"})
            budget = AudioBudget()
            async with asyncio.timeout(180):
                while True:
                    message = await asyncio.wait_for(socket.receive(), timeout=30)
                    if message["type"] == "websocket.disconnect":
                        break
                    if message.get("bytes") is not None:
                        samples = budget.audio(message["bytes"])
                        result = await native(session.append, samples, budget.rate)
                    else:
                        raw = message.get("text", "")
                        if len(raw) > 256:
                            raise ValueError("Invalid control")
                        if not budget.control(json.loads(raw)):
                            continue
                        result = await native(session.finish)
                    if result:
                        await socket.send_json(result)
                    if budget.ended:
                        break
        except WebSocketDisconnect:
            pass
        except (ValueError, TypeError, RuntimeError, TimeoutError, MoonshineError):
            with suppress(RuntimeError, WebSocketDisconnect):
                await socket.send_json({"type": "error"})
        finally:
            if session is not None:
                await native(session.close)
            with suppress(RuntimeError, WebSocketDisconnect):
                await socket.close()
