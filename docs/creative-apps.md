# Native creative applications

Nautionette's default agent image includes the entire current ArtCraft catalog:
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

Every app defaults to **enabled** and the version bundled in the image.

- Enable/disable apps for **new turns**.
- **Check releases** lists recent stable releases from the app's fixed official
  `storytold/<app>` GitHub repository that have a native CLI asset with a SHA-256
  digest for this server architecture. Enter an exact version and **Apply
  version**. Older exact pins are resolved through their release tag.
- Updates run asynchronously. Settings continues to show the active version while
  the release downloads, extracts and passes headless MCP initialization/tool
  discovery. A successful verification atomically selects the new installation;
  any failure keeps the previous active version and displays a retryable error.
- **Use bundled** switches back to the current image default without a download.
- Updates do not rebuild the agent image or replace a running container's
  binaries, including compatible queued messages steered into that same live
  session. A new agent run/container receives the updated selection. An override
  remains pinned when the base image is upgraded. A bundled selection
  follows future image defaults.

Settings writes use optimistic revisions; stale saves and overlapping updates to
one app are rejected. At most two updates may be pending. Interrupted updates are
marked failed during backend startup, leaving the working selection intact.
There is no automatic update/background polling of upstream releases.

## Packaging and runtime boundary

`images/pi-base/artcraft/catalog.json` is the shared, pinned catalog, including
Linux x86_64 and aarch64 release archive digests. `install.py --bundle` installs
all CLIs, preserving upstream release documentation, licenses and font notices.
It discards desktop/WASM payloads. VectorCraft uses `mcp --headless`; PdfCraft uses
its dedicated CLI archive. Image builds verify all archive checksums and probe
all engines before the image can pass CI.

The backend records app selections in `settings` under `artcraft.app.<app>`.
Immediately before a turn starts it snapshots enabled apps and exact release
identities. The broker validates this data. Updated binaries are immutable,
deployment-namespaced Docker volumes, ownership/release/digest checked and
mounted **read-only** at `/opt/artcraft/<app>`. Bundled binaries come from the
pinned agent image; the agent's existing writable root-filesystem policy is
unchanged. No global app MCP server or shared drawing is created.

Each engine starts lazily over newline-delimited stdio MCP in the turn's working
directory; at most **three** may be open at once. Engines inherit no gateway
credentials, private output grants or package secret environment variables.
They share the agent sandbox's OS trust domain, resource budgets, selected
projects and allowed network access; this is **not** additional per-app isolation.
Disabling an app hides its native tools, not its bundled executable from an agent
that has shell access.

Application calls have a 180-second timeout and a 32-MiB protocol frame limit.
Cancellation, protocol failure, crash or timeout stops the process group and
fails pending calls. Mutations are never automatically retried. Recovery requires
an explicit new session and restoration of the last saved source; unsaved work
can be lost. Session shutdown/container cleanup reaps disposable processes.

Version installation is a fixed authenticated broker verb, not a general shell
or container API. Installers receive only the exact release specification and
their own artifact volume: no Docker socket, projects, backend token or app data.
They run on a separate bridge, with dropped capabilities, no-new-privileges,
read-only root, bounded tmpfs/memory/CPU/PIDs, download/extraction limits and a
300-second wait. Release archive paths and selected regular files are checked.
The app is executed only after the archive digest matches. Updates trust the
upstream publisher's native code; digests are integrity checks, not an independent
security audit or publisher signature.

Successful artifacts are retained so existing turns cannot lose a mounted
version. Stale helper containers/networks are reaped; artifacts are not blindly
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
