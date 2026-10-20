# Durable chat outputs and MaxIO storage

Chats have **Chat / Outputs** tabs. Agents publish generated files, preview images
and editable sources from their disposable sandboxes. A completed publication
survives later turns, container cleanup and backend restarts. No browser editor,
WASM application or browser acknowledgement is involved in execution.

**Docker Compose runs MaxIO 0.4.2 as the output binary backend.** SQLite retains
ownership, immutable revision manifests, checksums, upload reservations and the
object-deletion ledger, **not new original/preview bytes**. The S3 adapter is
provider-neutral so the backend can change later without changing chat manifests;
MaxIO is the currently deployed Compose choice, not an optional fallback.

> [MaxIO upstream](https://github.com/coollabsio/maxio) warns that it is under active
> development and is not yet recommended for production. Keep it private, pin the
> version, test upgrades, and back up its volume together with the metadata DB.
> This integration does not make an early object store production-hardened.

The [native creative apps integration](creative-apps.md) supplies ArtCraft CLIs
which produce files through this boundary. `source_app` is descriptive metadata,
not an app invocation. Incoming chat image attachments remain a separate system;
this migration does not move `chat_images`, project worktrees or workflow artifacts.

## Agent tools

The default Pi extension registers local `publish_output`, `update_output` and `read_output` tools
when a chat turn has a private capability. They remain available independently of
the selectable federated MCP tools. Workflow agents and custom images without the
extension/capability have no chat output tools. Models supply paths and metadata,
never chat IDs, credentials, transfer URLs or file bytes:

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

Pass this to `publish_output` after generating completed files. **This tool only
creates new outputs and has no `output_id` or `expected_revision` parameters.**
The server generates the ID. To revise an existing output, use `update_output`
with the same metadata/file list plus the `output_id` and `expected_revision`
returned by `read_output` in this chat. Never invent IDs or copy them from another
chat. If an update reports "Output not found in this chat", list `read_output {}`
to find the correct ID, or use `publish_output` to create a new output. Do not
repeatedly retry the missing ID.

Creation and revision updates have separate schemas so models that fill every
schema property cannot turn a new publication into a fabricated-ID update.
`read_output` accepts omitted or null fields, so those models can list outputs
with `{"output_id": null, "revision": null, "file_id": null}` without inventing IDs.

Optional file
`name` and `mime_type` override basename and extension-based detection. Originals
are preserved exactly. Success returns committed output/file IDs, revision numbers
and SHA-256 digests, not bytes or public links. The chat receives a compact result
card and its Outputs tab receives the latest complete snapshot.

To continue in another turn:

1. `read_output {}` lists the chat's latest manifests.
2. `read_output {"output_id": "<id>"}` inspects a manifest and available revisions.
   Add `revision` to inspect an older retained snapshot.
3. Add `file_id` to restore that file into a new mode-0600 file in a private local
   directory. The tool returns `file.path`, never transfer URLs or base64. It
   streams the download and verifies both its byte count and SHA-256. Partial or
   corrupt downloads are removed; existing workspace files are not overwritten.
4. Edit the restored source, regenerate previews/exports and call `update_output`
   with the full file list, `output_id` and `expected_revision`. Updates are complete,
   immutable snapshots, not patches. Old revisions stay available until deleted.

An outdated expected revision fails with 409 instead of overwriting newer work.
Provider tool-call IDs longer than 256 Unicode characters become
`sha256:<UTF-8 digest>`; the answer-to-publication correlation uses the same
normalization. Retries with the same ID and content are idempotent; changed content
conflicts. After an ambiguous network failure, inspect `read_output` before making
a new publication call.

Publishing a preview does **not** automatically show its pixels to the model.
Restore the image and use the image-capable `read` tool with a vision-capable model.
User previews and model vision remain separate.

## Binary publication protocol

1. The extension safely opens regular, nonempty workspace/project files, checks
   descriptor/symlink boundaries and hashes bounded chunks. It detects changes to
   size, modification time or change time during publication.
2. A small private `backend_prepare_chat_output` MCP call sends only metadata,
   lengths and checksums. The backend authenticates the live chat/turn capability,
   checks policy and physical free space, reserves instance/chat quota, and
   registers staging objects durably before issuing expiring signed PUT URLs.
3. The sandbox streams each file directly to MaxIO over the private agent network.
   Signed headers bind length, content type and SHA-256. File bytes no longer pass
   through MCP, agentgateway's JSON buffers or a base64 HTTP request body.
4. The small `backend_publish_chat_output` commit request identifies the prepared
   upload. The backend verifies staging data with bounded temporary files,
   validates/re-encodes raster previews, and copies originals to private final
   keys for which it never issued PUT URLs. Final originals/previews are read back
   and verified before a SQLite transaction exposes the complete revision.
5. Immediately before that transaction the backend rechecks ownership, active-turn
   and stop/deletion fences, expected revision and current quotas, including safe
   preview bytes. A failed check publishes nothing. Objects created before a
   failed/crashed commit remain in the durable deletion ledger for retry.

MaxIO 0.4.2 has a SigV4 parser quirk: it does not percent-decode
`X-Amz-SignedHeaders` before splitting semicolons. The adapter normalizes only that
query field to literal semicolons, which preserves standard SigV4 canonical
encoding. It does **not** weaken or remove signed size/checksum headers. CI tests
actual signed PUTs, checksum rejection, downloads and copies against MaxIO.

Transfers expire after 10 minutes. A stopped turn cannot commit even if its issued
URL has not expired. Issued S3 capabilities cannot be revoked immediately:
staging deletion waits until their expiry plus clock-skew headroom (up to 11
minutes), preventing a late PUT from recreating an already-collected object.
URLs and capabilities never enter model results, chat history, events, settings
or browser snapshots. Errors do not echo S3 URLs, credentials or provider bodies.

Already-running old images retain a bounded legacy base64 compatibility path
(20 MiB/file, 40 MiB/publication). It also writes to S3, never new SQLite BLOBs.
Rebuild the default agent image for binary transfers and long-ID normalization;
splitting files is not a fix for a stale transport or an invalid request ID.

## Settings → Storage

The implemented route is **`/settings/storage`**, searchable in Settings. It offers:

- Instance charged usage and quota remaining, retained originals/previews, output,
  revision and file counts, active reservations, pending deletions and separately
  reported temporary/staging bytes.
- Actual MaxIO backing-filesystem total/used/available space, measured through the
  backend's read-only mount of **the same `maxio-data` volume**, plus the separately
  labeled backend data filesystem. These may share a host disk; do not add their
  totals. This is filesystem capacity, not an invented S3 free-space statistic.
- Editable instance, chat, file and publication quotas and a physical free-space
  reserve. Raising a logical quota does not increase physical disk capacity.
- Every retained output revision, paginated, searchable by chat/output/filename,
  sortable largest/oldest/newest, with per-file sizes and full snapshot totals.
- Selected-revision, whole-output and age-based cleanup. Preview returns an exact,
  expiring plan of affected revisions/bytes. Confirmation never expands that plan
  to outputs published afterward. Review at most 1,000 revisions per cleanup;
  narrow large selections or repeat in batches.
- Opt-in automatic age retention, off by default, with a keep-current-revision
  option. Saving an enabled policy authorizes background permanent cleanup.
- Verified legacy migration status and retry, deletion errors and retry, SQLite
  size/reusable pages, and explicit database compaction.

Deletion granularity is an immutable revision or whole output, not a file removed
silently from the middle of a manifest. Deleting a historical revision removes its
reply cards. Deleting the current revision falls back to the newest retained one;
an output without revisions disappears. Deleted revision numbers are never reused.
Chat snapshots update, and an open history selection falls back if its revision
is removed. Deleting a chat queues all original/preview objects through database
cascade triggers. Deleting a message or stopping a turn does not delete outputs.

Removed durable objects still consume instance quota until S3 confirms deletion.
An S3 outage does not lose the cleanup ledger or silently release that quota;
maintenance retries after recovery. Staged transfer copies are reported separately
and guarded by physical free-space checks rather than charged twice as retained
chat documents. There is no unannounced eviction.

### Default policy

| Setting | Default | Configurable |
| --- | --- | --- |
| Instance output quota | 10 GiB | Yes |
| Per-chat quota, every revision and preview | 256 MiB | Yes |
| Original file size | 20 MiB | Yes, up to 1 GiB |
| Originals per publication | 40 MiB | Yes, up to 2 GiB |
| Minimum free filesystem reserve | 512 MiB | Yes, including 0 |
| Automatic retention | Off (`retention_days=0`) | Yes |
| Preserve current revision during retention | Yes | Yes |
| Files per snapshot | 8 | Protocol ceiling |
| Outputs per chat | 50 | Protocol ceiling |
| Revision numbers per output | 64 | Protocol ceiling |

Quota checks include concurrent upload reservations and recheck the saved policy
at commit. Quotas can be lowered below current usage without deleting anything;
subsequent publications are blocked. Raster previews count toward retained quotas.
Physical free-space checks allow for staging/final copies, other reservations,
previews and backend temporary files. The backend serializes publication and
maintenance with a storage lock, but releases the SQLite lock during network I/O.
This supports the application's existing **single backend process**, not multiple
replicas with independent in-memory grants/locks.

Quota failures have structured, actionable details, for example:

```json
{
  "code": "output_storage_limit_exceeded",
  "message": "The instance output storage limit would be exceeded. Remove outputs or increase the limit in Settings → Storage.",
  "scope": "instance",
  "used_bytes": 10737400000,
  "limit_bytes": 10737418240,
  "requested_bytes": 1048576,
  "settings_url": "https://your-app.example/settings/storage"
}
```

`APP_URL` supplies the actual deployed origin; without it the link is the valid
relative route `/settings/storage`. File/publication oversize, low physical space,
physical exhaustion, integrity failure and unavailable object storage have distinct
codes/statuses. An agent should return the error's actual settings link and usage,
not retry repeatedly or describe a delivery failure as failed CAD generation.
The authenticated management APIs live under `/api/settings/storage`; the backend
MCP also exposes status, policy, inventory, preview/delete, maintenance and compaction.

For a future independently hosted S3 service, leave `OUTPUT_STORAGE_DISK_PATH`
empty unless a trusted local mount really measures that service's backing disk.
S3 does not expose physical capacity: the UI explicitly reports it unavailable,
while retained usage/quota accounting still works. Never substitute the backend
container's overlay filesystem and label it as a remote S3 server's capacity.

## Preview and authorization boundaries

PNG/JPEG/GIF/WebP originals are limited to 25 megapixels, decoded and re-encoded as
PNG thumbnails at most 1600 × 1600. Safe previews load lazily and can be enlarged.
PDF, SVG, HTML, CAD and other files are download-only; publish a separate raster
preview for them. There is no browser PDF renderer or conversion service.

Browser downloads stay on the authenticated chat/output/revision/file route:
`GET /api/chats/{chat_id}/outputs/{output_id}/revisions/{revision}/files/{file_id}`.
They stream through the backend, **not a public S3 redirect**. Originals use
`application/octet-stream`, attachment disposition, `nosniff`, sandbox CSP and
private/no-store caching. Only sanitized raster previews (`?preview=true`) are
inline. Browser/offline caches retain manifests, not bytes or capabilities.

Android saves use the same authenticated original bytes, staged into a private
cache file through ordered 192 KiB binary chunks (at most 256 KiB base64 per bridge
call). The native plugin checks offsets and total length before opening Android's
save-file picker, then streams the staged file to the selected document provider.
Only a staging ID/name/MIME type enter the picker call: whole-file base64 must not
be retained in Capacitor's Android activity state, which has a small Binder limit.
Staging is discarded on completion, cancellation, errors and navigation before the
picker opens; abandoned files from a killed process are removed on the next plugin
load. Failed provider writes attempt to delete the incomplete destination rather
than report success with a truncated file. This protocol requires rebuilding and
installing the Android APK; updating only the hosted frontend is not sufficient.

The backend creates capabilities only around active broker calls. They do not
enter persisted jobs; the broker supplies a mode-0600 private file which the Pi
extension consumes and unlinks. Private prepare/publish/read transports are hidden
from model-facing discovery and cannot shadow local tools. Service authentication
and the live scoped capability are both required. Models cannot change chat scope.
The sandbox receives no S3 credentials, shared internal token or Docker socket.

## Deploy, migrate and back up

1. Back up the existing database before upgrading. The GitHub → Coolify release
   pipeline generates separate random `MAXIO_ACCESS_KEY` and `MAXIO_SECRET_KEY`
   values only when missing, persists them in the target application's environment,
   and verifies persistence without logging them. Existing nonempty credentials
   are never rotated, copied between environments or reused from preview settings.
   For plain Compose/manual deployments, generate a separate strong
   `MAXIO_SECRET_KEY` (for example with `openssl rand -hex 32`) through the host's
   secret-management channel; set `MAXIO_ACCESS_KEY` too. Do not reuse app/internal
   tokens, commit `.env`, or print secrets. Compose requires these values.
2. Rebuild backend, frontend and the default agent-set image. Compose starts pinned
   MaxIO, creates the configured private bucket, waits for its health check and
   persists bytes in `maxio-data`. No MaxIO host ports or console are published.
   The backend connects on the private outputs network; sandboxes reach signed
   object URLs on the agent network.
3. Database initialization makes legacy BLOB columns nullable without discarding
   their contents. Background maintenance migrates up to 10 files per minute.
   Each original is checked against its original manifest; both original and safe
   preview are copied and read back with SHA-256 verification before their BLOBs
   are cleared atomically. Existing authenticated downloads work during migration.
   Migration ignores new-publication quotas so existing files are not stranded;
   physical-space safeguards still apply.
4. The release pipeline verifies authenticated component health **and** MaxIO
   connectivity through `/api/settings/storage`; a running but disconnected output
   store fails verification. Check **Settings → Storage** for remaining legacy files
   and errors. Failed migration retains source bytes and retries. The manual action
   runs another migration/cleanup batch. A binary restore can migrate just the
   requested legacy source while bulk migration is still underway.
5. Leave temporary headroom: the old NOT NULL table rebuild and verification can
   temporarily require additional database/filesystem space. SQLite clears bytes
   into reusable pages; after migration it automatically checkpoints/VACUUMs when
   no chat turn is running and enough reserve remains. Manual compaction is also
   available and can briefly block metadata writes. Do not delete old backups
   before verifying migrated outputs and a complete restore.

**Database-only backups are no longer complete output backups.** Back up
`backend-data` and `maxio-data` together (including MaxIO's internal metadata and
keys), consistently with the other persistent datasets. Restore the same bucket
configuration and compatible pinned MaxIO image. Protect backups as private user
data. The staging refresh tool now stops/snapshots/restores MaxIO and all **seven**
datasets, validates separate volumes, compatible MaxIO images and bucket/key
configuration, then resumes isolated staging. See [staging](staging.md) and
[the pipeline guide](github-pipelines.md). Never copy live object/SQLite files
independently and assume they form an exact consistent snapshot.

## Checks

```sh
uv run pytest tests/backend/test_chat_outputs.py tests/backend/test_output_storage.py \
  tests/backend/test_backend_mcp.py tests/test_staging_refresh.py
node --test tests/agent/test_outputs.mjs tests/agent/test_outputs_rpc.mjs \
  tests/agent/test_artcraft_native.mjs tests/agent/test_artcraft_rpc.mjs
npm --prefix services/frontend test
npm --prefix services/frontend run build
npm --prefix services/frontend run test:e2e -- tests/outputs.spec.js tests/storage.spec.js
```

CI additionally provisions the shipped MaxIO image and runs
`tests/backend/test_output_maxio.py`. The opt-in test uses `MAXIO_TEST_ENDPOINT`,
`MAXIO_TEST_ACCESS_KEY`, `MAXIO_TEST_SECRET_KEY` and a disposable
`MAXIO_TEST_BUCKET` starting with `nautionette-test-`. Never target a live bucket.
It exercises an actual 25 MiB binary publication, signed PUT/checksum rejection,
private signed reads, authenticated streamed downloads, revision deletion and
verified legacy original/preview migration. Real Pi/native CAD tests cover export,
publication, fresh-turn source restore and revision editing without model keys.

Inside an instrumented agent container, unset external
`OTEL_EXPORTER_OTLP_ENDPOINT`, `OTEL_SDK_DISABLED` and `NAUTIONETTE_MODEL_API` when
running the full suite; tests expect CI's clean environment. This does not alter
production telemetry or provider configuration.
