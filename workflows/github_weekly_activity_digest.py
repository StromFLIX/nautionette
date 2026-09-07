"""Every Monday at 9:00, summarize the user's GitHub activity over the past week.

Uses an agent step (agent_call) because this requires exploratory searching
across commits, issues, pull requests, and repos via the GitHub MCP tools -
not a single deterministic API call. The agent is instructed to search
broadly and produce a structured markdown summary, which is then saved as an
artifact and returned as `summary`.
"""

import json
from datetime import timedelta

from temporalio import workflow
from temporalio.common import RetryPolicy
from temporalio.exceptions import ApplicationError

MANIFEST = {
    "schema": 1,
    "name": "github_weekly_activity_digest",
    "title": "Weekly GitHub Activity Digest",
    "description": (
        "Search commits, issues, pull requests, and repos to summarize "
        "what the user did on GitHub in the last 7 days."
    ),
    "inputs": {
        "type": "object",
        "properties": {
            "github_username": {
                "type": "string",
                "description": "GitHub username to report on. Defaults to the authenticated user.",
            },
            "days": {
                "type": "integer",
                "description": "How many days back to look (default 7).",
            },
        },
        "required": [],
    },
    "outputs": {
        "type": "object",
        "properties": {
            "summary": {"type": "string"},
        },
        "required": ["summary"],
    },
    "agent_set": "default",
    "source": "chat",
    "timeout_minutes": 30,
    "tags": ["github", "digest"],
    "x_schedule": {"cron": "0 9 * * 1"},
}


def digest_summary(answer: dict) -> str:
    """Ignore stale runtime extraction; accept only the final JSON answer.

    This also protects existing agent images with the first-code-fence parser.
    A malformed final answer must not fall back to a valid earlier placeholder.
    """
    if not answer.get("ok"):
        raise ApplicationError("GitHub digest agent failed", non_retryable=True)
    text = (answer.get("text") or "").strip()
    output = answer.get("output")
    if text:
        if text.endswith("```"):
            text = text[:-3].rstrip()
        output = None
        for index, character in enumerate(text):
            if character != "{":
                continue
            try:
                output = json.loads(text[index:])
                break
            except ValueError:
                continue
    summary = output.get("summary") if isinstance(output, dict) else None
    if not isinstance(summary, str) or len(summary.strip()) < 80:
        raise ApplicationError(
            "GitHub digest has no substantive final summary; refusing to publish a placeholder",
            non_retryable=True,
        )
    return summary.strip()


@workflow.defn(name="github_weekly_activity_digest")
class GithubWeeklyActivityDigest:
    @workflow.run
    async def run(self, params: dict) -> dict:
        days = params.get("days") or 7
        username = params.get("github_username")

        who = (
            f"GitHub user '{username}'"
            if username
            else "the authenticated GitHub user (use get_me if needed)"
        )

        end = workflow.now()
        start = end - timedelta(days=days)
        prompt = (
            f"Report on what {who} did on GitHub in the last {days} days.\n"
            f"Exact UTC reporting window: {start.isoformat()} to {end.isoformat()}.\n\n"
            "Search thoroughly across:\n"
            "- Commits (across all repos they pushed to)\n"
            "- Issues (opened, closed, commented)\n"
            "- Pull requests (opened, reviewed, merged)\n"
            "- Repos (any created or notably active)\n\n"
            "Use the GitHub tools available to you (search_commits, search_issues, "
            "search_pull_requests, list_commits, list_issues, list_pull_requests, "
            "search_repositories, get_me, etc.) to gather this. Group findings by "
            "repository, and within each repo summarize commits/issues/PRs concisely "
            "with dates. End with a short overall summary paragraph.\n\n"
            "Use read-only GitHub tools only. Include source links and only activity within "
            "the reporting window. Repository updates alone are not proof of this user's activity. "
            "Disclose search/API coverage limits; never invent examples or imply exhaustive coverage. "
            "If access fails, report the failure rather than claiming there was no activity. "
            "Return exactly one final JSON object with a summary field containing the complete "
            "Markdown digest. Do not emit draft JSON, examples, or placeholders. If no activity "
            "was found, explain the window and searches performed in the summary."
        )

        # Deliberately omit output_schema: older agent images parse the first
        # code fence or fail before we can inspect text. Validate locally instead.
        answer = await workflow.execute_activity(
            "agent_call",
            {"prompt": prompt},
            start_to_close_timeout=timedelta(minutes=25),
            retry_policy=RetryPolicy(maximum_attempts=2),
        )
        summary = digest_summary(answer)

        await workflow.execute_activity(
            "save_artifact",
            {
                "name": f"github-weekly-digest-{workflow.info().workflow_id}.md",
                "content": f"# Weekly GitHub Activity Digest\n\n{summary}\n",
            },
            start_to_close_timeout=timedelta(minutes=1),
        )

        return {"summary": summary}
