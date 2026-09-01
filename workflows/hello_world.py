"""A first workflow, seeded into the volume so the system is never empty.

Read it, run it, change it. It is a normal Python file.
"""

from datetime import timedelta

from temporalio import workflow

MANIFEST = {
    "schema": 1,
    "name": "hello_world",
    "title": "Hello world",
    "description": "Asks the agent for one sentence about a topic and returns it.",
    "inputs": {
        "type": "object",
        "properties": {"topic": {"type": "string", "description": "What to say something about"}},
        "required": [],
    },
    "outputs": {
        "type": "object",
        "properties": {"message": {"type": "string"}},
        "required": ["message"],
    },
    "agent_set": "default",
    "timeout_minutes": 10,
    "source": "seed",
}


@workflow.defn(name="hello_world")
class HelloWorld:
    @workflow.run
    async def run(self, params: dict) -> dict:
        topic = params.get("topic") or "what a durable workflow is"

        answer = await workflow.execute_activity(
            "agent_call",
            {
                "prompt": f"Say one clear sentence about {topic}.",
                "output_schema": {
                    "type": "object",
                    "properties": {"message": {"type": "string"}},
                    "required": ["message"],
                },
            },
            start_to_close_timeout=timedelta(minutes=5),
        )
        message = (answer.get("output") or {}).get("message") or answer.get("text", "")

        await workflow.execute_activity(
            "emit_event",
            {"kind": "workflow.hello", "payload": {"topic": topic, "message": message[:200]}},
            start_to_close_timeout=timedelta(minutes=1),
        )
        return {"message": message}
