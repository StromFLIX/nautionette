# Saved agents in workflows

The `agent_call` activity accepts an optional saved `agent_id`. Without it, the
existing generic activity agent still uses instance defaults. With it, execution
uses that saved profile's model, reasoning effort, persona, tools, packages and
project permissions through the normal durable chat pipeline. No particular
agent, repository, schedule or maintenance policy is hardcoded in the platform.

```python
answer = await workflow.execute_activity(
    "agent_call",
    {
        "agent_id": params["agent_id"],
        "prompt": params["task"],
        "timeout_seconds": 3600,
    },
    start_to_close_timeout=timedelta(minutes=65),
    heartbeat_timeout=timedelta(minutes=3),
    retry_policy=RetryPolicy(maximum_attempts=3),
)
# answer includes ok, text, chat_id and turn_id; failures raise.
```

Keep the activity timeout longer than `timeout_seconds`, and the workflow's
manifest timeout longer than the whole workflow. Use a heartbeat timeout of at
least three minutes: the worker polls every ten seconds with bounded requests.
The profile task uses timeout-exempt chat semantics while the activity enforces
its deadline and requests a normal chat stop on timeout.

`validate_only: True` resolves and validates the profile and project selections
without creating a chat or calling a model. It returns `validated`, `agent_id`,
`agent_name`, `model` and `project_ids`. An invalid/deleted profile fails closed;
it never falls back to a different agent. Profile configuration is snapshotted
when a task starts; subsequent profile edits apply to later tasks only.

Project agents receive the same isolated per-chat worktrees and rotating Git
credentials as interactive conversations. Credentials are not put into workflow
source, inputs or history. Normal Git attribution, hooks, deployment draining,
turn persistence and tool restrictions remain in effect. The linked chat is
visible in the app and contains the full progress and diagnostic history.

## Caller attribution

Workflow calls and results use the same server-owned `origin` metadata as agent
delegation. A call says **Called by workflow** and links to both the workflow and
the exact run; result delivery says **Result from**. Scheduled, human/manual,
agent-started and webhook runs remain distinct. The trigger comes from the
recorded run or Temporal, not from workflow inputs or model output. An unknown
trigger is never guessed to be scheduled.

The worker derives workflow/run/activity identity from Temporal's activity
context, and the backend verifies it. New saved-profile tasks without that
identity are rejected. Normal public chat payloads cannot set attribution.
The task message, live response, persisted answer and recovery checkpoints all
retain the same caller metadata. A further subagent call shows its immediate
calling agent plus the original workflow/run, without changing subagent limits
or tying the workflow's lifetime to a parent chat turn. The called model also
receives an explicit automation-origin prefix, not an apparent human request.
Legacy workflow result and agent-sender metadata remains readable by the shared
UI; attribution is not inferred from arbitrary chat titles or prompt wording.

## Retries and cancellation

A task identity is derived from the Temporal workflow run ID and activity ID,
not the retry attempt. A restarted worker reconnects to the same durable turn
instead of repeating side effects. Different scheduled runs get different task
identities. Failed/completed turns are not automatically restarted by retries.
Small identity tombstones (IDs and a prompt hash, not prompt content) remain after
chat deletion, so deleting a chat cannot cause a retry to repeat its side effects.

Explicit activity cancellation and the polling deadline request a normal chat
stop. Worker shutdown alone does not stop the task: the replacement reconnects.
Abrupt Temporal termination cannot deliver cancellation, so operators must
inspect and stop the linked chat too. A lost request or terminated workflow is
not proof that an external agent stopped. Do not start a replacement mutating
task until the earlier chat's state is understood.

## Structured output

Pass `output_schema` and instruct the selected chat agent to return **only** the
matching JSON object. The worker parses and validates the final answer and adds
`output` to the result. Prose, schema mismatches, failed/interrupted turns and
missing final results fail the activity, rather than looking like success.
Validation failure does not rerun an already completed agent task.

Internal task start/status/stop endpoints use the existing service-token
authentication; they are not public chat-creation or MCP endpoints.
