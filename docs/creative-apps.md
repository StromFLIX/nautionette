# Native creative applications

Nautionette prepares the entire current ArtCraft catalog at deployment:
PhotoCraft, VectorCraft, FilmCraft, LightCraft, PdfCraft, EffectCraft, DesignCraft,
FigCraft, WordCraft, SoundCraft, DeckCraft, CADCraft and GridCraft.

These are **native headless CLIs**, not embedded desktop applications or browser
WASM editors. Execution happens inside the existing server-side agent sandbox.
The browser/mobile client only displays committed previews and downloads in
**Outputs**. Disconnecting the browser does not stop the agent.

## Using an app

Ask normally, for example:

> Use CADCraft to make a dimensioned 100 × 60 mm mounting plate with four corner
> holes. Publish the editable DXF and a PNG preview to Outputs.

In another turn:

> Increase the mounting plate's hole diameter to 6 mm and update the output.

The agent has three local tools, independent of the remote-MCP tool selection:

- `craft_apps`: list enabled apps and the turn's pinned versions; explicitly
  open/close a session. Listing does not start an application.
- `craft_tools`: discover an app's actual MCP tools. Search/paginate tool names,
  then request a name to retrieve its full parameter schema. Most apps also have
  a command registry reachable through their own list/execute tools.
- `craft_call`: invoke a discovered tool. Calls to one engine are serialized.
  Render images remain image blocks, not base64 text in model context. Large text
  results are saved to local files with a bounded excerpt.

The default agent instructions explain saving, rendering, inspecting and
publishing. `publish_output` commits the editable **source** and useful exports
and previews durably; `read_output` restores sources in later turns. Native
in-memory state and local files alone are **not** durable. See
[chat-outputs.md](chat-outputs.md) for authorization, file limits and revisions.

## Settings → Creative apps

Every app defaults to **enabled** and the pinned deployment version.

- Latest stable versions and **Update available / Up to date / Check failed**
  badges appear immediately from persisted discovery results, with outdated apps
  first. Discovery checks all apps hourly even when Settings is closed. Opening
  the pane triggers an asynchronous check if the cached status is older than an
  hour. **Check for updates** refreshes it manually. An outage is never presented
  as up to date; each failed lookup has a retry action.
- **Update to …** queues one app. **Update all** queues all known newer verified
  stable releases, including disabled apps (which remain disabled). Already
  queued apps, failed/unknown release checks and newer manually selected versions
  are skipped. Bulk actions use the exact discovered release/digest, not a mutable
  `latest` download URL. Discovery accepts only fixed official `storytold/<app>`
  assets for the server architecture with a SHA-256 digest; prereleases are excluded.
- **Automatically update all apps** is opt-in and off by default. It checks and
  installs newer stable versions hourly, without an open Settings page. It includes
  disabled apps and replaces manual pins, but never downgrades newer versions.
  Switching it off stops future automatic enqueueing, not already queued work.
- **Version options & documentation** contains exact version entry, release lists,
  and **Restore default**. Older exact pins are resolved through their release tag.
  The API retains `version: "bundled"` for default restoration compatibility.
- Settings keeps showing the working version while updates queue, download,
  extract and pass headless MCP initialization/tool discovery. Verification
  atomically selects an artifact; failures keep the previous active version and
  show an error. Retry failed apps individually or with **Update all**. Automatic
  mode retries on the next hourly check, not continuously.
- Enable/disable and version changes apply to **new turns**. No update replaces
  files mounted in a running container, including messages steered into that same
  live session. Manual overrides remain pinned across deployments unless automatic
  updating is enabled. Default selections follow future deployment defaults.

App and auto-update preference writes use optimistic revisions. A persistent
queue allows all 13 updates while running **at most two installers** across
manual, bulk and maintenance dispatchers. Queued/in-progress updates resume after
backend restart using the same pinned release and installation ID. The broker
can verify/reuse a completed artifact after a lost response. Release checks have
three bounded concurrent requests; partial failures do not block healthy apps.
The update filter and attention filter make pending work/failures easy to find.

## Packaging and runtime boundary

`images/pi-base/artcraft/catalog.json` is the shared, pinned catalog, including
Linux x86_64 and aarch64 release archive digests. The Pi image contains only this
catalog and the fixed installer, **not the native app binaries**. After acquiring
the agent images, the broker prepares all 13 default artifacts before reporting
agent/image readiness. The broker downloads missing defaults as bounded, checksum-verified archives;
fixed offline installer containers extract/probe them. Existing defaults are
reverified offline after broker restart. Corrupt/incomplete
unreferenced artifacts can be removed and reinstalled, never overwritten in place;
Docker refuses removal of any artifact referenced by a container. A failed
preparation keeps readiness degraded and is retried by reconciliation. No default
release discovery or app download occurs inside an agent turn.

The installer preserves release documentation, licenses and font notices, and
discards desktop/WASM payloads. VectorCraft uses `mcp --headless`; PdfCraft uses
its dedicated CLI archive. CI prepares volumes using the real broker installer,
reverifies them without network, then runs the full native app suite offline as
an unprivileged user with all capabilities dropped and volumes read-only.

The backend records app selections in `settings` under `artcraft.app.<app>`.
Immediately before a turn starts it snapshots enabled apps and exact release
identities. The broker validates this data. Updated binaries are immutable,
deployment-namespaced Docker volumes, ownership/release/digest checked and
mounted **read-only** at `/opt/artcraft/<app>`. Defaults use a deterministic ID
from the exact app/version/architecture/digest, so image upgrades cannot redirect
queued turns to newer binaries. Updated versions retain their immutable IDs.
Legacy queued jobs without an ID resolve their exact prepared default artifact,
never the current default. Missing artifacts fail rather than creating an empty
volume. Retained overrides are also verified offline on first use after broker
restart. The agent's existing root-filesystem policy is unchanged. No global app
MCP server or shared drawing is created.

Each engine starts lazily over newline-delimited stdio MCP in the turn's working
directory; at most **three** may be open at once. Engines inherit no gateway
credentials, private output grants or package secret environment variables.
They share the agent sandbox's OS trust domain, resource budgets, selected
projects and allowed network access; this is **not** additional per-app isolation.
Disabling an app hides its native tools and omits its artifact from new agents;
it is not a general isolation boundary against an agent with shell access.

Application calls have a 180-second timeout and a 32-MiB protocol frame limit.
Cancellation, protocol failure, crash or timeout stops the process group and
fails pending calls. Mutations are never automatically retried. Recovery requires
an explicit new session and restoration of the last saved source; unsaved work
can be lost. Session shutdown/container cleanup reaps disposable processes.

Version installation is a fixed authenticated broker verb, not a general shell
or container API. Installers receive only the exact release specification and
their own artifact volume: no Docker socket, projects, backend token or app data.
All installers use **network none**, dropped capabilities, no-new-privileges,
read-only root, bounded tmpfs/memory/CPU/PIDs, extraction limits and a 300-second
wait. No new Docker bridge/address pool is required, even for first deployment.
The broker streams only allowlisted HTTPS archives (at most 256 MiB each), checks
the pinned digest, and stages the archive plus a fixed internal helper through
Docker's archive API. It never extracts or executes upstream content itself.
The offline helper independently checks the digest, removes its staging files,
extracts safely, probes the CLI and only then publishes the manifest. The two
installation slots also bound temporary broker disk usage; archives/tar streams
are file-backed rather than accumulated in memory. Reconciliation reaps only
old, empty, deployment-owned legacy installer networks, never application
networks or artifact volumes. Release archive paths and selected regular files are checked.
The app is executed only after the archive digest matches. Updates trust the
upstream publisher's native code; digests are integrity checks, not an independent
security audit or publisher signature.

Default and updated artifacts are retained so existing turns cannot lose a mounted
version. Back up these deployment-namespaced volumes along with backend settings;
restoring settings alone does not restore selected binaries. Moving roughly 1 GB
out of the image does not eliminate the binaries' volume storage, but avoids the
large compressed app image layer and repeated whole-bundle updates. Stale helper containers/networks are reaped; artifacts are not blindly
removed after an ambiguous HTTP result. A deployment is capped at 128 artifacts.
An administrator must reclaim **unreferenced** ArtCraft volumes if this cap is
reached; there is currently no automatic artifact GC or Settings deletion button.
Do not remove a selected artifact or one referenced by an active container.

## Tests and limitations

CI runs the real shipped CLIs offline as the unprivileged project user. Tests
cover all 13 MCP catalogs, actual CAD creation/rendering/save/reopen and a 6-mm
revision after source publication/restoration, WordCraft DOCX/PDF/PNG export,
GridCraft XLSX/formula restoration, and real Pi's model → native tool → Outputs
publication loop. Unit tests cover lifecycle/serialization/cancellation, release
validation, unsafe archives, installer isolation, ownership-checked mounts,
atomic activation/failure/restart recovery, and desktop/mobile Settings controls.

These are early upstream apps, not guaranteed feature/format parity with their
commercial equivalents. UI-only commands (screenshots/clicks/window controls)
require a desktop and are not usable here. Font/rendering/format behavior depends
on the selected release. Large media jobs share the agent's existing resource and
turn-time limits. CI exercises Linux x86_64; aarch64 release pins are included but
are not a claim of ARM runtime test coverage. There are no embedded editors,
live collaboration service, independently persistent application compute or
transparent crash recovery in this slice. The fuller workspace design remains
in [chat-app-workspaces.md](chat-app-workspaces.md).
