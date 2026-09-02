"""The event bus and the parsing helpers in the outbound clients."""

from __future__ import annotations

import asyncio
import json

import httpx
import pytest
from nautionette_backend.clients import agentgateway
from nautionette_backend.clients.http import upstream_problem
from nautionette_backend.events import EventBus, sse


def test_an_event_carries_its_kind_and_a_timestamp():
    bus = EventBus()
    event = bus.publish("run.started", {"workflow": "url_digest"})
    assert event["kind"] == "run.started"
    assert event["workflow"] == "url_digest"
    assert event["at"] > 0


def test_the_replay_buffer_has_a_ceiling():
    bus = EventBus(history=3)
    for index in range(10):
        bus.publish("tick", {"index": index})
    assert [event["index"] for event in bus.history()] == [7, 8, 9]


async def test_a_subscriber_sees_what_is_published_after_it_arrives():
    bus = EventBus()
    stream = bus.stream()
    greeting = json.loads((await anext(stream)).removeprefix("data: "))
    assert greeting["kind"] == "connected"

    bus.publish("run.started", {"workflow": "url_digest"})
    frame = await asyncio.wait_for(anext(stream), timeout=1)
    assert json.loads(frame.removeprefix("data: "))["kind"] == "run.started"
    await stream.aclose()


def test_a_frame_is_one_json_object_in_the_sse_shape():
    assert sse({"kind": "x"}) == 'data: {"kind": "x"}\n\n'


# -------------------------------------------------------------------- clients


@pytest.mark.parametrize(
    ("provider", "expected"),
    [
        ("openai", "openai"),
        ({"reference": "nautionette-integration-openai"}, "nautionette-integration-openai"),
        ({"custom": {"formats": []}}, "custom"),
        (None, ""),
        ({}, ""),
    ],
)
def test_a_provider_is_a_name_a_reference_or_a_definition(provider, expected):
    assert agentgateway._provider_name(provider) == expected


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://user:pass@mcp.example.com/mcp", "https://mcp.example.com/mcp"),
        ("https://mcp.example.com/mcp", "https://mcp.example.com/mcp"),
        ("mcp.example.com", "mcp.example.com"),
    ],
)
def test_a_host_is_shown_without_its_credentials(url, expected):
    assert agentgateway._strip_userinfo(url) == expected


def test_an_mcp_answer_is_read_as_json_or_as_one_sse_frame():
    body = {"jsonrpc": "2.0", "id": 1, "result": {"tools": []}}
    plain = httpx.Response(200, json=body, headers={"content-type": "application/json"})
    assert agentgateway._rpc_result(plain) == {"tools": []}

    framed = httpx.Response(
        200,
        text=f"event: message\ndata: {json.dumps(body)}\n\n",
        headers={"content-type": "text/event-stream"},
    )
    assert agentgateway._rpc_result(framed) == {"tools": []}


def test_a_notification_carries_no_id():
    assert "id" not in agentgateway._rpc(None, "notifications/initialized")
    assert agentgateway._rpc(2, "tools/list")["id"] == 2


@pytest.mark.parametrize(
    ("status", "body", "expected"),
    [
        (
            500,
            "token not found",
            "agentgateway found no OpenAI credential. Add $OPENAI_API_KEY and try again.",
        ),
        (400, "unknown copilot-integration-id", "OpenAI rejected the configured integration ID."),
        (401, "", "OpenAI rejected $OPENAI_API_KEY."),
        (400, "invalid api key", "OpenAI rejected $OPENAI_API_KEY."),
        (503, "gateway timeout", "agentgateway could not reach OpenAI (HTTP 503)."),
        (None, "", "agentgateway could not reach OpenAI."),
    ],
)
def test_a_refused_call_becomes_one_actionable_sentence(status, body, expected):
    assert upstream_problem("OpenAI", "$OPENAI_API_KEY", status, body) == expected
