"""Appliance contract tests: real codecs and bounds, no model download required."""

import io
import unittest
import wave
from types import SimpleNamespace
from unittest.mock import patch

import server
from fastapi import HTTPException, UploadFile


def wav(seconds=1, rate=48000, channels=2):
    out = io.BytesIO()
    with wave.open(out, "wb") as audio:
        audio.setnchannels(channels)
        audio.setsampwidth(2)
        audio.setframerate(rate)
        audio.writeframes(b"\0\0" * rate * seconds * channels)
    return out.getvalue()


class AudioTests(unittest.TestCase):
    def test_downmix_and_resample(self):
        samples = server.decode_audio(wav())
        self.assertEqual(samples.shape, (16000,))
        self.assertEqual(str(samples.dtype), "float32")
        self.assertEqual(float(samples.max()), 0)

    def test_invalid_audio(self):
        with self.assertRaises(ValueError):
            server.decode_audio(b"not audio")

    def test_decoded_duration_is_bounded(self):
        with patch.object(server, "MAX_SECONDS", 1), self.assertRaises(ValueError):
            server.decode_audio(wav(seconds=2))


class EndpointTests(unittest.IsolatedAsyncioTestCase):
    async def test_transcription_contract(self):
        calls = []

        def transcribe(audio, **kwargs):
            calls.append((audio, kwargs))
            return iter([SimpleNamespace(text=" Hello"), SimpleNamespace(text=" world.")]), None

        server.app.state.model = SimpleNamespace(transcribe=transcribe)
        upload = UploadFile(io.BytesIO(wav()), filename="speech.wav")
        result = await server.transcribe(upload, server.MODEL, "de", "json")
        self.assertEqual(result, {"text": "Hello world."})
        self.assertEqual(calls[0][1]["language"], "de")
        self.assertTrue(calls[0][1]["vad_filter"])
        self.assertTrue(upload.file.closed)

    async def test_oversized_file_is_closed(self):
        upload = UploadFile(io.BytesIO(b"xx"), filename="speech.wav")
        with patch.object(server, "MAX_BYTES", 1), self.assertRaises(HTTPException) as error:
            await server.transcribe(upload, server.MODEL, "", "json")
        self.assertEqual(error.exception.status_code, 413)
        self.assertTrue(upload.file.closed)

    async def test_unknown_model_cannot_download(self):
        upload = UploadFile(io.BytesIO(b"x"), filename="speech.wav")
        with self.assertRaises(HTTPException) as error:
            await server.transcribe(upload, "untrusted/model", "", "json")
        self.assertEqual(error.exception.status_code, 422)


if __name__ == "__main__":
    unittest.main()
