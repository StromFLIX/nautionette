# Workflow controls and result delivery

## Disable and schedule synchronization

**Disable** blocks new manual, agent and webhook starts and pauses the workflow's
managed Temporal schedule. It does **not** cancel executions already started,
reverse external writes, or stop a linked agent task. Inspect/cancel the exact
run when that is required. New schedule creation/replacement is refused while
disabled. Starts, schedule replacement and control changes are serialized by
workflow within the supported single backend process.

The desired disabled flag is saved before contacting Temporal. A successful
settings response confirms schedule synchronization (or that no schedule exists).
If Temporal is unavailable, the endpoint returns **503** with
`schedule_sync_pending: true` and the saved settings. Manual/webhook starts remain
blocked, but Temporal may still fire the old schedule until it can be paused.
The workflow UI shows the error and **Retry sync**, which retries the saved intent
rather than toggling it back. Background reconciliation retries after recovery
and backend restarts, including flags saved by older installations.

Pauses owned by Disable carry a fixed marker in Temporal's schedule note, retaining
the original note. Enable releases only that owned pause. An already operator-paused
schedule stays paused after Disable/Enable. Updates preserve recurrence, timezone,
input, policy and execution timeout; they do not delete/recreate the schedule.
No claim is made that an execution racing an already-issued Temporal start is
cancelled. Do not treat a failed synchronization response as proof that unattended
work has stopped.

## Durable result outbox

Completion and delivery are separate obligations. One SQLite transaction seals
the run's terminal status/result and creates a `run_deliveries` record:

- `pending`: the saved result still needs a chat reply;
- `delivered`: its chat/message and context inbox were committed;
- `skipped`: a successful silent/no-result outcome needs no reply.

A separate background loop retries pending deliveries with persisted, bounded
backoff. Delivery retries do not query Temporal for the saved result and never
rerun a workflow, model or external tool. A transient failure fetching a closed
run's result is retried rather than sealed as an empty successful result. Explicit
termination uses the same durable completion path.

Creating/selecting the destination chat, updating its workflow binding, inserting
the reply, adding its native-context inbox entry and marking delivery complete
are one transaction. Duplicate observers/retries cannot create duplicate chats or
messages. The first sealed result is immutable to later completion observers.
Delivered tombstones survive chat deletion, so late retries do not resurrect a
conversation. Successful `notify: false` runs remain silent; failures are never
suppressed by that field.

This additive migration does **not** enqueue old terminal runs or replay historical
notifications. It protects completions recorded by the upgraded backend. Existing
pending/running work uses normal recovery; no workflow source is rewritten.

## Workflow results in native chat context

A workflow reply is visible immediately in the chat's SQL/UI projection. A durable
`chat_context_messages` inbox also retains its immutable source identity. At the
next standalone chat turn, the backend sends a bounded batch of pending messages
to the runner. **Only the runner**, while holding the existing session lock and
before spawning Pi, appends them using public `SessionManager` custom-message APIs.
The backend never writes into a session owned by a running agent.

Each entry explicitly identifies automation, workflow/run/trigger provenance and
result data—not a new human instruction or a fabricated assistant/tool exchange.
Fresh legacy imports exclude these inbox messages to avoid importing them twice.
The runner acknowledges only after native state is synced; the backend accepts
only IDs supplied to that turn in that chat. Old/custom images without the new
acknowledgement leave the messages pending instead of losing them.

A lost acknowledgement can safely be retried. Deduplication examines raw native
entries, not just the compacted projection, so summarized results are not injected
again. Large results retain their full native evidence and get append-only bounded
context excerpts with `chat_history` references. Batches contain at most 50 messages
and normally 256 KiB of source text (one individually oversized result is admitted
alone); additional messages remain pending. A chat branch copies inbox state and
source identities with its independent native session.

Results arriving while an agent is running wait for a later safe turn boundary.
Queued follow-ups are not text-only steered past pending context. Delivery itself
does not start a model call. Historical SQL-only workflow messages from before
this integration are not blindly reimported into already-compacted sessions.

## Verification

- Backend lifecycle tests inject failures between completion/outbox and
  chat/message/inbox commits; exercise retry, restart, silent outcomes, deletion,
  native acknowledgements, branch identities and pending-context steering fences.
- Real Pi RPC tests verify that the next provider request actually contains the
  delivered result/provenance, and a lost acknowledgement does not duplicate it.
- The opt-in local Temporal test verifies real schedule state updates and that
  action counts remain unchanged while disabled.
- Staging runs `python3 -m scripts.check_workflow_lifecycle`: a UUID-owned, pure
  Python workflow verifies Disable/Enable, rejected starts/schedule replacement,
  preserved inputs/timezone and a completed run's attributed chat delivery. It
  calls no model or external tool, refuses production and removes its own resources.
