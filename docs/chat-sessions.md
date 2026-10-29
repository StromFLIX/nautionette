# Durable native Pi chat sessions

Every interactive chat resumes **one exact native Pi JSONL session**. No feature
flag or new-chat-only behavior: existing chats import their stored history once,
then use the same path as new chats. Workflow agent calls remain one-shot.

## Ownership and context

- Nautionette owns chat IDs, accepted inputs, queueing, permissions, credentials,
  scheduling and the SQL/UI projection.
- Pi owns structured user/assistant/tool messages, tool-call relationships, native
  compaction boundaries, extension session entries and model/context projection.
- `chat-sessions/<chat-id>/session.jsonl` is the canonical agent transcript. The
  chat's `pi_session_id` detects missing/replaced state; it is not guessed from
  the newest session in a directory.
- `/workspace`, `/tmp`, processes, sockets and Pi home/auth remain ephemeral.
  Current settings, authorized tools, instructions and fresh project credentials
  are applied on every container start. Old tool declarations do not grant access.

Pi uses its model-aware context and automatic compaction, not Nautionette's old
200-message/character trimming. Compaction retains a summary and recent entries;
raw older history stays in the session. `/compact [instructions]` invokes native
manual compaction, and the UI records compaction start/completion/failure. Native
session-replacing extension commands are cancelled rather than silently switching
the chat to an ephemeral file; start a new Nautionette chat instead.

The local read-only `chat_history` tool retrieves bounded pre-compaction evidence,
with optional case-insensitive search and native entry-ID pagination. It returns
all branches as evidence, not as instructions or commands to execute. The
user-authenticated `GET /api/chats/{id}/session/entries?after=<byte-offset>&limit=20`
provides forward inspection (at most 100 records, 512 KB; large records become
bounded previews). `next` is a validated entry-boundary byte cursor. Normal chat
snapshots still use SQL and are not inflated with full tool results or images.

The native transcript retains the result Pi actually received, before the UI's
4,000-character preview cap. It cannot recover data a tool already truncated;
files in ephemeral `/tmp` mentioned by a tool will still disappear. Publish useful
artifacts/exports to Outputs rather than assuming all tool-created files persist.

## Input safety and crash recovery

The broker holds a nonblocking OS writer lock outside the sandbox subdirectory
for the full run, and refuses to start while an older chat container exists.
Deployment-scoped inventory/cleanup removes survivors before the next run. An
unreleased lock or failed Docker cleanup fails closed; no second writer starts.

`inputs.jsonl` journals stable accepted input IDs **before** dispatching to Pi,
including live steering. Native custom entries bind consumed input IDs to Pi user
entries. Session state is synced before external tools, subsequent model requests,
turn boundaries and final exit. The journal and session are updated throughout a
run, not saved only after the final answer.

A submitted/consumed ID is never blindly resubmitted. Orderly settlement may
release steering inputs that were not consumed, allowing their queued turns to
start normally. Crashes leave uncertain input IDs fenced; the existing queue
recovery pauses that chat and tells the user to verify outcomes. If a consumed
steering acknowledgement was lost, trying that queued ID fails explicitly instead
of executing it twice. Inspect the history, discard the already-processed queued
input and send a new instruction. This intentionally prefers safety over guessing
how an uncertain tool affected an external system. It is not exactly-once execution
or automatic reattachment to a crashed Pi process.

A torn final JSONL record is saved separately and removed before reopening; malformed
complete records and unsupported future session versions fail without substituting
SQL text. Restore a backup to repair missing native state. Do not remove
`pi_session_id` to force a lossy reimport.

## Attachments and legacy migration

Original uploaded bytes persist at `/chat-session/attachments/<id>.<extension>`.
Vision blocks also stay in native messages. Switching to a text-only route removes
historical vision blocks from that request's projection, not from disk or native
history; original files remain available to tools. Reused attachment IDs must have
identical bytes. Nothing auto-extracts, converts or executes uploads.

Legacy import preserves the full stored user/assistant text and available tool
steps, explicitly labelled as incomplete display evidence, **not fabricated native
tool calls**. Original attachments migrate directly to the shared volume; only
four recent originals also enter the import job as vision/file blocks. Previous
compaction checkpoints, original reasoning and already-truncated tool evidence
cannot be recovered. The one-time legacy job may be large: it does not silently
truncate history to fit the old character budget. Subsequent turns send only new
input and configuration. Old `history_chars` API settings remain compatible but
do not control native interactive chat memory.

## Containers, deletion and storage

Requires **Docker Engine 26+ / API 1.45+** for named-volume subpath mounts.
Compose mounts the new `chat-sessions` named volume into backend and broker. Each
agent mounts **only its own chat subpath** at `/chat-session`; it cannot see the
volume's root, sibling sessions or the writer-lock inode. Credentials and Pi home
are never mounted into this storage. Do not mount the whole sessions volume into
agents as a compatibility fallback.

- Completion, stop, timeout or failure force-removes the disposable container.
  Only session files and original attachments remain; no idle container/CPU/RAM.
- Backend/broker crash recovery removes orphaned agents before allowing a resume.
  Failed cleanup blocks continuation, rather than releasing isolation guarantees.
- Chat deletion requires stopping its active response first (`409` otherwise),
  reconciles stale containers, acquires the session lock, then removes the chat's
  native history, journal and attachment copies along with its database history.
  Shared volume data for other chats remains. Empty lock inodes remain outside
  agent mounts so concurrent waiters can never bypass a replaced lock.
- No automatic age expiry: retaining a chat retains its history. The broker checks
  `CHAT_SESSION_MAX_BYTES` before each turn (default 256 MiB). This is an admission
  guard, not a live filesystem quota or an instance-wide storage budget. If hit,
  export/read history and delete the chat, or deliberately increase the limit.

Back up **backend-data and chat-sessions together** with writers stopped/drained.
Native sessions can contain prompts, tool arguments/results, file contents and
extension data; protect backups like the chat database. Output originals remain
separately in MaxIO. Restore matching database/session snapshots, not one alone.
The staging-refresh dataset includes chat-sessions; production and staging must
use distinct named volumes even when the copied chats have identical IDs.

Deploy backend, Docker broker, frontend and Pi images together. Check the host's
Docker version before deploying. Run one backend process per database, as before.
No backend provider key or persistent sandbox credentials are introduced.

## Verification

- `uv run pytest tests/backend/test_chat_sessions.py tests/backend/test_chat_queue.py tests/backend/test_chat_recovery.py tests/broker/test_chat_sessions.py`
- `node --test tests/agent/test_chat_session.mjs tests/agent/test_chat_session_rpc.mjs`
- `NAUTIONETTE_DOCKER_TESTS=1 uv run pytest tests/broker/test_chat_sessions_docker.py`

RPC tests use the real production runner and a loopback-only fake model: original
full tool evidence survives fresh processes, native manual compaction persists,
read-only history retrieves summarized results, model switches apply new
instructions, extension entries survive and attachments outlive scratch space.
The Docker test validates actual subpath isolation and disposal as UID 10001.
CI builds the agent image and runs both offline RPC and real Docker checks.
