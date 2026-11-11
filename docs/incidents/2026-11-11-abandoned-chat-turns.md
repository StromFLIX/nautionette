# Abandoned chat turns — 2026-11-11

## Evidence and limits

Two production turns remained `running` after their owners had exited:

- `13bba40d6c85` / `2c5a89b0-27a4-4afa-80e3-05749a544e19`
  (Remove SigNoz and generalize OpenTelemetry): last persisted output ended
  mid-sentence. A later Stop request set `stop_requested=1` without settling it.
- `6fddeca3708e` / `30ca3b83-7b82-4787-b64b-3da250d8fd82`
  (Mock up compact file outputs): the Git command completed; the final checkpoint
  was the model thinking phase.

Read-only production broker inventory showed no container for either turn. A
read-only snapshot of the backend's live task registry, followed by its Python
Task objects, showed neither owner nor controller. Other chats were working;
the broker container had not restarted. Running normal interrupted-turn
recovery against an **in-memory copy** of the production database succeeded.

This rules out a currently blocked shell command, a still-running model request,
and a database checkpoint that cannot be finalized. It does **not** establish
which exception/cancellation first ended the owners: neither terminal event was
retained. Do not attribute this incident to Copilot from the last UI phase alone.

## Confirmed failure mechanism

`run_turn` had no outer supervisor. Its `finally` block performed several
fallible operations before `finish_chat_turn`: clock/tool settlement, task joins,
Docker cleanup, credential revocation and database persistence. An exception or
second cancellation there could end the owner while leaving the durable turn
`running`. The background-task callback removed the owner from the registry but
only logged its error. Production's application logger had only the optional
telemetry handler, so Docker logs did not retain the missing failure detail.

Stop only set a database flag. It relied on the owner's controller to consume
that flag, which cannot work after the owner has disappeared.

## Repair and guarantees

- Supervise the complete turn, including its finalizer. Unexpected exits trigger
  independently owned recovery, shielded from cancellation of the failed owner.
- Persist an error event **before** recovery awaits cleanup. Retain the exception
  type and bounded file/function/line frames, never exception messages, source
  text, locals, credentials or upstream request/response payloads.
- Pause the queue, confirm exact-turn Docker cleanup, preserve the last durable
  transcript/tools/timing checkpoint, and settle the turn as unsuccessful.
  Cleanup failure remains explicit and blocks queue advancement; new work still
  goes through existing exact-agent reconciliation.
- Session inspection exposes `runtime_owner` (true/false for running turns,
  null otherwise), so future incidents can distinguish orphaned SQL state from
  a live owner without attaching a debugger to the backend.
- Track live turn ownership. Stop settles an ownerless turn instead of waiting
  for a nonexistent controller. A live but silent owner is not classified as
  abandoned; there is no new age/idle timeout or automatic tool replay.
- Regression tests inject failures during clock settlement, revocation and
  terminal persistence; cover cleanup failure, second cancellation, idempotency,
  Stop without an owner, and Stop with a live silent owner.

Both affected production turns were explicitly stopped and finalized from their
saved checkpoints. Their queues remain paused and their tool/transcript steps
were retained. No tools were replayed and no project worktrees were reset.
Temporary diagnostic tasks and files were removed after verification.

The original triggering exception is not recoverable from the retained evidence.
The repair closes the reproducible lifecycle gap and makes any future occurrence
diagnosable from durable session events rather than optional telemetry alone.
