# Managed mini-apps

An app is self-contained HTML/CSS/JavaScript plus an optional Rust module using the platform's bundled SQLite SDK. Ordinary buttons call the app, not an agent. Deliberate workflow buttons can start an explicitly granted workflow for durable, multi-system orchestration.

## Publishing

Agents start with the backend MCP tool `miniapp_contract` (`GET /api/miniapps/contract`). It returns the exact manifest schema, SDK contract, resource limits and a working persistent-counter example.

1. `PUT /api/miniapps/{lowercase-slug}` with `{expected_revision: 0, manifest: ...}` creates an app. Updates supply the latest `revision`; stale updates return 409.
2. Frontend fields are `html` (body markup), `css` and `js` strings. Bundle dependencies into these strings; do not load remote scripts. Data-URL images/fonts are supported. Set `agent_id` to the owning saved agent; the shared agent rail filters both Apps and Chats. `icon` is a Material icon name (default `apps`); optional `image` is a PNG/JPEG/WebP base64 data URL of at most 32 KiB. Remote images and SVG are rejected.
3. An optional backend contains `source` (an `app.rs` module), `mode`, `memory_mb` and `permissions`. No Dockerfile, image, mount, command, port or Cargo dependency is accepted from a publisher.
4. Poll `GET /api/miniapps/{id}` after an ambiguous publication response. A pending build is **not** a successful release. Do not blindly repeat the PUT.
5. Test actual operations with `POST /api/miniapps/{id}/invoke`, supplying the active `revision`, logical `path`, `method`, optional `body` and a request ID for mutations. This route is also exposed through MCP. Agent ownership is navigation metadata, not an additional user/RBAC boundary.

Apps have five recent source releases plus any older active release. `POST .../restore` rebuilds retained code as a **new** revision, using the current compatible platform SDK and current data—not a stale database snapshot. Keep migrations backward compatible. A failed candidate never becomes active and cannot change the previous release's database.

### Shortcuts and release selection

Apps are **not pinned by default**. `PATCH` with `expected_revision`, `pinned: true` and `pin_revision: active_revision` pins the explicit, healthy, unarchived release. The five most recently pinned apps appear as clickable avatar/icon shortcuts directly below **Search chats** and **Search apps**. Selecting an agent shows only its pins; **All chats** and **All apps** show pins across all agents, including workspace-owned apps. Pins are independent of the search text. When the sidebar is collapsed or a mobile detail view hides the list, the same shortcuts remain above the open chat/app instead, without a duplicate bar. Avatar links have keyboard labels, release/owner tooltips and 44-pixel targets; narrow lists scroll the row locally rather than widening the page. Metadata refreshes do not reorder them. Unpinning or archiving removes a shortcut. Publishing new code without `manifest.pinned: true` unpins the old release; a failed candidate leaves its pin intact. The UI's **Use & pin** action restores retained code with `pinned: true` as a new release on current data, then pins that newly activated release. Pins never execute inactive backend code or silently roll back data. Existing pins migrate to their active release.

`PATCH` edits pins and archive state. Archiving revokes new capabilities and stops the container without deleting data. Operations already admitted upstream may finish; archiving does not undo remote writes. Deletion requires prior archiving and the current revision, then removes source, runtime and SQLite files. An archived app still occupies an instance app slot. The Apps UI groups by agent, lists pins in chat navigation, and exposes resources, restart, rebuild, restore and archive controls.

## Browser contract

```javascript
await nautionette.ready
const state = await nautionette.request('/state')
const changed = await nautionette.request('/increment', { method: 'POST' })
window.addEventListener('nautionette-theme', event => { /* optional */ })
```

The host supplies theme tokens as CSS variables (for example `--surface-app`, `--text`, `--accent`) and interface scale. The bridge generates a fresh request ID for mutations unless the app supplies a previously retained ID.

Every generated document lives in `iframe sandbox="allow-scripts"` **without** `allow-same-origin`. It has an opaque origin: no host DOM, instance token, cookies, localStorage or sessionStorage. A private MessageChannel reaches only this app's active-revision JSON dispatcher. There is no generic authenticated fetch/MCP bridge.

A first-in-document CSP denies networking, external assets, forms, frames, objects and base URLs. The embedding host's `frame-src 'self'` also denies external/blob/data navigations that could shed the child's CSP. Generated executable assets are never served as host-origin HTML/JS: authenticated source APIs return JSON with `nosniff` and a sandbox CSP. Native Android/WebView uses the same shell and isolation contract; browser CPU/memory is **not** a hard-quota VM boundary. As with other generated UI, malicious/buggy JavaScript can impair its renderer's availability.

Bridge requests have bounded size, four in-flight slots and 64 calls/minute per mounted app. Closing/replacing the frame or switching instances aborts the host bridge. Reopening an app reads current durable state instead of resubmitting a lost mutation.

## Rust/SQLite SDK

See `services/miniapp-sdk/src/app.rs` and the live contract. Implement:

```rust
use crate::{Context, Request, Result, Value, json};
pub fn health(ctx: &mut Context) -> Result<()> { /* local migrations */ Ok(()) }
pub fn handle(ctx: &mut Context, req: &Request) -> Result<Value> {
    // req.method, req.path, req.body, req.request_id
    Ok(json!({"ok": true}))
}
```

`ctx.db` is a `rusqlite::Connection`. The SDK enables persistent WAL and full synchronization. State survives container replacement, idle suspension, broker restart and platform deployment on the retained volume. Only `state.db`, `state.db-wal` and `state.db-shm` are writable persistent mounts; the parent/root filesystem is read-only. Use SQLite for application state, not arbitrary persistent files.

- `ctx.mcp(exact_tool_name, arguments_json, request_id)` calls a granted tool through a reusable host-owned MCP session. Server-side recursive argument constraints restrict resources (e.g. repository owner/name or service UUID). Omitting constraints deliberately grants the whole selected tool, not implicit resource scoping.
- `ctx.http(https_url, method, json_body, request_id)` reaches a granted **public** HTTPS origin/path/method. DNS answers must all be public unicast addresses; the request pins the resolved destination while preserving TLS SNI/certificate verification. No private/metadata addresses, redirects, cookies, instance credentials or caller-selected authorization headers. Use managed MCP targets for authenticated services.
- Empty permissions grant no external access. Host administration and credential tools are prohibited. The only workflow exceptions are the canonical `workflows_run_workflow` and `workflows_list_runs` tools, each requiring a valid fixed workflow name (see below). The adapter accepts only a named tool call, not arbitrary MCP protocol messages. Publishing a grant is authority-bearing: inspect exact tools/resource constraints, particularly write-capable integrations.
- Health initialization must be local and side-effect-safe. Candidate capabilities remain denied until activation. Background mode keeps the native process running; an app may implement its own bounded background read loop, but there is no implicit scheduler or retrying workflow.

MCP sessions idle out after two minutes. There is no automatic upstream tool-call retry. Mutations need an ID formatted as Unix milliseconds (13 digits), a hyphen, and 32 lowercase hex digits. Generate each operation's ID once and retain it across lost replies; pass the incoming ID for a single upstream write, or persist distinct IDs for multi-step writes. IDs expire after 24 hours. An instance-wide, bounded mutation ledger returns a known completed result or fences an uncertain outcome. It does **not** promise distributed exactly-once execution. For an uncertain result, inspect remote/local state before deliberately creating a new operation.

### Explicit workflow buttons

Contract version 3 advertises `workflow_tools` and their mandatory fixed argument. For example:

```json
{"mcp": [
  {"tool": "workflows_run_workflow", "arguments": {"name": "prepare_nautionette_release"}},
  {"tool": "workflows_list_runs", "arguments": {"workflow": "prepare_nautionette_release"}}
]}
```

The app calls these through `ctx.mcp`, never through browser credentials. `input` can vary between starts (for example `{"version":"minor"}`); constrain additional input fields in the grant when they must be fixed. Missing, empty or wildcard workflow scopes are rejected. Tool aliases, workflow editing/deployment/deletion and arbitrary run inspection remain denied. History reads are explicitly advertised read-only and need no mutation ID; starts require the retained incoming request ID. The existing ledger deduplicates known completed calls and fences uncertain outcomes. Never auto-retry a lost start with a fresh ID. A returned run ID means **started**, not succeeded; inspect scoped run history and the native workflow result before reporting completion. A grant authorizes that workflow's effects, so inspect it before granting it.

## Building and debugging

- Compiler infrastructure failures expose only a fixed `compiler_unavailable` code, the SDK fingerprint and a bounded stage (`docker`, `image`, `container`, `artifact`, `runtime_image`), not Docker/registry/authentication error text. A daemon initialization failure releases the compiler slot instead of blocking subsequent builds until restart.
- `POST /api/miniapps/validate` with `{manifest, compile: false}` validates a draft and its owner without creating any app/release. With `compile: true`, Rust source passes through the **same offline compiler** as a real publication. Success includes artifact bytes and the SDK fingerprint; failures include bounded compiler diagnostics with Rust source locations. Compiler containers/artifacts are removed; no runtime/image/data is created or existing app suspended. This tests syntax/types/SDK use, **not** initialization, migrations, frontend behavior or tool permissions. It shares the single compiler slot and may report busy—never change the app to work around that.
- `GET /api/miniapps/{id}/diagnostics` reads release/build errors, the latest 100 operation timings/outcomes, and the latest 100 mutation IDs/states. Histories are bounded; request/response data, argument values, URLs, credentials and native process logs are deliberately not exposed. A cached mutation replay is not a second runtime execution or a second diagnostic entry. Uncertain mutations remain fenced; inspect state before a new operation.
- Retrieve editable source with `GET .../releases/{revision}`. Read the current `revision` before publishing a fix; never overwrite a concurrent publication. Use `/invoke` for targeted application tests (writes are real and require retained mutation IDs).
- Settings → **App runtimes** reads per-app memory/limit, CPU sample/limit, process count, image size and SQLite usage plus measured totals. It refreshes every 15 seconds while visible; partial samples remain visibly incomplete instead of hiding unhealthy apps. CPU percentages use Docker CPU/system deltas, with 100% meaning one core; unavailable deltas show `—`. Image sizes and summed totals are logical image sizes, **not** physical Docker filesystem usage, and exclude shared compiler/cache layers. `/api/miniapps/overview` is the same authenticated read-only inventory for agents.
- Status, overview and diagnostics do not resume sleeping apps. The app info view exposes restart/rebuild/restore and the same resource/diagnostic data, while hiding permissions behind a disclosure. Metadata edits and opening/closing info do not reset the running iframe's UI state.

## Build and runtime boundary

The compiler is a source-fingerprinted, platform-owned Rust 1.91.1/Alpine image. Cargo dependencies are locked, vendored and prebuilt once. An app build has no network, credentials, Docker socket or persistent writable volume; it runs non-root with a read-only root, bounded tmpfs, one CPU, 1 GiB memory, PID limit and a 180-second compilation deadline. Only a bounded ELF artifact is copied into a platform-generated `FROM scratch` image. `scratch` is packaging, **not** the security boundary by itself.

Rust runtimes require **Docker Engine 28+**, because an ordinary `internal: true` Docker bridge still exposes services on the host's bridge address. Per-app networks require gateway mode `isolated`, disable IPv6 and connect only the app and broker. They have no published ports, external egress, gateway membership or other app members. Runtime bearer credentials authorize one fixed capability adapter, never broker administration. Missing internal authentication blocks backend publication. Older Docker engines fail closed for native apps without breaking chats/workflows/frontend-only apps.

### Hosts with exhausted Docker address pools

Docker's automatic allocator may reserve a whole `/16` per bridge and run out
with only a few dozen networks. Do not solve this by using host networking,
sharing app networks, weakening gateway isolation, or globally pruning networks.
Set **`MINIAPP_NETWORK_CIDR` on the broker** to a private pool reserved for this
one deployment, for example an unused `/24` verified against host routes and
existing Docker IPAM allocations. Use a **different pool for each environment**.
The setting accepts canonical RFC1918 IPv4 pools `/16` through `/24`; do not copy
an example range without checking it on your host.

New apps receive disjoint `/29` subnets (32 bridges per `/24`, enough for the
16-app instance limit). Allocation is serialized, checks existing allocations,
rejects overlap with unrelated/deployment-foreign networks, and relies on
Docker's final atomic IPAM overlap check. Exhaustion fails closed with an
operator-facing configuration error, never a fallback to a shared network.
Existing owned isolated networks are reused without readdressing or stopping
working apps. All isolation, IPv6 restrictions, capability drops, seccomp and
resource limits remain unchanged. No Docker daemon restart is required.

Blank retains Docker's automatic allocator for existing installations. Recreate
only the affected broker/application after changing the environment setting.
For promotion, reserve and validate a distinct production range **after staging
verification**; staging configuration is not implicitly copied to production.

Runtime controls:

| Resource | Bound |
| --- | --- |
| Instance app slots | 16, including archived/failed apps |
| Runtime RAM | 64 (default), 128 or 256 MiB; swap limit equals RAM |
| CPU / PIDs | 0.25 CPU / 32 PIDs |
| Persistent files | three files, each 16 MiB maximum; SDK main DB defaults to 12 MiB |
| Temporary filesystem | 8 MiB tmpfs, noexec/nosuid/nodev |
| Logs | one 256 KiB rotating file |
| Release / Rust source | 320 KiB / 64 KiB |
| Operation input / response | 48 KiB / 64 KiB |
| Mutation ledger | 4,096 instance-wide records, approximately 24-hour retention |
| Idle on-demand suspension | five minutes |

Processes run as 65532, with all capabilities dropped, no-new-privileges and a tightened Docker seccomp allowlist. RLIMIT_FSIZE alone is insufficient: file preallocation, filesystem ioctls and extended attributes can allocate beyond apparent EOF. The runtime policy denies these allocation paths and io_uring while retaining Docker's default-deny syscall baseline. The sole allowed `ioctl` command is `FIONBIO` (`0x5421`), which toggles non-blocking mode and is required by Rust's bounded `TcpStream::connect_timeout` for SDK callbacks. Arbitrary ioctl commands, including file cloning/allocation, remain denied. Native CI exercises a real SDK → broker → authenticated test-backend round trip, not just a blocking socket probe. The vendored Apache-2.0 profile is from [moby/profiles `seccomp/v0.1.0`](https://github.com/moby/profiles/tree/seccomp/v0.1.0/seccomp); its adjacent LICENSE is retained. Containers share the host kernel: this is not a VM-grade adversarial-code isolation claim.

A single build slot bounds compiler concurrency. Requests and capability callbacks have separate bounded slots and total wall-clock network deadlines. Mini-app reconciliation runs off the worker-control monitor thread so unhealthy app processes cannot delay ordinary worker recovery. Interrupted candidates are discarded, never implicitly promoted. Interrupted deletion remains journaled and revoked until cleanup succeeds.

## Deployment, measurements and operations

Compose mounts a separate `miniapps` volume **only** on the broker. `backend-data` retains source metadata and the mutation ledger. The broker derives host mount locations from its own deployment-owned volume; callers cannot select host paths or networks.

CI builds/tests the compiler and actual scratch runtimes, then publishes the exact SDK fingerprint alongside the tested agent images. `images/source-fingerprints.json` records those canonical identities and is copied separately into the broker: deployment tools such as Coolify inject build arguments into Dockerfiles, which must not change the registry tag the broker requests. After changing an agent image or SDK source, regenerate the lock with `python services/docker-broker/nautionette_docker_broker/image_context.py --write-lock`; CI checks it against the unmodified repository. Packaged locks are validated and invalid ones fail closed. Local/source execution without a packaged lock retains dynamic context hashing. The broker pulls the locked fingerprint when `AGENT_IMAGE_REGISTRY_PREFIX` is configured; this is the existing **image-name prefix** (for example `ghcr.io/stromflix/nautionette/pi-`), so the compiler is pulled as its sibling `ghcr.io/stromflix/nautionette/miniapp-builder:<fingerprint>`, never from a `pi-/miniapp-builder` subdirectory. Local development can build the bundled context. The shared compiler/cache can consume substantially more disk than an individual app and is not included in per-app image measurements. Do not claim a net reduction from old declarative dashboards without measuring the full stack.

Runtime containers and their isolated bridges survive broker replacement during deployments. Before every health check or RPC, the broker validates the app network's ownership/isolation and joins it with the `miniapp-host` SDK callback alias if needed. This also covers on-demand containers waking after a deployment; no rebuild, data reset or mutation replay is required. Native Docker CI replaces the broker while retaining an app, checks live/sleeping reconnection and SQLite persistence, and verifies the real SDK callback after network detachment.

`GET .../status` reports measured container memory, image bytes, SQLite bytes and enforced limits. On-demand containers release process memory after suspension; background mode intentionally retains it. Each successful publication briefly needs both old and candidate SQLite copies (up to 96 MiB/app). Successful activation deletes the old runtime, image tag and data copy; rollback retains current data. Retained source lives in SQLite, not an indefinite collection of Docker images. If an operator prunes an archived app's image, use **Rebuild code** to reconstruct it without losing data.

Back up **both** `backend-data` and the broker's `miniapps` volume. Archive apps (or stop backend/broker app operations) before a coordinated filesystem backup; copying a live SQLite main file without its WAL is not a consistent backup. Never delete or share the volume between independently running deployments. App credential journals are mode 0600 and are not an export format.

The staging Deploy job runs `scripts/check_miniapps.py` with environment-scoped credentials: it dry-compiles a draft without publication, creates a uniquely named disposable app, checks actual compilation/activation, explicit release pins, passive resource inventory, bounded diagnostics, request deduplication, container-replacement persistence, failed-health rollback, source restore, archive/revocation and unarchive, records resource measurements, then removes only its own app. It also creates a uniquely named deterministic workflow and app, verifies a real scoped start, input forwarding, deduplication, completed result and read-only scoped history through MCP, then removes only those temporary resources. No release workflow, model call or notification is triggered. This test never runs against production on release promotion. Chromium and WebKit suites cover bridge isolation, forbidden navigation, mobile navigation, pins and archive UI. Production promotion remains a separate release action.
