import asyncio
import io
import json
import wave

import pytest

from scripts import check_dictation_deployment as checker


def sample():
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(16000)
        audio.writeframes(b"\x00\x01" * 3)
    return buffer.getvalue()


@pytest.fixture
def route(monkeypatch):
    calls, sockets = [], []

    def request(url, token, body=None, origin=""):
        calls.append((url, body, origin))
        return {"languages": ["en", "de"]} if body is None else {"ticket": "single-use"}

    class Socket:
        def __init__(self):
            self.events = asyncio.Queue()
            self.frames = []
            self.closed = False
            self.fail_audio = False

        async def send(self, data):
            self.frames.append(data)
            if isinstance(data, bytes):
                event = (
                    {"type": "error"}
                    if self.fail_audio
                    else {"type": "transcript", "revision": 1, "text": "public sample"}
                )
                await self.events.put(json.dumps(event))
            else:
                kind = json.loads(data)["type"]
                if kind == "auth":
                    await self.events.put('{"type":"ready"}')
                if kind == "end":
                    await self.events.put('{"type":"done","revision":2,"text":"public sample"}')
            await asyncio.sleep(0)

        async def recv(self):
            return await self.events.get()

        async def close(self):
            self.closed = True

    async def connect(url, **kwargs):
        assert url == "wss://self-hosted.example/api/speech/stream"
        assert kwargs["proxy"] is None
        assert kwargs["origin"] == "https://self-hosted.example"
        socket = Socket()
        sockets.append(socket)
        return socket

    monkeypatch.setattr(checker, "request", request)
    monkeypatch.setattr(checker, "connect", connect)
    return calls, sockets, connect


@pytest.mark.asyncio
async def test_deployment_check_streams_once_and_reports_only_metadata(route):
    calls, sockets, _ = route
    result = await checker.check("https://self-hosted.example", "test-only", sample(), 1)
    assert result["audio_frames"] == 1
    assert result["updates_before_stop"] == 1
    assert "public sample" not in json.dumps(result)
    assert calls[-1][1] == {"language": "en"}
    assert calls[-1][2] == "https://self-hosted.example"
    assert len([x for x in sockets[0].frames if isinstance(x, bytes)]) == 1
    assert sockets[0].closed


@pytest.mark.asyncio
async def test_recording_failure_is_never_retried(route, monkeypatch):
    calls, sockets, connect = route

    async def fail_audio(*args, **kwargs):
        socket = await connect(*args, **kwargs)
        socket.fail_audio = True
        return socket

    monkeypatch.setattr(checker, "connect", fail_audio)
    with pytest.raises(RuntimeError):
        await checker.check("https://self-hosted.example", "test-only", sample(), 1)
    assert len(sockets) == 1
    assert sockets[0].closed
    assert len(calls) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "url",
    [
        "file://localhost",
        "http://remote.example",
        "https://user:password@self-hosted.example",
        "https://self-hosted.example?token=invalid",
    ],
)
async def test_deployment_check_rejects_unsafe_urls(url):
    with pytest.raises(ValueError):
        await checker.check(url, "", sample(), 1)


def test_authenticated_http_never_follows_redirects():
    assert checker.NoRedirect().redirect_request(None, None, 302, "", {}, "https://elsewhere.example") is None
    with pytest.raises(ValueError):
        checker.request("file:///tmp/not-a-network-resource", "test-only")
