"""Real PCM decoding and native-session contracts without downloading models."""

import asyncio
import json
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np
import server
from moonshine_voice.transcriber import Error, LineCompleted, LineTextChanged


class FakeStream:
    def __init__(self):
        self.closed = False
        self.samples = []

    def add_listener(self, callback):
        self.callback = callback

    def start(self):
        pass

    def emit(self, kind, text, line_id=1):
        self.callback(kind(SimpleNamespace(text=text, line_id=line_id), 1))

    def add_audio(self, samples, rate):
        self.samples.append((samples, rate))
        self.emit(LineTextChanged, "live words")

    def stop(self):
        self.emit(LineCompleted, "live words finished")
        return object()

    def close(self):
        self.closed = True


class FakeSocket:
    def __init__(self, frames):
        self.frames = iter(frames)
        self.started = False
        self.sent = []
        self.closed = False

    async def accept(self):
        pass

    async def receive(self):
        if not self.started:
            self.started = True
            return {"type": "websocket.receive", "text": json.dumps({"type": "start", "language": "en"})}
        return next(self.frames)

    async def send_json(self, event):
        self.sent.append(event)

    async def close(self, **kwargs):
        self.closed = True


class ProtocolTests(unittest.TestCase):
    def test_pcm16_decode_and_sequence_are_exact(self):
        budget = server.AudioBudget()
        self.assertFalse(budget.control({"type": "format", "sample_rate": 16000}))
        samples = budget.audio(b"\x00" * 4 + np.array([0, 16384, -16384], dtype="<i2").tobytes())
        np.testing.assert_array_equal(samples, [0, 0.5, -0.5])
        self.assertEqual(budget.sequence, 1)
        self.assertTrue(budget.control({"type": "end", "sequence": 1}))
        with self.assertRaises(ValueError):
            budget.audio(b"\x01\x00\x00\x00\x00\x00")

    def test_sample_rate_frame_and_duration_limits(self):
        for rate in [True, None, "16000", 7999, 48001]:
            with self.subTest(rate=rate), self.assertRaises(ValueError):
                server.AudioBudget().control({"type": "format", "sample_rate": rate})
        budget = server.AudioBudget()
        with self.assertRaises(ValueError):
            budget.audio(b"\x00" * 6)
        budget.control({"type": "format", "sample_rate": 8000})
        for frame in [b"", b"\x00" * 5, b"\x00" * (server.MAX_FRAME + 2), b"\x01\x00\x00\x00\x00\x00"]:
            with self.subTest(size=len(frame)), self.assertRaises(ValueError):
                budget.audio(frame)
        with patch.object(server, "MAX_SECONDS", 0), self.assertRaises(ValueError):
            budget.audio(b"\x00" * 6)

    def test_finalized_lines_do_not_get_rewritten(self):
        stream = FakeStream()
        session = server.Session(SimpleNamespace(create_stream=lambda **kwargs: stream))
        stream.emit(LineTextChanged, "first")
        self.assertEqual(session.result()["text"], "first")
        self.assertIsNone(session.result())
        stream.emit(LineCompleted, "first final")
        stream.emit(LineTextChanged, "wrong rewrite")
        stream.emit(LineTextChanged, "next", 2)
        self.assertEqual(session.result()["text"], "first final next")
        session.close()
        self.assertTrue(stream.closed)
        self.assertFalse(session.lines)

    def test_native_errors_are_not_successful_empty_results(self):
        stream = FakeStream()
        session = server.Session(SimpleNamespace(create_stream=lambda **kwargs: stream))
        session.event(Error(RuntimeError("private detail"), 1))
        with self.assertRaisesRegex(RuntimeError, "Recognition failed"):
            session.result()
        session.close()


class SessionTests(unittest.IsolatedAsyncioTestCase):
    async def test_stream_emits_text_before_end_and_releases_state(self):
        stream = FakeStream()
        server.app.state.models = {"en": SimpleNamespace(create_stream=lambda **kwargs: stream)}
        socket = FakeSocket(
            [
                {"type": "websocket.receive", "text": '{"type":"format","sample_rate":16000}'},
                {"type": "websocket.receive", "bytes": b"\x00" * 6},
                {"type": "websocket.receive", "text": '{"type":"end","sequence":1}'},
            ]
        )
        await server.stream(socket)
        self.assertEqual([event["type"] for event in socket.sent], ["ready", "transcript", "done"])
        self.assertEqual(socket.sent[-1]["text"], "live words finished")
        self.assertEqual(len(stream.samples), 1, "Stop must not replay captured audio")
        self.assertTrue(stream.closed)
        self.assertTrue(socket.closed)
        self.assertFalse(server.slot.locked())

    async def test_disconnect_does_not_finalize_or_retain_audio(self):
        stream = FakeStream()
        server.app.state.models = {"en": SimpleNamespace(create_stream=lambda **kwargs: stream)}
        socket = FakeSocket([{"type": "websocket.disconnect"}])
        await server.stream(socket)
        self.assertEqual(socket.sent, [{"type": "ready"}])
        self.assertTrue(stream.closed)

    async def test_native_cancellation_waits_for_inference_to_finish(self):
        started, release = threading.Event(), threading.Event()

        def inference():
            started.set()
            release.wait(2)

        task = asyncio.create_task(server.native(inference))
        await asyncio.to_thread(started.wait, 1)
        task.cancel()
        await asyncio.sleep(0.01)
        self.assertFalse(task.done(), "A running native operation must retain its slot/state")
        release.set()
        with self.assertRaises(asyncio.CancelledError):
            await task

    async def test_cancelled_session_constructor_is_closed(self):
        started, release = threading.Event(), threading.Event()
        stream = FakeStream()

        def construct():
            started.set()
            release.wait(2)
            return server.Session(SimpleNamespace(create_stream=lambda **kwargs: stream))

        task = asyncio.create_task(server.native(construct))
        await asyncio.to_thread(started.wait, 1)
        task.cancel()
        release.set()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertTrue(stream.closed)

    async def test_cancelled_transcript_result_is_not_mistaken_for_model_handles(self):
        started, release = threading.Event(), threading.Event()

        def update():
            started.set()
            release.wait(2)
            return {"type": "transcript", "text": "acknowledged", "revision": 1}

        task = asyncio.create_task(server.native(update))
        await asyncio.to_thread(started.wait, 1)
        task.cancel()
        release.set()
        with self.assertRaises(asyncio.CancelledError):
            await task


if __name__ == "__main__":
    unittest.main()
