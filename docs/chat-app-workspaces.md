# Investigation: on-demand application workspaces inside a chat

Status: historical research and proposed full workspace design. The narrower
output-only integration and native CLI catalog are now implemented; see
[chat outputs](chat-outputs.md) and [creative apps](creative-apps.md). Browser
WASM editors, shared live documents and separate application workers below
remain proposals, not implemented features.

Investigated against Nautionette `9d9cf1a` and public ArtCraft sources/releases on
2026-10-15. Smoke tests ran in disposable research space, not against production.

## Recommendation

Build a small **chat app workspace** feature, not a general container platform:

- A chat can own durable application documents and display application tabs below
  its existing header: `Chat | CADCraft | FigCraft | …`.
- Load the application's browser WASM build lazily in an isolated iframe.
- Run a native headless engine in a restricted container only when agent work
  needs it. Do not tie this container's lifetime to a Pi turn or a browser tab.
- Store documents independently of both runtimes. Containers and WASM instances
  are disposable; documents are not.
- Use a narrow, stable app-tool API with application-specific adapters. Do not
  register a new global MCP server or deploy a Compose service for every chat.
- Start with **one app** behind a feature flag. FigCraft is the best candidate for
  true shared live editing; CADCraft is a good candidate if single-writer,
  checkpoint-based editing is acceptable initially.

The tab UI is the easy part. The meaningful engineering is shared document state,
scoped tool authorization, save acknowledgement, and runtime recovery. This can be
additive with bounded integration seams, but it is not safely a two-file iframe
change.

## 1. What upstream actually provides

### Release inventory

GitHub's latest published releases, rather than the sometimes older versions on
getartcraft.com/apps, reported the following. Sizes are **compressed release ZIP
sizes**, not browser RAM usage or container sizes.

| App | Published release | Web ZIP, approximately MiB |
| --- | --- | ---: |
| CADCraft | 0.7.0 | 8.1 |
| FigCraft | 0.6.0 | 8.5 |
| GridCraft | 0.7.0 | 6.8 |
| PhotoCraft | 0.9.0 | 10.9 |
| VectorCraft | 0.11.0 | 23.5 |
| FilmCraft | 0.8.0 | 12.7 |
| LightCraft | 0.8.0 | 63.6 |
| PdfCraft | 0.8.0 | 13.5 |
| EffectCraft | 0.10.0 | 18.1 |
| DesignCraft | 0.8.0 | 14.6 |
| WordCraft | 0.7.0 | 14.3 |
| SoundCraft | 0.7.0 | 5.7 |
| DeckCraft | 0.7.0 | 9.0 |

All thirteen root Cargo manifests declare `MIT OR Apache-2.0`. That is encouraging
for hosting and adapting them, but is not a complete redistribution review.
Bundled assets/fonts retain separate licenses; ArtCraft brand assets have separate
restrictions. Preserve required license notices and review each selected release's
NOTICE/ATTRIBUTION and branding terms before distributing it.

Source inspection was concentrated on CADCraft, GridCraft and FigCraft, not a
security or functionality audit of all thirteen apps. These are actively developed
early-stage applications; do not equate an available command with production-grade
format parity.

### Browser WASM is not a headless server runtime

CADCraft's web build is a static HTML/JS/WASM bundle, using eframe/egui and wgpu.
The browser executes it, using WebGPU or WebGL2. No server process is necessary to
serve each instance. The same assets can be cached for every chat.

Its target is `wasm32-unknown-unknown`, with browser APIs. This is **not** a WASI
component that can simply be started in Wasmtime on a server. Doing that requires
an additional headless build/interface. The existing native CLIs are a much
smaller initial integration than adding a server-side WASM platform.

CADCraft's hosting guide says no SharedArrayBuffer/COOP/COEP is required. Avoid
adding global cross-origin isolation headers to Nautionette speculatively. For
0.7.0 the actual WASM in the ZIP was 22,546,717 bytes (~21.5 MiB), larger than the
older approximate size in the hosting guide. Pin and measure real artifacts.

### CADCraft / GridCraft: useful file bridge, not browser MCP

Both have native headless MCP and desktop control APIs. CADCraft's native control
channel is newline-delimited JSON on loopback, and its CLI exposes headless MCP
via stdio. The desktop bridge and headless engine are distinct modes.

Their browser `?host=parent` implementations support messages such as:

- `cadcraft:ready` / `gridcraft:ready`;
- `*:open` with filename and ArrayBuffer/Uint8Array bytes;
- `*:save` with exported bytes;
- `*:dirty` and `*:failed`.

Important limitations observed in source:

1. The bridge **only accepts and answers its same-origin parent window**. A
   cross-origin iframe can display the app, but the existing host bridge does not
   then work with a cross-origin Nautionette parent.
2. This is a **file** bridge, not arbitrary command execution. Opening an iframe
   does not attach native MCP to the browser's document.
3. Save delivery is considered successful when `postMessage` succeeds, before the
   parent durably stores the file. There is no durable-save acknowledgement.
4. It does not provide a parent-requested checkpoint/export or generic command
   request/response protocol. CADCraft has no complete browser drawing autosave
   solution documented in its gaps list.
5. A headless drawing and a browser drawing are separate copies unless an adapter
   deliberately synchronizes them. Never imply otherwise.

For CADCraft, `CadApp::run`, `open_bytes`, `control::handle`, and the engine's
`document.bytes` command are useful existing building blocks for a future browser
adapter. The UI/engine does not need rewriting. A browser transport must queue
work into the app's frame loop and reply asynchronously, not block a browser
thread waiting on a native socket.

### FigCraft: existing shared document server changes the equation

FigCraft already ships:

- a native `figcraft-server`, with persistent snapshots/logs and edit/view links;
- a headless MCP client that can publish/join a server document;
- a WASM browser client that can join the same document;
- live edits, presence and per-participant undo.

This is much closer to the desired architecture: agent and user can be peers in
one actual document, instead of continuously reopening snapshots.

Caveats:

- The server is native, not WASM. It has no user-account authorization model:
  possession of a file link is access. Put it behind Nautionette's scoped access
  layer, keep management credentials server-side, and self-host it. Do not send
  private work to the upstream public sharing server.
- A private shared server is a pragmatic first implementation, but its files are
  in one process/filesystem trust domain. It is not per-document OS isolation.
- The inspected web shell does not implement automatic host-provided document
  joining. Add a small, authenticated embedding/bootstrap bridge, or the user
  would have to paste a join link manually. Full browser command automation is
  unnecessary if the native agent peer already operates on the shared document.
- Headless collaboration is pumped by command execution. In testing,
  `inspect_document` alone did not refresh incoming peer edits. Calling
  `run_command(collab.peers)` before inspection fixed it. Serialize a bounded poll
  loop with commands and poll before reads; do not assume idle stdin means a
  continuously up-to-date engine. Test acknowledgements before reporting an edit
  as durable.
- Native and browser font availability can differ. Pin fonts for reproducible
  layout/rendering. Collaboration and format compatibility remain alpha concerns.

## 2. Checks actually performed

### CADCraft 0.7.0

Downloaded the published Linux tarball and web ZIP; verified both against the
release's SHA256SUMS file. This verifies download consistency, not independent
publisher authenticity or a security audit.

A local native stdio MCP test:

1. Initialized MCP and discovered 12 headless tools.
2. Created a metric drawing, a 100 x 60 rectangle and a radius-4 circle.
3. Inspected the geometry and saved a 14,997-byte DXF.
4. Rendered at 800 x 600 without a GUI/display server.

A local Playwright/Chromium test with software WebGL2:

1. Loaded the **unmodified release** in a same-origin iframe.
2. Received `cadcraft:ready` and sent the generated DXF through `cadcraft:open`.
3. Visually confirmed the rectangle/circle in the editor screenshot.
4. Sent Ctrl+S; received `cadcraft:save` with a 15,049-byte DXF.
5. Observed no page errors.
6. Repeated with a cross-origin parent: no host bridge messages arrived, consistent
   with the explicit source/origin checks.

This proves native automation, browser rendering, and file exchange separately.
It does **not** prove simultaneous shared CAD editing, crash-safe autosave,
container isolation, or mobile/WebView compatibility.

### FigCraft 0.6.0

Ran the published native server and two independent headless MCP clients against
an isolated temporary local server:

1. Client A published a file; client B joined it.
2. A created a rectangle; B observed it after collaboration polling.
3. B renamed that rectangle; A observed the rename after polling.
4. Stopped both clients and the server; restarted the server and joined with a new
   client. The renamed rectangle was recovered.

The initial stale-read attempt was diagnostic, not silently counted as success.
The corrected test explicitly polled before inspecting. This validates a small
native collaboration/durability scenario; FigCraft's browser joining path was
source-inspected, **not** end-to-end tested in this investigation. The full
Nautionette integration and large/concurrent documents were not tested.

### Small-document process memory observations

Linux `/proc/<pid>/status`, approximate values from the smoke runs:

| Process/workload | Observed RSS | Observed high-water RSS |
| --- | ---: | ---: |
| CADCraft MCP, initialized | 5 MiB | 5 MiB |
| CADCraft after tiny drawing and 800 x 600 render | 11 MiB | 15 MiB |
| FigCraft MCP, initialized | 6.3 MiB | 6.3 MiB |
| FigCraft MCP, tiny collaborative drawing | 13.3 MiB | 13.3 MiB |

These exclude container shim/wrapper, browser/GPU memory, shared server and real
large-document workloads. They are not deployment capacity benchmarks. They do
support trying small native headless containers before a more complex runtime.

## 3. Where this fits into Nautionette

Existing architecture provides most of the plumbing:

| Existing code | Relevant behavior |
| --- | --- |
| `services/frontend/src/views/ChatPane.vue` | Header, conversation, composer, generation-fenced chat loading and chat SSE. Natural insertion point for tabs. |
| `services/frontend/src/api.js` | Authenticated API calls; browser API bearer token is stored in localStorage. |
| `services/frontend/src/store.js` | App-wide live events and refresh patterns. |
| `services/backend/nautionette_backend/events.py` | Existing event fan-out and reconnect/resync mechanism. |
| `services/backend/nautionette_backend/db.py` | SQLite chat metadata, turns and migrations; suitable for app-instance metadata, not frequent large document blobs. |
| `services/backend/nautionette_backend/conversations.py` | Turn execution, cleanup and recovery. App grants should be minted at execution, not persisted into queued jobs. |
| `services/backend/nautionette_backend/backend_mcp.py` | Automatically exposes selected HTTP operations, but authenticates globally and has no trusted per-chat caller identity. |
| `services/docker-broker/nautionette_docker_broker/agent_run.py` | Fixed container creation, deployment/chat/turn labels, caps and cleanup; useful patterns, **not** the right lifecycle for app documents. |
| `services/docker-broker/nautionette_docker_broker/chat_agents.py` | Exact-turn inventory/cleanup; new app runtimes must be excluded from this ownership model. |
| `images/agent-sets/default/extensions/nautionette/index.ts` | Discovers MCP tools once at startup. Stable app tools avoid runtime rediscovery. Currently renders MCP results as text rather than preserving image blocks. |

Two particularly important boundaries:

- Pi containers are intentionally one-per-call. Stopping an answer or finishing a
  turn must not destroy an open application document.
- Configured stdio MCP servers execute **inside the shared agentgateway
  container**. Installing `cadcraft-cli mcp` there is not isolated per chat and
  does not solve browser connection or durable document ownership.

Do not expand the backend MCP route allowlist to all `/api/chats` just to expose
app operations: that would also expose unrelated chat mutation/stream routes.

## 4. Proposed minimum architecture

### Which runtime strategy is actually simplest?

| Strategy | Benefit | Limitation |
| --- | --- | --- |
| Browser-only WASM + command bridge | No per-session server compute; agent edits the visible model. | Requires an upstream browser command bridge; cannot execute with no connected browser. |
| CLI inside the **existing Pi container** + durable checkpoints | Smallest infrastructure change; reuse existing per-turn cleanup and limits. | App and agent share a sandbox/resource budget; restore headless state next turn; no independent app compute after the turn. |
| Separate native app worker + browser WASM | Independent app limits/lifetime, background execution, suitable long-term adapter boundary. | Adds a fixed broker lifecycle and admission/reconciliation logic. |
| Native GUI streamed to the browser | Can attach to an unchanged desktop app's control channel. | Adds display server, streaming, latency and often GPU/resource costs; not the lightweight first choice. |

If **minimum touchpoints** outrank separate app isolation, the existing-Pi-container
variant is a reasonable first experiment: pin one headless CLI in its image,
provide only the selected chat app's data scope, and publish/checkpoint through
the same scoped app manager. That is not a separate lightweight application
container, and its isolation/lifetime claims must say so. It avoids building the
independent app-worker supervisor until measurements justify it. The UI/document
contract below should support either execution adapter.

For the fuller experience described in the request, prefer the separate native
worker hybrid below. Do not implement both execution adapters in the first slice.

```text
ChatPane: Chat | App tabs
       |
       +-- normal chat stream/composer (unchanged contract)
       |
       +-- isolated app-origin shell -- iframe --> version-pinned static WASM
                    |                                (same app origin)
                    +-- scoped bootstrap / file / collaboration connection

Pi -- existing gateway --> stable apps tools -- scoped backend app manager
                                                   |
                                          fixed Docker-broker verbs
                                                   |
                                      restricted native engine runtime
                                                   |
                                      durable app document / revisions
```

For FigCraft, browser and native engine connect to a private collaboration server
through scoped routing. The server owns committed live state. Keep its durable
store distinct from exported downloadable files. An initial shared server avoids
one server process per document; stronger isolation can use a per-workspace server
without changing the chat UI/tool contract.

For CADCraft/GridCraft, choose one of these explicit adapter modes:

1. **Checkpoint / single writer:** native engine owns the document during agent
   work; browser receives committed revisions. Human editing is a deliberate
   ownership handoff, not concurrent editing of an independent copy. Do not reopen
   over dirty browser work. The upstream app has no proven enforceable read-only
   embedding mode: either add one, or show a genuinely non-editable preview during
   agent ownership. Never label an unrestricted editor read-only using CSS alone.
2. **Browser command bridge:** a small upstream adapter exposes typed commands and
   checkpoint export through postMessage. The agent can manipulate the exact
   visible document. With no connected browser, return `viewer_unavailable` unless
   the workspace has explicitly checkpointed/handed off to a native engine.
3. **Real collaborative backend:** most capable, but requires app-specific upstream
   work for apps without FigCraft's equivalent. Not the minimum first release.

Do not build automatic dual-engine failover until there is a tested state transfer
protocol. An idle native engine is not a synchronized standby for a browser.

### Tool contract

Keep a small fixed surface, for example:

- `apps_list` — available adapters and capabilities;
- `apps_open` — create/resume an instance for the caller's chat;
- `apps_commands` — bounded/searchable app command discovery;
- `apps_call` — execute one command or a supported batch;
- `apps_inspect` — current state/revision, optionally a bounded visual capture;
- `apps_export` — durable artifact reference, not document base64 in model text;
- `apps_close` — release a runtime/view lease, **not** delete the document.

Open returns an opaque instance ID and current revision. The manager publishes
`app.changed`; the tab appears without agent-produced HTML/URLs. It should not steal
focus while the user types or silently switch away from Chat.

Each operation is checked against an authenticated runtime scope. The current
shared MCP gateway session and model-supplied `chat_id` are **not** that scope.
Mint a short-lived per-turn capability at execution time, bound to deployment,
chat, turn, permissions and expiry. A host-controlled app-tool wrapper supplies
it; never rely on the model to choose an owner. Do not give the container a global
backend/internal token.

The scope transport needs a focused integration test: use a dedicated app MCP
adapter whose schema intentionally accepts a runtime-reserved scope field,
overridden by the host wrapper before transmission, or a scoped proxy endpoint
with supported credential forwarding. Do not assume arbitrary MCP headers are
forwarded through the existing gateway. Keep capability values out of prompts,
public jobs, tool history and telemetry; redact at the receiving edge too. Stable
tool schemas remain discoverable before any app runtime exists.

This scopes the **new app capability**, not every existing agent permission.
Nautionette currently uses an instance-wide app token and can grant agents global
backend management tools. Those agents retain that broader authority; do not
advertise full hostile-tenant isolation merely because app tools are scoped.
A multi-user/tenant product would additionally need principal-based authorization
and restrictions on existing administrative tools.

For visual feedback, fix the existing MCP result bridge to preserve bounded image
content blocks. Otherwise upstream screenshot tools become text/base64 rather
than usable model images. Never persist full image/document bodies in event rows.
Keep structured results small: current chat timelines truncate tool text to 4,000
characters.

### Data and command semantics

Add an `app_instances` metadata table with roughly:

`id, chat_id, app_id, pinned_version, adapter_mode, runtime_state, revision,
document_ref, created_at, last_activity_at`.

Use a separate revision/operation record for request IDs, expected revision,
commit status and result reference. Put documents/checkpoints in a dedicated,
backed-up app-data volume or object store; do not use Pi's `/workspace` tmpfs or
browser localStorage as the only copy. Existing global artifact downloads are
not a sufficient scoped document namespace on their own.

Serialize commands per instance. For snapshot mode, require `expected_revision` /
If-Match for saves and reject conflicts rather than last-writer-wins. For collab
mode, use the upstream protocol's commit/ack semantics instead of pretending
whole-file revision checks implement collaboration.

Distinguish `accepted`, `applied`, and `durably committed`. On lost replies, never
blindly repeat a mutation. A wrapper request-ID ledger cannot make an arbitrary
upstream command exactly-once if a crash happens between mutation and ledger
write. Fence/recover the session, inspect committed state, and report
`outcome_unknown` where necessary. Only replay when the adapter actually proves
it safe.

### Browser isolation

Do **not** host third-party app JavaScript on Nautionette's authenticated origin:
a same-origin iframe with scripts/storage can access the app bearer token and
other parent state. WASM does not remove this JavaScript/DOM risk.

Recommended shape:

- a separate, credential-free app origin; ideally per-instance origins when
  hostile document/script isolation is required;
- a small host shell on that origin, with the WASM iframe on the **same app
  origin** so upstream open/save bridges still work;
- a narrow cross-origin parent-to-shell postMessage protocol validating
  `event.origin`, `event.source`, message schema, instance and request ID;
- bootstrap via an origin-checked exchange with a short-lived instance-limited
  grant, not a global bearer token in iframe URLs;
- narrow CSP/frame-ancestors, no arbitrary navigation, no unneeded popup/clipboard
  privileges, bounded payloads and narrowly permitted network destinations.

One shared app origin isolates apps from Nautionette but not app instances from
one another. Plan accordingly. Browser origin isolation is not a CPU/RAM quota or
a substitute for container isolation.

Add save request/ack support for the chosen file-bridge adapter. Keep a document
marked unsaved in the host until the backend confirms durable storage; buffer
failed uploads and expose retries. A browser unload handler alone is not reliable
crash protection.

Test HTTPS/WebGPU policy, WebGL2 fallback, mobile memory pressure and Capacitor
WebViews. The native app uses an origin distinct from its backend already. Do not
assume desktop Chromium results cover that environment.

## 5. Lifetime, cleanup and concurrency

Keep three separate lifetimes:

1. **Document:** retained until explicit deletion/retention policy; backed up.
2. **App instance metadata/tab:** retained with its chat even while suspended.
3. **Compute runtime / browser view:** created lazily and removed freely after a
   safe checkpoint. A tab can display `Suspended` without losing its document.

Proposed initial policy (configurable, not existing behavior):

| Event | Behavior |
| --- | --- |
| Tool opens an app | Idempotently create/resume the instance; admit compute only if needed. |
| Pi turn finishes | Release its compute lease; keep document/tab. |
| User switches to Chat | Preserve editor state; pause unnecessary drawing if supported. Do not unmount dirty state. |
| Browser disconnects | Expire its presence lease after a grace period; do not delete documents. |
| Runtime idle for ~5 minutes, no command/lease | Confirm durable checkpoint and remove container; retain instance as suspended. |
| Explicit close tab | Release view/compute where safe; app remains reopenable. |
| Stop response | Stop/fence in-flight agent app operations; retain all committed document work. |
| Runtime crash / OOM | Mark failed/suspended; recover only committed state; don't replay unknown mutations. |
| Backend/broker restart | Reconcile only labeled app runtimes for this deployment, using ownership/generation fences. |
| Chat/app deletion | Fence new calls, revoke grants, settle/stop compute, then remove data through an explicit deletion policy. |

Use leased activity rather than an open browser socket as the only keepalive.
Do not suspend during a long export or retain compute forever because a hidden tab
sends heartbeats. Protect checkpoint-vs-cleanup races with generation fencing.
A checkpoint failure should surface an error and block unsafe eviction, while a
hard resource limit still contains the process.

Each native container should use a fixed image digest, non-root UID, read-only
root, dropped capabilities, no-new-privileges, memory/CPU/PID/tmpfs limits, and only
its own data mount. No Docker socket, project volume, provider credentials or
internal admin token. Default deny network access; collaborative workers get only
the specific collaboration route they need. An `internal` Docker network alone
does not enforce peer-to-peer or destination allowlisting.

Use Docker first. gVisor/Kata/microVMs can strengthen a future untrusted-code
boundary; Kubernetes or a new WASM scheduler is not necessary for this proof of
concept. Trusted app binaries parsing untrusted documents still require patches,
limits, and review of scripting/plugin/network commands.

### Capacity

Start with conservative **proposed** limits, for example 1–2 active app engines
per chat, 8–16 total per instance, a separate render/export semaphore, and measured
small-document memory caps around 256 MiB per worker. These are rollout knobs,
not claims that every ArtCraft app fits that cap. Use larger app-specific caps or
reject oversize inputs; never borrow the Pi runtime's current default 4 GiB cap
for each app blindly.

A planning upper bound is:

`min(global cap, memory budget / app reservation, CPU/render budget, I/O budget)`.

For illustration only, 8 GiB *dedicated to app workers* divided by a 256 MiB
reservation gives 32 workers before additional headroom. It is not a measured
server capacity. Idle headless processes can be cheap; big drawings, compressed
imports, fonts, video/audio and image rendering can dominate memory and CPU.

Static WASM tabs consume principally the client's resources, so backend session
count is not browser renderer capacity. Limit retained iframe renderers too:
lazy-load, checkpoint before eviction, and avoid mounting every app in every chat.

## 6. Keeping touchpoints limited

Keep most new logic in new app-specific modules. Expected existing integration
seams are:

| Area | Minimum integration |
| --- | --- |
| Frontend | `ChatPane.vue`, `api.js`; new `ChatApps.vue`/`AppFrame.vue` and state helper, using existing live events. |
| Backend | Router registration, additive DB schema, startup reconciliation, chat-delete cleanup, execution-time scoped grant issuance. New `app_sessions.py` / app router / app MCP adapter hold most logic. |
| Docker broker | Register fixed app verbs, scoped reconciliation and matching client methods; new `apps.py`, separate from Pi-turn cleanup. |
| Agent runtime | Deliver private app scope in the outgoing job, expose it only to the host wrapper, support image results and runtime-injected app scope. |
| Infrastructure | App asset origin/host shell, private app data volume, pinned engine image; private FigCraft server only if that adapter is selected. |
| Upstream adapter | FigCraft: embedding auto-join bootstrap. CADCraft: safe snapshot handoff/read-only mode or a browser command bridge, plus checkpoint/save acknowledgement as needed. |

For the **separate-worker** variant, this is approximately five architectural
seams and on the order of 10–15 existing source files for a complete first adapter,
plus new modules, configuration and tests. The existing-Pi-container experiment
can omit the independent app-runtime supervision seam. The exact count depends on scope transport and viewer mode; it is not an
implementation promise. Later apps should add a manifest, image and adapter rather
than changing the chat engine again.

Avoid changes to Temporal/workflow execution for the first release. Normal chat
SSE, steering, queuing, recovery, model configuration and Pi container lifetime
can retain their existing contracts. A later workflow adapter can explicitly open
a durable app instance with operation idempotency designed for activity retries.

Frontend details to preserve:

- Chat remains the default, and the composer/chat connection stay alive while an
  app is selected. The agent may keep working while a user watches the app.
- Lazy mounting and tab switching must not destroy unsaved state.
- Gate `acknowledgeRead` on the **Chat tab being visible**. Today snapshots can mark
  chat replies read while the conversation is at the bottom; an app tab must not
  silently mark unseen replies as read.
- Fence late loads/messages by chat/instance generation, as ChatPane already does.
- Preserve focus, drafts, keyboard behavior and scroll position; use accessible
  tab roles and clear `Starting / Ready / Busy / Suspended / Failed / Unsaved`
  states. Empty chats and feature-disabled deployments must look/work as before.

## 7. Rollout and acceptance gates

1. **Feasibility spike, no chat changes:** isolated asset hosting, scoped native
   runtime, two-client shared-state/save test. Native and CAD file-bridge subsets
   were tested here; authorization/container/browser integration remains to do.
2. **Feature-flagged vertical slice in staging:** one app, one chat-owned document,
   fixed app tools, tab shell, checkpoint/reopen, bounded resources. Prefer
   FigCraft if actual simultaneous editing is the priority. If CADCraft is the
   priority, explicitly choose single-writer or fund its browser adapter.
3. **Hardening:** crash/restart/unknown-operation tests, origin/token isolation,
   save acknowledgement, quotas and load testing.
4. **Small opt-in rollout:** pin a tested release. Rebuild images/assets in CI,
   not during an individual chat request. Old sessions keep their pinned version;
   version upgrades happen at a safe reopen boundary with a recoverable copy.
5. **Expand adapters** only after the contract holds. Add capabilities such as
   `collaborative`, `browser_commands`, `checkpoint`, `background_execution`,
   `read_only_view`, `export_formats`, rather than pretending all apps are equal.

Required regression/security checks:

- Existing text chats, SSE reconnects, queued/steered messages, stop, draining,
  backend restart and project worktrees behave as before when apps are disabled.
- Concurrent opens produce one intended instance; another chat's grant cannot
  inspect/edit/export it; a stale turn/grant cannot mutate after revocation.
- Cross-origin messages, wrong sources, arbitrary URLs/images/paths, oversized
  payloads, mount traversal and network escape attempts are rejected.
- App code cannot read Nautionette localStorage/bearer tokens or parent DOM.
- User edits survive tab switches; browser/server/container failure restores only
  confirmed state and exposes unsaved/unknown outcomes honestly.
- A lost mutation reply is not silently retried; deletion/checkpoint/restart races
  do not resurrect deleted instances or evict live work.
- No container/grant leaks after idle/close/delete or broker/backend restart.
- Load tests cover large/complex files, simultaneous render/import bursts and
  memory pressure on both server and mobile client.

Rollback: disable the feature/admission path, drain and remove only app-labeled
runtimes, retain documents and additive tables for export/recovery. Chat operation
must not depend on the app server being healthy. Deploy additions without restarting
unrelated workflow workers or live chat agents just to register an app.

## Sources

- [ArtCraft app catalog](https://getartcraft.com/apps)
- [CADCraft release 0.7.0](https://github.com/storytold/cadcraft/releases/tag/v0.7.0)
- [CADCraft inspected source](https://github.com/storytold/cadcraft/tree/fc4e98315a8d771b5ab832f0ade0b5e7b106df7f)
- [CADCraft hosting](https://github.com/storytold/cadcraft/blob/fc4e98315a8d771b5ab832f0ade0b5e7b106df7f/packaging/web/README.md)
- [CADCraft host bridge](https://github.com/storytold/cadcraft/blob/fc4e98315a8d771b5ab832f0ade0b5e7b106df7f/apps/cadcraft-web/src/host.rs)
- [CADCraft control protocol](https://github.com/storytold/cadcraft/blob/fc4e98315a8d771b5ab832f0ade0b5e7b106df7f/docs/control-protocol.md)
- [CADCraft MCP](https://github.com/storytold/cadcraft/blob/fc4e98315a8d771b5ab832f0ade0b5e7b106df7f/docs/mcp.md)
- [GridCraft inspected source](https://github.com/storytold/gridcraft/tree/e6b6b349708ab4978cb3f061f6bd6ecbc25667cb)
- [FigCraft release 0.6.0](https://github.com/storytold/figcraft/releases/tag/v0.6.0)
- [FigCraft inspected source](https://github.com/storytold/figcraft/tree/102404e8c3d23d86c3490c1a6beff3202bb73e25)
- [FigCraft server](https://github.com/storytold/figcraft/blob/102404e8c3d23d86c3490c1a6beff3202bb73e25/docs/server.md)
- [FigCraft live-session MCP commands](https://github.com/storytold/figcraft/blob/102404e8c3d23d86c3490c1a6beff3202bb73e25/docs/mcp.md#live-sessions)
- [Nautionette frontend design](frontend-design.md)
- [Nautionette MCP runtime/trust boundary](mcp-servers.md)
