"""A workflow that fetches something and summarises it.

The shape a promoted chat usually ends up with: fixed values from the chat
became inputs, and the agent step declares the object it must return.
"""

from datetime import timedelta

from temporalio import workflow

MANIFEST = {
    "schema": 1,
    "name": "url_digest",
    "title": "URL digest",
    "description": "Fetches a URL and returns a short summary with the key points.",
    "inputs": {
        "type": "object",
        "properties": {
            "url": {"type": "string", "description": "The page to read"},
            "focus": {"type": "string", "description": "What to pay attention to"},
        },
        "required": ["url"],
    },
    "outputs": {
        "type": "object",
        "properties": {
            "summary": {"type": "string"},
            "key_points": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["summary"],
    },
    "agent_set": "default",
    "timeout_minutes": 20,
    "tags": ["example", "http"],
    "source": "seed",
}


@workflow.defn(name="url_digest")
class UrlDigest:
    @workflow.run
    async def run(self, params: dict) -> dict:
        url = params.get("url") or "https://pi.dev"
        focus = params.get("focus") or "what it is and who it is for"

        page = await workflow.execute_activity(
            "http_fetch",
            {"url": url},
            start_to_close_timeout=timedelta(minutes=2),
        )
        if page["status"] >= 400:
            return {"summary": f"{url} returned HTTP {page['status']}", "key_points": []}

        digest = await workflow.execute_activity(
            "agent_call",
            {
                "prompt": (f"Summarise this page. Focus on {focus}.\n\nURL: {url}\n\n{page['body'][:20000]}"),
                "output_schema": {
                    "type": "object",
                    "properties": {
                        "summary": {"type": "string"},
                        "key_points": {"type": "array", "items": {"type": "string"}},
                    },
                    "required": ["summary"],
                },
            },
            start_to_close_timeout=timedelta(minutes=10),
        )
        output = digest.get("output") or {"summary": digest.get("text", ""), "key_points": []}

        await workflow.execute_activity(
            "save_artifact",
            {
                "name": f"url-digest-{workflow.info().workflow_id}.md",
                "content": f"# {url}\n\n{output.get('summary', '')}\n",
            },
            start_to_close_timeout=timedelta(minutes=1),
        )
        return output
