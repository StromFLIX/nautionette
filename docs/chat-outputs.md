# Durable chat outputs (output-only MVP)

Chats have **Chat / Outputs** tabs. The default agent can publish generated files,
preview images, and editable source documents from its sandbox. Published bytes
are owned by the chat, not the disposable container, and survive later turns and
backend restarts. There is no browser application, editor, WASM dependency, or
browser acknowledgement in the execution path.

This is an application-independent publication foundation. It does **not** add
CADCraft, FigCraft, PDF converters, dedicated Rust workers, a worker pool, or
interactive document synchronization. Application tools can produce files and use
this same publication boundary later. A `source_app` label does not install or run
that application.

## Agent tools

The default Pi extension registers two local tools when a chat turn has a private
output capability. They are independent of the selectable federated MCP tools.
Workflow agents and old/custom agent images without this extension have no output
tools. The model supplies paths and metadata, never a chat ID, credentials, URLs,
or base64 content.

```json
{
  "title": "Mounting bracket",
  "summary": "Drawing, preview and editable source.",
  "source_app": "CADCraft",
  "files": [
    {"path": "/workspace/bracket.pdf"},
    {"path": "/workspace/preview.png", "role": "preview"},
    {"path": "/workspace/bracket.dxf", "role": "source"}
  ]
}
```

Pass this to `publish_output` after generating the files. Optional file `name` and
`mime_type` override basename and extension-based MIME detection. Original bytes
are preserved exactly. The return value is committed output metadata including
`id`, `revision`, file IDs and SHA-256 digests, not file bytes or public links. The
chat shows a compact result card and the latest output in its Outputs tab.

To continue in a fresh container:

1. `read_output {}` lists the chat's latest output manifests.
2. `read_output {"output_id": "<id>"}` returns the selected manifest and available
   revision numbers. Optional `revision` selects a saved version.
3. `read_output {"output_id": "<id>", "file_id": "<source-file-id>"}` restores the
   unchanged file to a new, private directory under the current workspace and
   returns its local `file.path`. Nothing overwrites existing workspace files.
4. Edit the restored source, regenerate exports and previews, then use
   `publish_output` with `output_id` and `expected_revision` as well as the full
   file list. Each revision is a complete, immutable snapshot, not a patch.

Concurrent revision updates use optimistic locking: an outdated
`expected_revision` fails with 409 instead of overwriting newer work. Transport
retries using the same tool-call ID are idempotent. If publication has an ambiguous
network failure, inspect `read_output` before issuing a new publication call.

Publishing an image does **not** automatically give it to the model. To inspect
one visually, restore its original with `read_output` and use Pi's image-capable
`read` tool with a vision-capable model/API route. User-facing previews and model
vision remain separate.

## UI and preview safety

- Latest output metadata is included in ordinary chat snapshots. Publications emit
  a metadata-only `chat.output.published` event; the existing stream delivers a
  fresh snapshot, including when reconnecting after a disconnected browser.
- Publications never change the active tab or steal composer focus. Viewing
  Outputs does not mark hidden replies as read. Composer drafts survive tab
  switches; changing chats resets the output selection and cancels preview work.
- Selecting an output pins its identity, not its position in a reordered list.
  Latest follows committed revisions; an explicitly selected historical revision
  stays pinned across tab switches. Older reply cards open their exact revision,
  not a newer document. History is loaded on demand.
- PNG/JPEG/GIF/WebP inputs are validated with the existing image rules (25 MP),
  decoded and re-encoded as PNG previews at most 1600 × 1600. They load lazily
  through authenticated requests and support an enlarged view.
- Other files, including SVG, HTML and PDF, are **download-only**. PDFs can be
  published alongside one or more generated page images; no PDF renderer or
  conversion service is included. Explicit `role: preview` requires a supported
  raster image.
- Downloads are authenticated and chat/output/revision/file-scoped. Originals use
  `application/octet-stream`, attachment disposition, `nosniff`, sandbox CSP and
  private/no-store caching. Only re-encoded raster previews are inline. No file
  bytes or capabilities enter SSE snapshots or the offline transcript cache.

The presentation routes are `GET /api/chats/{chat_id}/outputs`,
`.../outputs/{output_id}/revisions`, and
`.../outputs/{output_id}/revisions/{revision}/files/{file_id}` (`?preview=true` for
safe thumbnails). They use the app's existing authenticated, single-user model;
this change does not introduce a multi-user authorization model.

## Storage, authorization and limits

SQLite tables `chat_outputs`, `chat_output_revisions` and `chat_output_files`
separate manifests from original/preview bytes. Publication validates all files,
then commits the identity, immutable manifest, bytes and current-revision pointer
in one transaction. A failed validation, quota check or commit publishes nothing.
Normal database backups include outputs; deleting a chat cascades to its output
records. Deleting a message or stopping a turn does not delete its outputs.

Initial hard limits:

| Limit | Value |
| --- | --- |
| Files per publication | 8 |
| Original file size | 20 MiB |
| Originals per publication | 40 MiB |
| Chat storage, all revisions and previews | 256 MiB |
| Outputs per chat | 50 |
| Revisions per output | 64 |

At most two publications validate/commit concurrently on the backend event loop;
waiting requests do not occupy the shared thread pool. This bounds concurrent
raster decoding so normal chat requests retain worker threads.

Quota exhaustion fails explicitly. There is no silent eviction, revision GC,
per-file deletion UI or object store yet. These modest per-chat limits are not a
global storage quota or a dedicated-worker capacity benchmark.

The backend creates a random capability only around a broker execution call;
queued/persisted jobs contain no capability. The broker separates it from the
ordinary job and copies a mode-0600 private file into the container before start.
The Pi extension consumes and unlinks that file. The sandbox still gets no backend
URL, shared internal token or Docker socket. Local tools read regular files only
within the current working directory, `/workspace` or selected `/projects`, check
symlink/descriptor paths and concurrent modification, and send bytes themselves.

Private `backend_publish_chat_output` / `backend_read_chat_output` transports run
through agentgateway and are hidden from model-facing discovery and the MCP tool
picker. Reserved local tool names cannot be shadowed by remote tools. The backend
still requires its normal service authentication **and** the scoped capability.
Every access checks that the same chat/turn is running and not stopped;
publication rechecks immediately before the database commit. Scope cannot be
changed by tool arguments. Completion, cancellation, context exit or a backend
restart revoke access. The capability store assumes the existing **single backend
process**; multiple backend replicas would require a shared grant/fencing design.

This slice uses existing per-turn agent sandbox lifecycle, cleanup, timeout and
resource/concurrency settings. It creates no additional containers, idle workers,
network allowances or background application processes. Separate isolated Rust
worker lifecycle and limits remain a later application-adapter change.

## Deployment and checks

Rebuild **backend, Docker broker, frontend and the default agent-set image**
together. Database initialization adds the tables automatically; the broker's
normal image invalidation rebuilds the changed extension. Existing agent
containers keep their old tools until the next turn. No runtime integration or
MCP configuration changes are required.

```sh
uv run pytest tests/backend/test_chat_outputs.py tests/backend/test_backend_mcp.py \
  tests/backend/test_chat_timeouts.py tests/broker/test_broker.py
node --test tests/agent/test_outputs.mjs tests/agent/test_outputs_rpc.mjs
npm --prefix services/frontend run build
npm --prefix services/frontend run test:e2e -- tests/outputs.spec.js
```

When running the entire suite from inside an instrumented agent container, unset
its test-external `OTEL_EXPORTER_OTLP_ENDPOINT`, `OTEL_SDK_DISABLED` and
`NAUTIONETTE_MODEL_API` variables for the test commands. The telemetry and API
policy tests expect the clean environment used by CI; this does not change any
production configuration.

The real-Pi RPC check uses an offline model/gateway fixture to publish a PDF and
PNG, restore the preview and pass its pixels through the normal image-reading
path. It needs `pi` installed (CI runs it inside the agent image). Browser tests
cover desktop/mobile downloads, previews, revision updates/history, selection,
keyboard controls, errors, retained drafts and hidden-reply read state. These are
not a live CADCraft export or a Docker-worker load test.
