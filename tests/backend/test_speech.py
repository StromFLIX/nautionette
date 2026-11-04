import json
from types import SimpleNamespace

import httpx
import pytest
from nautionette_backend.clients import gateway
from nautionette_backend.clients.agentgateway import GatewayClient
from nautionette_backend.config import settings
from nautionette_backend.routers import speech


def events(response):
    return [json.loads(line[5:]) for line in response.text.splitlines() if line.startswith("data:")]


@pytest.fixture
def stt(monkeypatch):
    calls = []
    monkeypatch.setattr(settings, "stt_url", "http://private-stt:8000/v1")
    monkeypatch.setattr(settings, "stt_model", "small")
    monkeypatch.setattr(settings, "stt_api_key", "private-key")

    async def post(url, **kwargs):
        calls.append((url, kwargs))
        return httpx.Response(200, json={"text": "hello world"}, request=httpx.Request("POST", url))

    monkeypatch.setattr(speech, "shared", lambda: SimpleNamespace(post=post))
    return calls


def test_dictation_requires_auth_and_configuration(anonymous, client, monkeypatch):
    assert anonymous.get("/api/speech").status_code == 401
    assert anonymous.post("/api/speech/transcribe", content=b"x").status_code == 401
    assert anonymous.post("/api/speech/cleanup", json={"text": "raw"}).status_code == 401
    monkeypatch.setattr(settings, "stt_url", "")
    assert client.get("/api/speech").json()["enabled"] is False
    assert client.post("/api/speech/transcribe", content=b"x").status_code == 503


def test_raw_transcription_is_ephemeral_and_compatible(client, stt, backend):
    response = client.post(
        "/api/speech/transcribe?language=de",
        content=b"audio",
        headers={"Content-Type": "audio/webm;codecs=opus"},
    )
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert events(response) == [
        {"type": "transcript", "text": "hello world"},
        {"type": "delta", "text": "hello world"},
        {"type": "done", "text": "hello world"},
    ]
    url, options = stt[0]
    assert url == "http://private-stt:8000/v1/audio/transcriptions"
    assert options["data"] == {"model": "small", "response_format": "json", "language": "de"}
    assert options["headers"] == {"Authorization": "Bearer private-key"}
    assert options["files"]["file"] == ("dictation.webm", b"audio", "audio/webm")
    assert backend.db.query("SELECT * FROM messages") == []


def test_cleanup_streams_using_selected_model(client, stt, monkeypatch):
    calls = []

    async def cleanup(model, instructions, text):
        calls.append((model, instructions, text))
        yield "Hello"
        yield ", world."

    monkeypatch.setattr(gateway, "dictation_cleanup", cleanup)
    response = client.post(
        "/api/speech/transcribe?cleanup=true&model=test/chosen",
        content=b"audio",
        headers={"Content-Type": "audio/mp4"},
    )
    assert [event["text"] for event in events(response)] == [
        "hello world",
        "Hello",
        ", world.",
        "Hello, world.",
    ]
    assert calls[0][0] == "test/chosen"
    assert "not an assistant answering" in calls[0][1]


def test_live_cleanup_does_not_retranscribe_audio(client, stt, monkeypatch):
    async def cleanup(model, instructions, text):
        assert model == "test/chosen"
        assert text == "um fix the test"
        yield "Fix the test."

    monkeypatch.setattr(gateway, "dictation_cleanup", cleanup)
    response = client.post("/api/speech/cleanup", json={"text": "um fix the test", "model": "test/chosen"})
    assert events(response)[-1] == {"type": "done", "text": "Fix the test."}
    assert not stt
    assert client.post("/api/speech/cleanup", json={"text": "x" * 16001}).status_code == 422


@pytest.mark.parametrize("empty", [False, True])
def test_failed_or_empty_cleanup_preserves_original(client, stt, monkeypatch, empty):
    async def cleanup(*args):
        if empty:
            return
        yield "Partial"
        raise httpx.ReadError("upstream secret")

    monkeypatch.setattr(gateway, "dictation_cleanup", cleanup)
    response = client.post(
        "/api/speech/transcribe?cleanup=true", content=b"audio", headers={"Content-Type": "audio/ogg"}
    )
    last = events(response)[-1]
    assert last["text"] == "hello world"
    assert "original transcript" in last["warning"]
    assert "secret" not in response.text


@pytest.mark.parametrize(
    "payload,mime,code", [(b"", "audio/webm", 422), (b"x", "text/plain", 415), (b"xx", "audio/webm", 413)]
)
def test_invalid_uploads_are_bounded(client, stt, monkeypatch, payload, mime, code):
    monkeypatch.setattr(speech, "MAX_BYTES", 1)
    assert (
        client.post("/api/speech/transcribe", content=payload, headers={"Content-Type": mime}).status_code
        == code
    )
    assert not stt


def test_upstream_errors_are_sanitized(client, stt, monkeypatch):
    async def post(*args, **kwargs):
        return httpx.Response(
            500, text="credentials and transcript", request=httpx.Request("POST", "http://private-stt")
        )

    monkeypatch.setattr(speech, "shared", lambda: SimpleNamespace(post=post))
    response = client.post("/api/speech/transcribe", content=b"x", headers={"Content-Type": "audio/webm"})
    assert response.status_code == 502
    assert "credentials" not in response.text


@pytest.mark.asyncio
@pytest.mark.parametrize("model", ["test/model", "copilot/model"])
async def test_gateway_cleanup_stream_routes_and_bounds(model, monkeypatch):
    from nautionette_backend.clients import agentgateway

    calls = []

    def reply(request):
        calls.append((request.url.path, json.loads(request.content)))
        frames = (
            [{"type": "response.output_text.delta", "delta": "Edited."}, {"type": "response.completed"}]
            if model.startswith("copilot/")
            else [{"choices": [{"delta": {"content": "Edited."}}]}, {"choices": [{"finish_reason": "stop"}]}]
        )
        return httpx.Response(200, text="".join(f"data: {json.dumps(item)}\n\n" for item in frames))

    async with httpx.AsyncClient(transport=httpx.MockTransport(reply)) as http:
        monkeypatch.setattr(agentgateway, "shared", lambda: http)
        result = [
            part async for part in GatewayClient("http://gateway").dictation_cleanup(model, "edit", "raw")
        ]
    assert result == ["Edited."]
    path, body = calls[0]
    assert path == ("/v1/responses" if model.startswith("copilot/") else "/v1/chat/completions")
    assert body["stream"] is True
    assert body["model"] == model
    assert "tools" not in body


@pytest.mark.asyncio
async def test_gateway_cleanup_detects_truncated_stream(monkeypatch):
    from nautionette_backend.clients import agentgateway

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                200, text='data: {"choices":[{"delta":{"content":"Partial"}}]}\n\n'
            )
        )
    ) as http:
        monkeypatch.setattr(agentgateway, "shared", lambda: http)
        with pytest.raises(ValueError, match="before completion"):
            _ = [
                part
                async for part in GatewayClient("http://gateway").dictation_cleanup(
                    "test/model", "edit", "raw"
                )
            ]


def test_busy_transcriptions_reject_without_uploading(client, stt, monkeypatch):
    monkeypatch.setattr(speech, "_slot", SimpleNamespace(locked=lambda: True))
    assert (
        client.post(
            "/api/speech/transcribe", content=b"x", headers={"Content-Type": "audio/webm"}
        ).status_code
        == 429
    )
    assert not stt
