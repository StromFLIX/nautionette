from pathlib import Path

from nautionette_backend.workflow_graph import definition_graph


def test_digest_branches_and_early_return():
    code = (Path(__file__).parents[2] / "workflows/url_digest.py").read_text()
    graph = definition_graph(code, "url_digest")
    nodes = graph["nodes"]
    assert [node["label"] for node in nodes if node["kind"] == "activity"] == [
        "http_fetch",
        "agent_call",
        "save_artifact",
    ]
    condition = next(node for node in nodes if node["kind"] == "condition")
    branches = {edge["label"]: edge["target"] for edge in graph["edges"] if edge["source"] == condition["id"]}
    returned = next(node for node in nodes if node["id"] == branches["Yes"])
    assert returned["kind"] == "return"
    assert not any(edge["source"] == returned["id"] for edge in graph["edges"])
    assert next(node for node in nodes if node["id"] == branches["No"])["label"] == "agent_call"


def test_parallel_activities_join_before_next_step():
    graph = definition_graph(
        """
from temporalio import workflow as wf
import asyncio
@wf.defn(name="demo")
class Demo:
    @wf.run
    async def run(self):
        await asyncio.gather(wf.execute_activity("first"), wf.execute_activity("second"))
        await wf.sleep(30)
""",
        "demo",
    )
    nodes = graph["nodes"]
    fork = next(node for node in nodes if node["kind"] == "parallel")
    join = next(node for node in nodes if node["kind"] == "join")
    assert len([edge for edge in graph["edges"] if edge["source"] == fork["id"]]) == 2
    assert len([edge for edge in graph["edges"] if edge["target"] == join["id"]]) == 2
    assert any(node["kind"] == "timer" for node in nodes)


def test_invalid_or_missing_entry_is_an_explicit_partial_graph():
    assert definition_graph("invalid python!", "demo")["warnings"]
    assert definition_graph("print('not a workflow')", "demo")["warnings"]


def test_source_is_never_executed(tmp_path):
    marker = tmp_path / "executed"
    graph = definition_graph(f"open({str(marker)!r}, 'w').write('oops')", "demo")
    assert not marker.exists()
    assert graph["warnings"]


def test_detail_includes_definition_graph(client, backend):
    backend.authoring.add_workflow("demo")
    response = client.get("/api/workflows/demo")
    assert response.status_code == 200
    assert response.json()["graph"]["mode"] == "definition"
