"""Private CPU transcription appliance. Standard OpenAI multipart API; no audio persistence."""

import asyncio
import io
import os
from contextlib import asynccontextmanager

import av
import numpy as np
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from faster_whisper import WhisperModel

MAX_BYTES = 12 * 1024 * 1024
MAX_SECONDS = 120
RATE = 16000
MODEL = os.environ.get("STT_MODEL", "small")
slot = asyncio.Semaphore(1)


@asynccontextmanager
async def lifespan(app):
    app.state.model = await asyncio.to_thread(
        WhisperModel,
        MODEL,
        device="cpu",
        compute_type="int8",
        cpu_threads=int(os.environ.get("STT_THREADS", "4")),
        num_workers=1,
        download_root="/models",
    )
    yield


app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None)


@app.get("/healthz")
async def health():
    return {"ready": True}


def decode_audio(data):
    """Bound decoded samples as well as compressed bytes (including hostile uploads)."""
    chunks = []
    count = 0
    try:
        with av.open(io.BytesIO(data)) as container:
            resampler = av.AudioResampler(format="s16", layout="mono", rate=RATE)
            for frame in container.decode(audio=0):
                for converted in resampler.resample(frame):
                    count += converted.samples
                    if count > RATE * MAX_SECONDS:
                        raise ValueError("Recording exceeds 120 seconds")
                    chunks.append(converted.to_ndarray().flatten())
            for converted in resampler.resample(None):
                count += converted.samples
                if count > RATE * MAX_SECONDS:
                    raise ValueError("Recording exceeds 120 seconds")
                chunks.append(converted.to_ndarray().flatten())
        if not count:
            raise ValueError("Empty audio")
        return np.concatenate(chunks).astype(np.float32) / 32768.0
    except (av.error.FFmpegError, IndexError) as exc:
        raise ValueError("Invalid audio") from exc


def recognize(model, data, language):
    audio = decode_audio(data)
    segments, _ = model.transcribe(
        audio, language=language or None, beam_size=5, vad_filter=True, condition_on_previous_text=False
    )
    return "".join(segment.text for segment in segments).strip()


@app.post("/v1/audio/transcriptions")
async def transcribe(
    file: UploadFile = File(...),
    model: str = Form(MODEL),
    language: str = Form(""),
    response_format: str = Form("json"),
):
    # The loaded model is fixed at startup: clients cannot trigger model downloads.
    if model != MODEL or response_format != "json":
        raise HTTPException(422, "Unsupported model or response format")
    if slot.locked():
        raise HTTPException(429, "Transcription busy")
    async with slot:
        try:
            data = await file.read(MAX_BYTES + 1)
            if len(data) > MAX_BYTES:
                raise HTTPException(413, "Audio too large")
            text = await asyncio.to_thread(recognize, app.state.model, data, language)
            return {"text": text}
        except ValueError as exc:
            raise HTTPException(422, "Invalid audio, language or recording over 120 seconds") from exc
        finally:
            await file.close()
