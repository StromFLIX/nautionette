"""The latency probe must not count setup events or failed replies as success."""

import json
from dataclasses import dataclass

import httpx
import pytest

from scripts import benchmark_chat_ttft
from scripts.benchmark_chat_ttft import main, measure_turn, sse_events, summarize


@dataclass
class Clock:
    value: float = 0

    def __call__(self):
        return self.value


class TimedStream(httpx.SyncByteStream):
    def __init__(self, clock, events):
        self.clock = clock
        self.events = events

    def __iter__(self):
        for at, event in self.events:
            self.clock.value = at
            yield f"data: {json.dumps(event)}\n\n".encode()


def run(events, *, status=200, content_type="text/event-stream"):
    clock = Clock()
    requests = []

    def handler(request):
        requests.append(request)
        if request.url.path.endswith("/stop"):
            return httpx.Response(200, json={})
        return httpx.Response(
            status, headers={"Content-Type": content_type}, stream=TimedStream(clock, events)
        )

    with httpx.Client(base_url="https://app.test", transport=httpx.MockTransport(handler)) as client:
        result = measure_turn(client, "our-chat", ["our-project"], clock=clock)
    return result, requests


def test_counts_only_the_first_nonempty_text_delta_and_waits_for_completion():
    result, requests = run(
        [
            (0.1, {"type": "started"}),
            (0.2, {"type": "phase", "phase": "model"}),
            (0.3, {"type": "thinking", "text": "private reasoning"}),
            (0.4, {"type": "tool", "args": {"private": "data"}}),
            (0.5, {"type": "delta", "text": ""}),
            (0.6, {"type": "delta", "text": "READY"}),
            (0.7, {"type": "delta", "text": "ignored"}),
            (0.8, {"type": "result", "ok": True}),
            (0.9, {"type": "done", "message": {"meta": {}}}),
        ]
    )
    assert result["ok"]
    assert result["first_event_ms"] == 100
    assert result["first_reasoning_ms"] == 300
    assert result["ttft_text_ms"] == 600
    assert result["total_ms"] == 900
    assert result["milestones_ms"]["phase_model"] == 200
    assert result["tool_calls"] == 1
    assert "private" not in json.dumps(result) and "READY" not in json.dumps(result)
    assert len(requests) == 1
    assert requests[0].headers["Accept"] == "text/event-stream"
    payload = json.loads(requests[0].content)
    assert payload["project_ids"] == ["our-project"]
    assert payload["message_id"] == result["turn_id"]


@pytest.mark.parametrize(
    ("events", "failure", "stop"),
    [
        ([(1, {"type": "started"})], "stream_ended_before_done", True),
        (
            [(1, {"type": "done", "message": {"content": "Not a streamed token"}})],
            "missing_successful_result",
            False,
        ),
        ([(1, {"type": "result", "ok": True}), (2, {"type": "done"})], "no_text_delta", False),
        (
            [
                (1, {"type": "delta", "text": "partial"}),
                (2, {"type": "result", "ok": False}),
                (3, {"type": "done"}),
            ],
            "agent_result_failed",
            False,
        ),
        (
            [(1, {"type": "error", "message": "private upstream payload"}), (2, {"type": "done"})],
            "agent_error",
            False,
        ),
        (
            [(1, {"type": "done", "message": {"meta": {"error": "failed"}}})],
            "turn_failed_or_interrupted",
            False,
        ),
    ],
)
def test_failures_are_not_successful_measurements(events, failure, stop):
    result, requests = run(events)
    assert not result["ok"]
    assert result["failure"] == failure
    assert (len(requests) == 2) is stop
    if stop:
        assert requests[-1].url.path == "/api/chats/our-chat/stop"
        assert json.loads(requests[-1].content) == {"turn_id": result["turn_id"]}


def test_http_and_non_streaming_failures_are_reported_without_response_bodies():
    for status, content_type, failure in [
        (401, "application/json", "http_401"),
        (200, "application/json", "invalid_stream"),
    ]:
        result, _ = run([], status=status, content_type=content_type)
        assert not result["ok"]
        assert result["failure"] == failure
        assert result["ttft_text_ms"] is None


def test_parses_multiline_sse_comments_and_unterminated_final_frame():
    response = httpx.Response(
        200, text=': heartbeat\n\nevent: update\ndata: {"type":\ndata: "started"}\n\ndata: {"type":"done"}'
    )
    assert list(sse_events(response)) == [{"type": "started"}, {"type": "done"}]


def test_first_useful_action_excludes_search_and_failed_results():
    result, _ = run(
        [
            (0.1, {"type": "tool", "name": "tool_search"}),
            (0.2, {"type": "tool_done", "name": "tool_search", "error": False}),
            (0.3, {"type": "tool", "name": "backend_system_status"}),
            (0.4, {"type": "tool_done", "name": "backend_system_status", "error": True}),
            (0.5, {"type": "tool_done", "name": "backend_system_status", "error": False}),
            (0.6, {"type": "usage", "context": {"input_tokens": 4000, "cache_read_tokens": 3900}}),
            (0.7, {"type": "delta", "text": "READY"}),
            (0.8, {"type": "result", "ok": True}),
            (0.9, {"type": "done"}),
        ]
    )
    assert result["ok"]
    assert result["first_tool_ms"] == 100
    assert result["first_action_ms"] == 300
    assert result["first_action_result_ms"] == 500
    assert result["tool_names"] == ["tool_search", "backend_system_status"]
    assert result["context"]["input_tokens"] == 4000


def test_summary_excludes_failed_samples_even_after_they_stream_text():
    samples = [
        {"ok": True, "ttft_text_ms": 300},
        {"ok": False, "ttft_text_ms": 100},
        {"ok": True, "ttft_text_ms": 500},
    ]
    assert summarize(samples) == {
        "successful": 2,
        "failed": 1,
        "ttft_text_ms": {"median": 400, "min": 300, "max": 500},
    }
    assert summarize([])["ttft_text_ms"] is None


def test_disconnect_stops_exact_turn_and_records_unconfirmed_cleanup():
    requests = []

    def handler(request):
        requests.append(request)
        raise httpx.ReadTimeout("must not leak upstream details", request=request)

    with httpx.Client(base_url="https://app.test", transport=httpx.MockTransport(handler)) as client:
        result = measure_turn(client, "our-chat", [], clock=Clock())
    assert result["failure"] == "transport_error"
    assert result["cleanup"] == "stop_unconfirmed_inspect_chat"
    assert len(requests) == 2
    assert json.loads(requests[1].content) == {"turn_id": result["turn_id"]}
    assert "upstream" not in json.dumps(result)


def test_cli_keeps_project_selection_and_does_not_continue_after_failure(monkeypatch, capsys):
    requests = []
    real_client = httpx.Client

    def handler(request):
        requests.append(request)
        if request.url.path == "/healthz":
            return httpx.Response(200, json={"version": "test-build", "environment": "staging"})
        if request.url.path == "/api/chats":
            return httpx.Response(200, json={"id": "new-chat", "model": "test-model"})
        events = [{"type": "result", "ok": False}, {"type": "done"}]
        return httpx.Response(
            200,
            headers={"Content-Type": "text/event-stream"},
            content="".join(f"data: {json.dumps(event)}\n\n" for event in events),
        )

    monkeypatch.setenv("APP_TOKEN", "test-auth-only")
    monkeypatch.setattr(
        benchmark_chat_ttft.httpx,
        "Client",
        lambda **kwargs: real_client(transport=httpx.MockTransport(handler), **kwargs),
    )
    assert main(["--base", "https://app.test", "--project-id", "project", "--samples", "5"]) == 1
    report = json.loads(capsys.readouterr().out)
    assert len(report["samples"]) == 1
    assert report["samples"][0]["turn"] == "first"
    assert report["app"] == {"version": "test-build", "build_commit": None, "environment": "staging"}
    assert json.loads(requests[1].content)["project_ids"] == ["project"]
    assert json.loads(requests[2].content)["project_ids"] == ["project"]
    assert len(requests) == 3
    assert "test-auth-only" not in json.dumps(report)


def test_cli_requires_environment_auth_without_starting_a_request(monkeypatch):
    monkeypatch.delenv("APP_TOKEN", raising=False)
    with pytest.raises(SystemExit) as exc:
        main(["--base", "https://app.test"])
    assert exc.value.code == 2
