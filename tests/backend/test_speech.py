import asyncio
import json
from contextlib import asynccontextmanager

import pytest
from nautionette_backend.routers import speech
from starlette.websockets import WebSocketDisconnect


@pytest.fixture(autouse=True)
def reset_tickets():
    speech._tickets.clear()
    yield
    speech._tickets.clear()


@pytest.fixture
def appliance(monkeypatch):
    calls, sessions = [], []

    class Upstream:
        def __init__(self):
            self.events = asyncio.Queue()
            self.frames = []
            self.revision = 0
            self.closed = False

        async def send(self, data):
            self.frames.append(data)
            if isinstance(data, bytes):
                self.revision += 1
                await self.events.put(
                    json.dumps({"type": "transcript", "revision": self.revision, "text": "Recognized live."})
                )
                return
            event = json.loads(data)
            if event["type"] == "start":
                await self.events.put('{"type":"ready"}')
            if event["type"] == "end":
                self.revision += 1
                await self.events.put(
                    json.dumps(
                        {
                            "type": "done",
                            "revision": self.revision,
                            "text": "Recognized live." if self.revision > 1 else "",
                        }
                    )
                )

        async def recv(self):
            return await self.events.get()

    @asynccontextmanager
    async def connect(url, **kwargs):
        calls.append((url, kwargs))
        upstream = Upstream()
        sessions.append(upstream)
        try:
            yield upstream
        finally:
            upstream.closed = True

    monkeypatch.setattr(speech, "connect", connect)
    return calls, sessions


def ticket(client, language="en", **kwargs):
    response = client.post("/api/speech/stream-ticket", json={"language": language}, **kwargs)
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    return response.json()["ticket"]


def test_public_socket_transport_enforces_bounds_before_router_allocation(monkeypatch):
    from nautionette_backend import server

    configs = []

    class Server:
        def __init__(self, config):
            configs.append(config)

        def run(self):
            pass

    monkeypatch.setattr(server, "DrainingServer", Server)
    server.main()
    assert configs[0].ws_max_size == speech.MAX_FRAME
    assert configs[0].ws_max_queue == 4
    assert configs[0].ws_per_message_deflate is False


def test_only_self_hosted_streaming_is_exposed(anonymous, client):
    assert anonymous.get("/api/speech").status_code == 401
    assert anonymous.post("/api/speech/stream-ticket", json={"language": "en"}).status_code == 401
    assert client.get("/api/speech").json()["languages"] == ["en", "de"]
    paths = client.get("/api/openapi.json").json()["paths"]
    assert "/api/speech/cleanup" not in paths
    assert "/api/speech/transcribe" not in paths
    assert client.post("/api/speech/stream-ticket", json={"language": "cloud/anything"}).status_code == 422


def test_streams_audio_and_finalizes_without_uploading_it_again(client, appliance, backend):
    calls, sessions = appliance
    auth = ticket(client, "de")
    with client.websocket_connect("/api/speech/stream") as socket:
        socket.send_json({"type": "auth", "ticket": auth})
        assert socket.receive_json() == {"type": "ready"}
        socket.send_json({"type": "format", "sample_rate": 16000})
        frame = b"\x00\x00\x00\x00\x01\x00"
        socket.send_bytes(frame)
        assert socket.receive_json() == {"type": "transcript", "revision": 1, "text": "Recognized live."}
        socket.send_json({"type": "end", "sequence": 1})
        assert socket.receive_json() == {"type": "done", "revision": 2, "text": "Recognized live."}
    assert calls[0][0] == "ws://stt:8000/v1/audio/stream"
    assert calls[0][1]["proxy"] is None
    assert sessions[0].frames.count(frame) == 1
    assert json.loads(sessions[0].frames[0]) == {"type": "start", "language": "de"}
    assert sessions[0].closed
    assert backend.db.query("SELECT * FROM messages") == []
    with client.websocket_connect("/api/speech/stream") as socket:
        socket.send_json({"type": "auth", "ticket": auth})
        with pytest.raises(WebSocketDisconnect) as error:
            socket.receive_json()
        assert error.value.code == 1008
    assert len(calls) == 1


@pytest.mark.parametrize(
    "auth", [{}, [], {"type": "auth", "ticket": "invalid"}, {"type": "auth", "ticket": []}]
)
def test_unauthenticated_socket_allocates_no_upstream(client, appliance, auth):
    with client.websocket_connect("/api/speech/stream") as socket:
        socket.send_json(auth)
        with pytest.raises(WebSocketDisconnect):
            socket.receive_json()
    assert not appliance[0]


def test_waiting_unauthenticated_handshake_does_not_block_authenticated_dictation(client, appliance):
    auth = ticket(client)
    with client.websocket_connect("/api/speech/stream"):
        with client.websocket_connect("/api/speech/stream") as authenticated:
            authenticated.send_json({"type": "auth", "ticket": auth})
            assert authenticated.receive_json() == {"type": "ready"}
    assert len(appliance[0]) == 1
    assert not speech._slot.locked()


def test_binary_auth_frame_is_rejected_without_a_private_connection(client, appliance):
    with client.websocket_connect("/api/speech/stream") as socket:
        socket.send_bytes(b"invalid auth")
        with pytest.raises(WebSocketDisconnect) as error:
            socket.receive_json()
        assert error.value.code == 1008
    assert not appliance[0]


def test_ticket_origin_and_expiry_are_enforced(client, appliance):
    auth = ticket(client, headers={"Origin": "https://my-app.example"})
    with client.websocket_connect(
        "/api/speech/stream", headers={"Origin": "https://other.example"}
    ) as socket:
        socket.send_json({"type": "auth", "ticket": auth})
        with pytest.raises(WebSocketDisconnect):
            socket.receive_json()
    auth = ticket(client)
    speech._tickets[auth] = (0, "", "en")
    with client.websocket_connect("/api/speech/stream") as socket:
        socket.send_json({"type": "auth", "ticket": auth})
        with pytest.raises(WebSocketDisconnect):
            socket.receive_json()
    assert not appliance[0]


def test_ticket_storage_is_bounded_and_expired_entries_are_pruned(client):
    for n in range(128):
        speech._tickets[str(n)] = (float("inf"), "", "en")
    assert client.post("/api/speech/stream-ticket", json={"language": "en"}).status_code == 429
    speech._tickets["0"] = (0, "", "en")
    ticket(client)
    assert len(speech._tickets) == 128


@pytest.mark.parametrize(
    "bad",
    [
        {"type": "format", "sample_rate": True},
        {"type": "format", "sample_rate": 192000},
        {"type": "end", "sequence": 2},
        [],
        b"x" * (speech.MAX_FRAME + 1),
        b"\x02\x00\x00\x00\x00\x00",
    ],
)
def test_bad_frames_fail_without_exposing_content(client, appliance, bad):
    auth = ticket(client)
    with client.websocket_connect("/api/speech/stream") as socket:
        socket.send_json({"type": "auth", "ticket": auth})
        assert socket.receive_json()["type"] == "ready"
        if isinstance(bad, bytes):
            socket.send_json({"type": "format", "sample_rate": 16000})
            socket.send_bytes(bad)
        else:
            socket.send_json(bad)
        assert socket.receive_json() == {"type": "error"}
    assert appliance[1][0].closed


def test_over_limit_audio_is_not_forwarded(client, appliance, monkeypatch):
    monkeypatch.setattr(speech, "MAX_BYTES", 1)
    auth = ticket(client)
    with client.websocket_connect("/api/speech/stream") as socket:
        socket.send_json({"type": "auth", "ticket": auth})
        socket.receive_json()
        socket.send_json({"type": "format", "sample_rate": 16000})
        socket.send_bytes(b"\x00" * 6)
        assert socket.receive_json() == {"type": "error"}
    assert not any(isinstance(frame, bytes) for frame in appliance[1][0].frames)


def test_cancel_releases_private_session(client, appliance):
    auth = ticket(client)
    with client.websocket_connect("/api/speech/stream") as socket:
        socket.send_json({"type": "auth", "ticket": auth})
        socket.receive_json()
    assert appliance[1][0].closed
    assert not speech._slot.locked()


def test_private_endpoint_failures_are_sanitized(client, monkeypatch):
    @asynccontextmanager
    async def unavailable(*args, **kwargs):
        raise OSError("secret transcript and credentials")
        yield

    monkeypatch.setattr(speech, "connect", unavailable)
    auth = ticket(client)
    with client.websocket_connect("/api/speech/stream") as socket:
        socket.send_json({"type": "auth", "ticket": auth})
        assert socket.receive_json() == {"type": "error"}
