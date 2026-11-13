# Dependency audit — 2026-11-10

Scope: every committed dependency manifest/lock, Dockerfile, Compose image,
GitHub Action, native ArtCraft release, and bundled model reference. Latest
**stable** releases were checked against npm, PyPI, crates.io, upstream GitHub
releases, Docker registries, and the model repository. This is a compatibility
refresh, not a claim that every ecosystem's newest major can safely be deployed.

## Where the complete inventory lives

| Layer | Authoritative inventory |
| --- | --- |
| Python services and developer tools | Root/member `pyproject.toml` files and `uv.lock` (99 workspace/external entries) |
| Separate CPU speech appliance | `services/stt/requirements.in` and hash-locked `requirements.txt` |
| Web, Electron, Android and iOS clients | `services/frontend/package.json` and `package-lock.json`, including optional/peer/transitive packages |
| Rust compiler and statically bundled SQLite | `services/miniapp-sdk/Cargo.toml` and `Cargo.lock` |
| PostgreSQL privilege helper's Go modules | `infra/postgres/go.mod` and `go.sum`; checksum-pinned gosu source archive |
| Container runtimes and external executables | All 15 Dockerfiles, `docker-compose.yaml`, `images/agent-images.hcl` |
| Creative applications | `images/pi-base/artcraft/catalog.json`: exact versions and Linux x86_64/aarch64 SHA-256 digests |
| Build/release tooling | `.github/workflows/*.yml`; floating major action tags already resolve to the current compatible action releases |
| Experimental Waymark reproduction | `scripts/spikes/waymark/requirements.txt` and `compose.yaml`; intentionally separate from production |
| Model artifacts | Search model revision in `search_index.py`; Moonshine model selection/cache in `services/stt/server.py` |

CI now publishes **SPDX JSON SBOM artifacts for every built service image, both
agent images, the mini-app compiler, PostgreSQL, MaxIO and Temporal UI**. These
record the packages actually present in images, including distribution packages
and nested Python/npm/Rust dependencies, rather than pretending a Dockerfile's
explicit install list is a complete OS inventory. Grype JSON reports accompany
those images. Container vulnerability reports are report-only: upstream/unfixed
findings remain visible, not silently allowlisted. npm's audit is a blocking CI
check, including desktop development dependencies.

SBOMs describe the tested image/platform, not all other CPU architectures or an
already-running deployment. ArtCraft binaries live in separately verified,
immutable volumes; their version/digest catalog is a separate inventory. Projects,
user-installed Pi packages, authored workflow dependencies, and dynamically
configured MCP servers are runtime data, not repository-controlled dependencies;
this audit does not replace or upgrade arbitrary user software.

## Updates applied

### Python

The complete workspace graph was re-resolved with `uv lock --upgrade`. Seventeen
packages changed; all lockfile hashes/platform variants were regenerated:

| Package | Before | After |
| --- | --- | --- |
| anyio | 4.15.1 | 4.16.0 |
| boto3 / botocore | 1.43.122 | 1.43.127 |
| click | 8.5.0 | 8.5.1 |
| fastapi | 0.162.0 | 0.162.3 |
| fastembed | 0.8.1 | 0.9.1 |
| filelock | 4.2.4 | 4.2.10 |
| Pillow | 12.3.0 | 13.0.0 |
| pydantic | 2.14.2 | 2.14.3 |
| pydantic-core | 2.50.2 | 2.50.4 |
| pytest-asyncio | 1.4.1 | 1.4.1.post1 |
| python-multipart | 0.0.32 | 0.0.33 |
| ruff | 0.17.3 | 0.17.4 |
| sse-starlette | 3.6.0 | 3.7.0 |
| typing-inspection | 0.4.4 | 0.4.5 |
| websockets | 17.3 | 17.4 |
| wrapt | 2.5.1 | 2.5.2 |

FastEmbed 0.9.1 explicitly supports Pillow 13. Its dependency range was deliberately
advanced; the search index identity changes from `fastembed-0.8` to
`fastembed-0.9` so old vectors are rebuilt rather than mixed with a changed encoder.
The model itself is unchanged. Real pinned-model offline semantic retrieval was
verified, as were the image and output tests.

The speech appliance was independently recompiled with `uv pip compile --upgrade
--generate-hashes --python-version 3.13`. Its FastAPI/websockets pins and transitive
graph are current; `uv pip list --outdated` in its isolated environment is empty.
Moonshine Voice 0.1.6 remains the latest stable package. Its existing native model
selection/cache behavior is retained; no unreviewed model substitution was made.

### Frontend and agent runtime

| Direct package | Before | After |
| --- | --- | --- |
| marked | 18.1.2 | 18.2.0 |
| quasar | 2.35.2 | 2.35.3 |
| vue | 3.5.44 | 3.5.45 |
| @playwright/test and agent Playwright | 1.64.1 | 1.65.0 |
| fake-indexeddb | 6.2.6 | 6.2.8 |
| sass-embedded | 1.107.0 | 1.108.1 |
| vite | 8.3.5 | 8.3.7 |
| @earendil-works/pi-coding-agent | 1.4.0 | 1.5.2 |

All other direct frontend dependencies were checked and are current: Capacitor
core/CLI/Android/iOS 8.6.0 and its app/filesystem/keyboard/share plugins, Dagre,
Quasar extras/Vite plugin, Vue Flow core/background, css-tree, DOMPurify,
highlight.js, idb, Vue Router, Vue's Vite plugin, Electron and electron-builder.
The npm transitive graph was also refreshed, not just package.json.

`npm audit` initially reported eight moderate findings. Updated glob/rimraf resolve
two; **version-qualified** overrides of electron-builder's minimatch 10 and
@electron/universal's minimatch 9 to 11.0.5 resolve the remaining findings.
Do not use a global minimatch override: ASAR/glob 7 require the legacy callable
API. Regression tests exercise actual Electron file filtering, universal-binary
matching, ASAR creation and native-module unpacking. The resulting npm audit has
**zero known findings**, including development dependencies. Do not follow npm's
suggestion to downgrade electron-builder to 25.1.0.

Pi's exact runtime guard moves with its Docker pin. Public SDK imports and real
package/model/transport/session RPC contracts are tested against Pi 1.5.2.

### Images and native code

| Dependency | Before | After / decision |
| --- | --- | --- |
| Copied uv executable | 0.13.6 | 0.13.7 |
| Copied static BusyBox (gateway and collector health checks) | 1.37.0-musl | 1.38.0-musl |
| OpenTelemetry Collector contrib | 0.164.0 | 0.165.0 |
| Temporal UI (debug profile) | 2.57.0 | 2.58.1 |
| Rust builder | 1.91.1-alpine3.22 | 1.99.1-alpine3.22 |
| rusqlite | 0.37.0 | 0.41.1 |
| Bundled libsqlite3-sys | 0.35.0 | 0.39.1 (SQLite 3.54.0) |
| Node | 24 | Retain supported LTS line; refresh floating image (24.22.2 observed) |
| Python | 3.13 (3.13.11 in the old uv image) | Explicitly use 3.13.18-slim-bookworm plus copied uv 0.13.7 |
| nginx | 1.31.8-alpine | Already current; refresh distribution packages |
| PostgreSQL | 17-alpine | Retain data-compatible major; refreshed tag supplies current 17.x patch |
| MaxIO | 0.4.2 | Already latest; derived image patches its Debian packages, retaining maxio:maxio |
| agentgateway | v1.7.0 | Already latest; CI binary and container stay synchronized |
| Container npm | 11.19.1 | 11.21.2 (supported npm 11 line, not npm 12) |
| PostgreSQL gosu | 1.19, built with Go 1.24.6 | Checksum-pinned 1.20 source rebuilt with Go 1.27.3 |
| gosu modules | moby/sys/user 0.1.0; x/sys 0.1.0 | Locked 0.5.0 and 0.50.0 respectively |

Cargo's entire graph was updated, including cc, foldhash, hashbrown, hashlink,
itoa, smallvec and syn. The builder still vendors and tests the lock **offline**;
compiler/runtime isolation is unchanged. Rebuilding the compiler does not rewrite
already-published mini-app executables: existing apps retain their embedded
SQLite until republished/restored as a new release through the supported API.

Every repository-controlled Debian-based build stage now upgrades installed distribution packages
before installation; Alpine application/compiler stages run `apk upgrade`.
The Temporal wrapper also upgrades its existing Alpine branch, restoring `USER
temporal` without changing server/schema versions. PostgreSQL and MaxIO use
namespace-separated local build tags but retain the same data mounts, entrypoints,
commands and users. CI tests real S3 uploads/restoration against the patched MaxIO
image, and initializes a volume with upstream PostgreSQL 17 before verifying its
data and non-root process survive startup with the patched PostgreSQL image.
CI service builds and agent Bake builds pull base images. Static copied binaries,
Rust SQLite and ArtCraft require their own explicit updates and are not patched
by apt/apk. An unchanged cached RUN layer does **not** refresh package indexes:
for a deliberate subsequent OS-only refresh use `docker compose --profile images
build --pull --no-cache`, rerun the test gates and publish source-fingerprinted
agent images. Never prune live named volumes to refresh packages.

### Creative apps

Both architecture hashes were taken from the upstream release asset digests.
Every x86_64 archive was downloaded, checksum-verified, safely extracted and
passed a real headless MCP initialize/tools-list probe; the agent's real native
schema suite and CAD/source/preview/restore, Word and Grid round trips also passed.

| App | Before | After |
| --- | --- | --- |
| PhotoCraft | 0.17.0 | 0.22.0 |
| VectorCraft | 0.18.0 | 0.23.0 |
| FilmCraft | 0.14.0 | 0.19.0 |
| LightCraft | 0.15.0 | 0.20.0 |
| PdfCraft | 0.15.0 | 0.20.0 |
| EffectCraft | 0.18.0 | 0.23.1 |
| DesignCraft | 0.14.0 | 0.19.0 |
| FigCraft | 0.12.1 | 0.17.0 |
| WordCraft | 0.12.0 | 0.17.0 |
| SoundCraft | 0.14.0 | 0.19.0 |
| DeckCraft | 0.12.0 | 0.17.0 |
| CADCraft | 0.14.0 | 0.19.0 |
| GridCraft | 0.13.0 | 0.18.0 |

Immutable old selections are not overwritten. New defaults use new content-addressed
volumes; explicitly pinned user selections and queued turns keep their exact release.
aarch64 checksums were verified against release metadata, not executed locally.

## Findings from scanning the actual images

The first scan found problems that manifest-only outdated checks missed:

- The floating `uv:python3.13-bookworm-slim` image still contained **Python
  3.13.11**, despite pulling it fresh. All four Python services now use the
  official **3.13.18** runtime with an independently pinned uv executable.
- Node 24 bundled **npm 11.19.1**, which contained vulnerable brace-expansion
  5.0.9 and undici 6.28.0. npm **11.21.2** contains 5.0.12/6.29.1 and patches
  those findings without a major CLI upgrade. The upstream npm bundle still
  includes minimatch 10; do not confuse that upstream tooling report with the
  separately overridden, zero-audit-finding frontend application graph.
- Upstream **MaxIO** and **Temporal auto-setup** images contained older
  distribution packages. Build-time wrappers upgrade them in their existing
  Debian/Alpine distributions and restore the original non-root users.
- PostgreSQL itself was current **17.11**, but its unowned, downloaded **gosu**
  executable embedded **Go 1.24.6**. Even the new gosu 1.20 release binary uses
  that compiler. Rebuilding checksum-pinned 1.20 source and locking updated Go
  modules fixes the underlying helper packages without changing PostgreSQL's
  major version or substituting a different privilege-dropping program.

Scanners also report upstream findings with no newer compatible image/package,
and generic CPE/embedded-stdlib matches do not establish exploitability. In
particular, the old Temporal server/tools have embedded Go modules that apk cannot
patch; upgrading that server is the separate migration below. Temporal UI's latest
2.58.1 image also embeds an older x/net than its advisory's fix. Collector 0.165.0
and current Debian packages have additional upstream/unfixed findings. These
reports are deliberately retained in CI artifacts rather than suppressed or
misrepresented as a vulnerability-free stack. Private networks and existing
authentication/isolation remain unchanged. Review the exact latest artifact for
package versions, advisory IDs and fix availability; counts can duplicate one
vulnerability across several binaries.

## Deliberate holds and follow-up work

- **Temporal server:** `temporalio/auto-setup:1.29.7` is current in its deprecated
  image line, not the current maintained server. Upstream now ships
  `temporalio/server:1.32.2` and `admin-tools`; an `auto-setup:1.32.2` tag does not
  exist. A safe migration needs backups, explicit schema management, consecutive
  minor-version upgrades, namespace initialization, schedule/replay/resumption and
  rollback tests. Before 1.31 the core/visibility schemas must reach v1.19/v1.14.
  This is a separate operational migration, not an unattended tag substitution.
  The [migration runbook](temporal-upgrade.md) now provides an opt-in maintained
  server overlay, guarded schema tooling and a real PostgreSQL upgrade/restore
  CI rehearsal. Live migration remains pending; the base pin is intentionally
  unchanged until each environment completes its maintenance procedure.
- **PostgreSQL 18:** requires a real major-version data migration (and changed
  official-image data-directory conventions). Keep major 17 for existing volumes.
- **huggingface-hub 2.4.0:** FastEmbed 0.9.1 requires `<2`; retain latest compatible
  1.33.0 rather than bypassing the resolver.
- **nexus-rpc 1.5.0:** Temporal SDK 1.35.0 requires `<1.5`; retain 1.4.0.
- **python-dateutil 4.0.0:** botocore requires `<3`; retain 2.9.0.post0.
- **Node 26 / Python 3.15 / Alpine distribution jumps:** supported existing runtime
  lines are not obsolete packages. Major runtime/ABI upgrades need their own
  platform matrix. npm is patched independently within major 11; major 12 is not
  needed to obtain the fixed brace-expansion/undici versions.
- **Waymark spike:** its pins reproduce the dated experimental report and are not
  installed by any production build. Preserve those evidence pins rather than
  silently invalidating the published benchmark; rerun the experiment separately
  if evaluating a newer Waymark.
- **Models:** the pinned search model revision is still current upstream.
  Model quality changes and Moonshine model reproducibility are separate from
  library patching and require retrieval/speech validation.

## Verification and release policy

Use the exact commit's CI artifacts and Deploy run as the deployment evidence.
The sandbox has no Docker daemon and lacks WebKit OS libraries: container builds,
real Docker isolation and WebKit run on GitHub runners, not fabricated locally.
Only CI-successful commits go to staging; production remains release-gated.

Local checks include workspace lint/format and full pytest, frontend and website
unit tests, Vite production build, real Pi RPC tests, hash-locked CPU speech tests,
Rust tests, real offline semantic retrieval and native ArtCraft tests. `pip-audit`
on the resolved Python workspace found no known vulnerabilities. CI additionally
builds actual images, checks Compose, exercises MaxIO, validates gateway OAuth,
mini-app isolation and browser contracts, and verifies staging components plus
mini-app/OAuth/dictation/creative-app readiness before production eligibility.
