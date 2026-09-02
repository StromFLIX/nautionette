"""The activities every workflow can call by name."""

from __future__ import annotations

import json

import pytest
from nautionette_worker import activities
from temporalio.testing import ActivityEnvironment

from ..conftest import json_body, text_body

BACKEND = "http://backend:8080"


@pytest.fixture
def artifacts(tmp_path, monkeypatch):
    monkeypatch.setattr(activities, "ARTIFACTS_DIR", tmp_path / "artifacts")
    return tmp_path / "artifacts"


@pytest.fixture
def env() -> ActivityEnvironment:
    return ActivityEnvironment()


# ----------------------------------------------------------------- http_fetch


async def test_a_page_comes_back_as_status_body_and_json(http):
    http["https://example.com"] = json_body({"tag_name": "v1"})
    result = await activities.http_fetch({"url": "https://example.com/releases"})
    assert result["status"] == 200
    assert result["json"] == {"tag_name": "v1"}
    assert json.loads(result["body"]) == {"tag_name": "v1"}


async def test_a_page_that_is_not_json_still_has_a_body(http):
    http["https://example.com"] = text_body("<html>hi</html>")
    result = await activities.http_fetch({"url": "https://example.com"})
    assert result["json"] is None
    assert result["body"] == "<html>hi</html>"


async def test_an_enormous_page_is_truncated(http, monkeypatch):
    monkeypatch.setattr(activities, "HTTP_MAX_BYTES", 10)
    http["https://example.com"] = text_body("x" * 500)
    assert len((await activities.http_fetch({"url": "https://example.com"}))["body"]) == 10


@pytest.mark.parametrize("url", [None, "", "file:///etc/passwd", "ftp://example.com"])
async def test_only_an_http_url_may_be_fetched(url):
    with pytest.raises(ValueError, match="http_fetch needs an http"):
        await activities.http_fetch({"url": url})


# ------------------------------------------------------------------ artifacts


async def test_an_artifact_round_trips(artifacts):
    saved = await activities.save_artifact({"name": "digest.md", "content": "# Digest"})
    assert saved["name"] == "digest.md"
    assert saved["bytes"] == len("# Digest")
    assert (await activities.read_artifact({"name": "digest.md"}))["content"] == "# Digest"


async def test_an_artifact_name_can_never_leave_its_directory(artifacts):
    saved = await activities.save_artifact({"name": "../../etc/passwd", "content": "x"})
    assert saved["path"] == str(artifacts / "passwd")


async def test_an_artifact_that_was_never_written_says_so(artifacts):
    artifacts.mkdir(parents=True)
    with pytest.raises(FileNotFoundError, match="does not exist"):
        await activities.read_artifact({"name": "nothing.md"})


# ----------------------------------------------------------------- emit_event


async def test_progress_reaches_the_backend_with_the_run_it_came_from(env, http):
    seen = {}

    def record(request):
        seen.update(json.loads(request.content))
        return json_body({"ok": True})(request)

    http[BACKEND] = record
    result = await env.run(activities.emit_event, {"kind": "workflow.note", "payload": {"n": 1}})
    assert result == {"ok": True}
    assert seen["kind"] == "workflow.note"
    assert seen["payload"]["n"] == 1
    assert "workflow_id" in seen["payload"]


async def test_a_notice_that_does_not_land_never_fails_the_run(env, http):
    result = await env.run(activities.emit_event, {"kind": "workflow.note"})
    assert result["ok"] is False
    assert result["error"]


# ----------------------------------------------------------------- agent_call


async def test_an_agent_step_returns_the_object_the_backend_built(env, http):
    http[BACKEND] = json_body({"ok": True, "text": "done", "output": {"summary": "s"}})
    result = await env.run(activities.agent_call, {"prompt": "summarise"})
    assert result["output"] == {"summary": "s"}


async def test_a_failed_agent_step_fails_the_activity_so_temporal_retries(env, http):
    http[BACKEND] = json_body({"ok": False, "error": "no model configured"})
    with pytest.raises(RuntimeError, match="no model configured"):
        await env.run(activities.agent_call, {"prompt": "summarise"})


async def test_prose_where_an_object_was_declared_is_a_failure(env, http):
    http[BACKEND] = json_body({"ok": True, "text": "some prose", "output": None})
    with pytest.raises(RuntimeError, match="structured object was declared"):
        await env.run(
            activities.agent_call, {"prompt": "summarise", "output_schema": {"type": "object"}}
        )


# ------------------------------------------------------------------- mcp_call


def mcp_frame(result: dict) -> dict:
    return {"jsonrpc": "2.0", "id": 2, "result": result}


def mcp_route(result: dict, *, sse: bool = False):
    def handler(request):
        body = json.loads(request.content)
        if body.get("method") != "tools/call":
            return json_body({"jsonrpc": "2.0", "id": body.get("id"), "result": {}})(request)
        if sse:
            return text_body(
                f"data: {json.dumps(mcp_frame(result))}\n\n", **{"content-type": "text/event-stream"}
            )(request)
        return json_body(mcp_frame(result))(request)

    return handler


async def test_a_tool_answer_is_returned_as_text_and_as_json(http):
    http["http://agentgateway:4000"] = mcp_route({"content": [{"type": "text", "text": '{"a": 1}'}]})
    assert await activities.mcp_call({"tool": "search"}) == {"ok": True, "text": '{"a": 1}', "json": {"a": 1}}


async def test_a_one_frame_sse_answer_reads_the_same(http):
    http["http://agentgateway:4000"] = mcp_route(
        {"content": [{"type": "text", "text": "plain"}]}, sse=True
    )
    assert await activities.mcp_call({"tool": "search"}) == {"ok": True, "text": "plain", "json": None}


async def test_a_tool_that_reports_an_error_fails_the_activity(http):
    http["http://agentgateway:4000"] = mcp_route(
        {"isError": True, "content": [{"type": "text", "text": "no such issue"}]}
    )
    with pytest.raises(RuntimeError, match="no such issue"):
        await activities.mcp_call({"tool": "search"})


async def test_a_protocol_error_fails_the_activity(http):
    def handler(request):
        body = json.loads(request.content)
        if body.get("method") != "tools/call":
            return json_body({"jsonrpc": "2.0", "id": body.get("id"), "result": {}})(request)
        return json_body({"jsonrpc": "2.0", "id": 2, "error": {"message": "unknown tool"}})(request)

    http["http://agentgateway:4000"] = handler
    with pytest.raises(RuntimeError, match="unknown tool"):
        await activities.mcp_call({"tool": "nope"})


async def test_a_call_without_a_tool_name_goes_nowhere():
    with pytest.raises(ValueError, match="needs a tool name"):
        await activities.mcp_call({})


def test_every_activity_a_workflow_may_name_is_registered():
    assert {getattr(fn, "__temporal_activity_definition").name for fn in activities.ALL} == {
        "agent_call",
        "http_fetch",
        "mcp_call",
        "emit_event",
        "save_artifact",
        "read_artifact",
    }
