import ast

import pytest
from nautionette_backend.activity_details import activity_metadata
from nautionette_backend.execution_graph import execution_graph
from nautionette_backend.workflow_graph import definition_graph


def fields(metadata):
    return {item["label"]: item for item in metadata["details"]}


@pytest.mark.parametrize(
    "name,payload,label,value",
    [
        ("agent_call", {"agent_set": "research", "prompt": "Summarise this"}, "Agent set", "research"),
        ("mcp_call", {"tool": "github_search_issues", "arguments": {"query": "bug"}}, "Server", "github"),
        ("http_fetch", {"url": "https://example.com"}, "Method", "GET"),
        ("emit_event", {"kind": "digest.ready", "payload": {}}, "Event", "digest.ready"),
        ("save_artifact", {"name": "digest.md", "content": "Summary"}, "File", "digest.md"),
        ("read_artifact", {"name": "digest.md"}, "File", "digest.md"),
    ],
)
def test_all_builtins_describe_literal_source_and_runtime_values(name, payload, label, value):
    runtime = activity_metadata(name, payload)
    source = activity_metadata(name, ast.parse(repr(payload), mode="eval").body)
    assert source == runtime
    assert fields(source)[label] == {"label": label, "value": value, "dynamic": False}


def test_dynamic_inputs_are_explicit_and_never_evaluated(tmp_path):
    marker = tmp_path / "must-not-exist"
    payload = ast.parse(
        f'{{"agent_set": params["agent"], "prompt": open({str(marker)!r}, "w")}}', mode="eval"
    ).body
    data = fields(activity_metadata("agent_call", payload))
    assert data["Agent set"]["dynamic"] is True
    assert data["Prompt"]["dynamic"] is True
    assert not marker.exists()
    data = fields(activity_metadata("agent_call", ast.parse("options", mode="eval").body))
    assert data["Agent set"]["value"] == "From activity input"
    assert data["Input expression"]["value"] == "options"


@pytest.mark.parametrize("agent_set", [None, ""])
def test_empty_agent_set_uses_runtime_default_in_source_and_history(agent_set):
    payload = {"agent_set": agent_set}
    runtime = activity_metadata("agent_call", payload)
    source = activity_metadata("agent_call", ast.parse(repr(payload), mode="eval").body)
    assert source == runtime
    assert runtime["title"] == "Agent: Runtime default"


def test_agent_tools_are_observed_not_guessed_and_custom_activities_stay_generic():
    assert fields(activity_metadata("agent_call", {}))["Tools"]["value"] == "Selected by the agent at runtime"
    observed = fields(activity_metadata("agent_call", {}, result={"tools": ["github_search_issues"]}))
    assert observed["Tools used"]["value"] == "github_search_issues"
    assert activity_metadata("custom", {"agent_set": "research"}) == {}


def test_definition_accepts_keyword_arguments_and_temporal_options():
    graph = definition_graph(
        """
from temporalio import workflow
@workflow.defn
class Demo:
    @workflow.run
    async def run(self, params):
        await workflow.execute_activity(activity="mcp_call", args=[{
            "tool": "github_search_issues", "arguments": {"query": params["query"]}
        }], start_to_close_timeout=timedelta(minutes=2))
""",
        "Demo",
    )
    node = graph["nodes"][1]
    assert node["title"] == "github_search_issues"
    data = fields(node)
    assert data["Arguments"]["dynamic"] is True
    assert data["Execution timeout"]["value"] == "timedelta(minutes=2)"


def test_execution_enriches_the_correlated_result_and_keeps_raw_input():
    graph = execution_graph(
        {"workflow_id": "demo", "status": "COMPLETED"},
        [
            {
                "id": 5,
                "event": "activity.scheduled",
                "at": "2026-09-07",
                "activity": "agent_call",
                "input": [{"agent_set": "research", "prompt": "Summarise"}],
            },
            {
                "id": 7,
                "event": "activity.completed",
                "at": "2026-09-07",
                "scheduled_event_id": 5,
                "result": {"tools": ["github_search_issues"], "output": {"summary": "Done"}},
            },
        ],
    )
    node = graph["nodes"][1]
    assert fields(node)["Tools used"]["value"] == "github_search_issues"
    assert node["input"][0]["agent_set"] == "research"
    assert node["status"] == "completed"
