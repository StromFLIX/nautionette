# Dependency audit — 2026-11-13

This audit rechecked upstream registries/releases on **2026-11-13 UTC**; it does
not inherit the November 10 audit's freshness claims. Scope includes every
workspace/transitive Python dependency, the independently hash-locked speech
appliance, web/mobile/Electron npm graph, Rust/SQLite, gosu/Go modules, all
Dockerfiles/Compose/Bake contexts and copied executables, Actions, ArtCraft's two
architectures, models, source fingerprints and upgrade/deployment tooling.
Runtime user projects, authored apps/workflows, installed Pi packages and configured
MCP services are not repository dependencies and were not changed.

## Updates

| Layer | Previous | Updated |
| --- | --- | --- |
| annotated-doc (workspace and speech) | 0.0.5 | 0.0.6 |
| boto3 / botocore | 1.43.127 | 1.44.3 |
| FastAPI (workspace and speech) | 0.162.3 | 0.162.7 |
| Temporal Python SDK | 1.35.0 | 1.36.0 |
| nexus-rpc | 1.4.0 | 1.5.0 |
| ONNX Runtime | 1.31.1 | 1.32.0 |
| pytest-asyncio | 1.4.1.post1 | 1.5.0 |
| Ruff | 0.17.4 | 0.17.5 |
| Capacitor core/CLI/Android/iOS | 8.6.0 | 8.6.2 |
| Quasar | 2.35.3 | 2.35.4 |
| Vue Router | 5.4.0 | 5.5.1 |
| Vue Vite plugin | 6.0.10 | 6.0.11 |
| Electron | 45.2.3 | 45.3.2 |
| Sass embedded | 1.108.1 | 1.108.2 |
| Vite | 8.3.7 | 8.3.8 |
| Copied uv (all six consumers) | 0.13.7 | 0.13.8 |
| Rust builder | 1.99.1-alpine3.22 | 1.100.0-alpine3.22 |
| rusqlite | 0.41.1 | 0.42.0 |
| libsqlite3-sys / bundled SQLite | 0.39.1 / 3.54.0 | 0.40.0 / 3.54.1 |
| cc | 1.11.1 | 1.11.2 |
| gosu compiler and go.mod | Go 1.27.3 | Go 1.27.4 |

Both Python graphs were re-resolved, not manually edited. Speech retains generated
hashes and its separate install environment. The full npm lock was refreshed,
including optional native platform packages, Babel, Rolldown 1.2.19, tar 7.5.23,
glob 14.0.6 and minimatch 11.0.8. Existing **version-qualified** Electron minimatch
overrides remain: the legacy callable minimatch used by ASAR must not be globally
replaced. Lock integrity/checksum verification stays enabled in all ecosystems.

The [SDK 1.36 release notes](https://github.com/temporalio/sdk-python/releases/tag/1.36.0)
were reviewed: Python 3.10 support was dropped (we run 3.13); workflow clock calls
are restricted; default poller enrollment and activation batching changed. No
repository workflow uses the newly restricted clock calls. Native PostgreSQL
migration/history/replay tests remain a required CI gate. The former nexus-rpc
hold is **removed**: SDK 1.36 permits 1.5.0. This is not a Temporal server/schema
version change. Existing migration/backup/fence/rollback contracts are unchanged.

[rusqlite 0.42](https://github.com/rusqlite/rusqlite/releases/tag/v0.42.0) includes
SQLite 3.54.1 and registration/session safety fixes. The actual bundled header
was checked, and real authenticated RPC/SQLite tests passed. Rust and Go downloads
used upstream SHA-256 metadata; Cargo regenerated and verified registry checksums.
The Docker Hub tags for both new compiler images exist. Gosu remains the same
checksum-pinned 1.20 source and the same privilege/entrypoint contract; its modules
moby/sys/user 0.5.0 and x/sys 0.50.0 remain current. Go 1.27.4 addresses
**GO-2026-6859**, observed in the old PostgreSQL image's actual gosu binary.
Updating this compiler does **not** rewrite SQLite or other code embedded in
already-published user miniapps. Only a deliberate supported publication/restore
rebuilds one of those apps; none were automatically republished.

### Native creative apps

| App | Previous | Updated |
| --- | --- | --- |
| PhotoCraft | 0.22.0 | 0.23.1 |
| VectorCraft | 0.23.0 | 0.24.1 |
| FilmCraft | 0.19.0 | 0.20.1 |
| LightCraft | 0.20.0 | 0.21.1 |
| PdfCraft CLI | 0.20.0 | 0.21.1 |
| EffectCraft | 0.23.1 | 0.24.1 |
| DesignCraft | 0.19.0 | 0.20.1 |
| FigCraft | 0.17.0 | 0.18.1 |
| WordCraft | 0.17.0 | 0.18.1 |
| SoundCraft | 0.19.0 | 0.20.1 |
| DeckCraft | 0.17.0 | 0.18.1 |
| CADCraft | 0.19.0 | 0.20.1 |
| GridCraft | 0.18.0 | 0.19.1 |

All 26 Linux archive digests in `images/pi-base/artcraft/catalog.json` match the
release asset API metadata. Every x86_64 archive was downloaded, checksum-verified,
safely extracted with the repository installer and probed using real headless MCP
initialize/tools-list. Native schema tests and CAD editable-source/PNG publication,
fresh-session restore/revision, Word DOCX/PDF/PNG and Grid formula round trips
passed. Release notes include parser/resource-boundary hardening, format fixes
and the new cohort's updater repair; we still extract only CLI/docs/licenses, not
desktop updaters. aarch64 metadata was checked, **not executed**. Immutable old
selections/volumes and queued turns keep their existing exact versions.

## Actual image evidence, not just pins

Downloaded and inspected **all 32 SPDX/Grype JSON files** from successful baseline
[CI 43498882494](https://github.com/StromFLIX/nautionette/actions/runs/43498882494),
commit `94bb46f81440f84dd2007bd14ddf41791d20bec2`. This is the latest successful
pre-update CI, not a claim about post-update images or a running deployment.
The baseline contained **1,720** distinct package/version/advisory/namespace/fix
tuples. After implementing the updates, all **40** JSON files from successful
[CI 43512596141](https://github.com/StromFLIX/nautionette/actions/runs/43512596141)
for `0a0b02307fa1a1ea49b6042c3e49606bcf20e8fe` were downloaded and inspected too.
The [complete finding ledger](dependency-audit-2026-11-13-images.json) now records
that tested candidate's original artifact/file SHA-256s, **20** full-inventory
counts/fingerprints and **1,885** distinct tuples. The larger scope includes the
four previously unscanned maintenance/recovery images; it is not evidence that
updating the application introduced all the additional findings. Multiple
binaries/images can duplicate a finding. No severity, unfixed issue or generic
CPE match was filtered out.

The new `scripts/summarize_dependency_artifacts.py` reproduces this ledger from
downloaded artifacts; it rejects empty/incomplete input and does not change any
scanner policy. SPDX remains the authoritative package inventory. CI now also
builds/inventories/scans **Temporal admin tools, both legacy recovery images and
the actual candidate-bundled maintenance controller**. These four contexts were
missing from the earlier SBOM matrix. Regression tests require coverage of every
repository Dockerfile and the deployment controller's exact source-bundle builder.

The tested candidate's actual PostgreSQL SBOM confirms **17.12** and gosu's embedded
**Go 1.27.4**; its Grype report contains only the three distro BusyBox matches,
with **GO-2026-6859 absent**. All 24 CI jobs succeeded, including real Chromium /
WebKit, native ArtCraft, offline compiler/runtime isolation, PostgreSQL volume
reuse/privilege drop, MaxIO and Temporal migration/replay/restore tests. This is
image/test evidence for that SHA, not a substitute for the final pushed SHA's gates.

Inspection of initial implementation CI 43509214606 found a genuine scanner blind
spot: the compiler SPDX listed only **31 OS/image entries**, not Cargo dependencies.
Syft's image defaults omit its directory-oriented Cargo.lock cataloger. CI now adds
`+rust-cargo-lock-cataloger` through `.github/syft-miniapp.yaml`, **preserving all
normal image catalogers**, and fails if any external locked crate is missing from
the actual image SPDX. The corrected CI 43512596141/compiler SPDX has **1,153
entries**, including all **29** external crates from `/sdk/template/Cargo.lock`
and upstream development locks in `/sdk/vendor`. The assertion requires the SDK
lock as the provenance: an unrelated vendor dev lock must not satisfy it. The
ledger now contains the corrected scan. This inventories optional/vendored source
too, not proof every crate is linked into every miniapp.

Observed baseline runtimes include Python **3.13.18**, Node **24.22.3**, npm
**11.21.2**, uv **0.13.7**, BusyBox's copied static binary **1.38.0**, distro
BusyBox **1.37.0-r31** (miniapp compiler **-r20**), nginx **1.31.8-r1**, OpenSSL
**3.0.22-1~deb12u2** / **3.5.7-1~deb13u3**, and Alpine libssl3 **3.5.9-r0**.
The actual Temporal image embeds Go **1.26.10**; Collector, PostgreSQL's gosu and
Temporal UI embed **1.27.3**. apt/apk cannot patch an embedded Go module/stdlib.

### Advisory dispositions and required follow-up

Every exact advisory/package/version/fix state is retained in the ledger. These
are the reasons/next actions for its categories, not a vulnerability allowlist:

- **gosu GO-2026-6859:** remedied in source by the verified Go 1.27.4 rebuild.
  The candidate PostgreSQL SBOM/security report and existing-volume/non-root test
  verified this, not just the Dockerfile. PostgreSQL's floating **17-alpine** now resolves to
  **17.12**, a same-major patch; data path and volume identity remain unchanged.
- **Temporal 1.32.2 / admin-tools:** no newer stable upstream release exists.
  Its embedded Thrift 0.24.0 findings (**CVE-2026-63772/66055/82458/83663/94637**)
  advertise 0.25.0 fixes; compress 1.18.7 (**GO-2026-6865**) advertises 1.20.2;
  x/net 0.60.0 (**GO-2026-6860/6866**) advertises 0.62.0; x/crypto
  (**GO-2026-5932**) and OTel (**GHSA-8wmf-6v46-5gfg**) also have newer module
  fixes. Echo's **GHSA-246p-cpwv-v3jq / GHSA-99jh-6h7p-pp36** are reported
  unfixed. Track upstream server/tools builds and rerun schema/replay/restore
  tests before replacing them. Do not binary-patch these modules or make an
  unreviewed server/schema jump. Legacy images are only the exact-version
  rehearsal/recovery contract, not the normal deployed server.
- **Temporal UI 2.58.1 and Collector 0.165.0:** both remain latest stable.
  Their embedded Go 1.27.3 (**GO-2026-6859**) needs an upstream rebuild; UI's
  x/net 0.60.0 needs 0.62.0. Collector also reports x/crypto's
  **GO-2026-5932**; use the exact ledger rather than assuming a module is fixed
  by apk. Recheck upstream releases and new image artifacts next run.
- **Maintenance controller's copied Docker/Buildx binaries:** the current
  `docker:29-cli` is CLI **29.9.3** (embedded Go **1.27.4**) and Buildx **0.39.0**
  (embedded Go **1.26.10**). Buildx 0.39.0 was independently checked as upstream's
  latest stable release. Its module graph still includes Docker 28.5.2 and
  go-archive 0.2.0. Reports include **GO-2026-4887/6859**, x/crypto's
  **GO-2026-5932**, **GHSA-hfg8-hc9c-6c3h** (archive fix 0.3.0), and Docker's
  **GHSA-pxq6-2prw-chj9 / GHSA-x86f-5xw2-fm2r / GHSA-rg2x-37c3-w2rh /
  GHSA-vp62-88p7-qqf5** (no fixed versions in the reports). Track official
  Docker/Buildx rebuilt releases, then verify the private candidate-bundle build
  and maintenance-controller rehearsal. Do not binary-patch a privileged
  deployment tool or substitute a Docker daemon upgrade (host daemon is not a
  repository dependency). The controller remains deployment-only, stopped with
  restart disabled after completion, with no public command API.
- **Legacy recovery server/tools:** the newly scanned 1.29.7 images report older
  Go 1.25.11/1.26.4 and numerous embedded SDK, Thrift, Echo, gRPC, JOSE, OTel,
  crypto/net/text and stdlib findings. Every exact version/ID/fix is in the ledger
  under `vulnerabilities-temporal-legacy` / `-tools-legacy`. They are intentionally
  pinned to the original restore/minor-hop compatibility contract, not normal
  deployed services. Do not replace them with a maintained server and invalidate
  the old-schema restore test. Required next step is upstream backports for that
  old line, or a separately rehearsed replacement/recovery protocol; continue
  using only private disposable migration/recovery networks meanwhile. Some
  embedded server pseudo-version CPE/advisory matches are not reliable evidence
  of the actual server release, so retain rather than silently dismiss them.
- **Vendored development lock rand 0.8.5:** the corrected compiler scan exposes
  two low-severity **GHSA-cq8v-f236-94qc / RUSTSEC-2026-0097** matches (fix 0.8.6).
  SPDX provenance is `/sdk/vendor/foldhash/Cargo.lock` and
  `/sdk/vendor/memchr/Cargo.lock`, not `/sdk/template/Cargo.lock`. Rand is absent
  from the SDK's 29-crate resolved graph and is not built into its application
  through these unused upstream dev/test lockfiles. Keep these observations
  visible. Required follow-up: consume parent crate releases that refresh their
  packaged dev locks, or revisit if those test graphs are ever executed. Do not
  mutate checksummed vendor files, add an unused rand override, or exclude the
  files from scanning merely to erase findings.
- **npm's own nested glob/minimatch:** **GHSA-3g3m-2fjr-6pw4** (glob 13.0.6 →
  14.0.2) and **GHSA-g3hv-75wg-m249** (minimatch 10.2.5 → 11.0.1) occur in
  the globally installed npm 11.21.2 tool bundle. This is separate from the
  frontend graph's zero-finding npm audit. No newer npm 11 is published.
  Replacing files inside npm or applying a global minimatch override is not safe.
  npm 12's migration requirements are below; track an upstream npm 11 patch or
  implement/test that distinct tool/runtime migration.
- **Debian/Alpine packages:** the complete ledger preserves package-specific CVEs
  and distro revisions, including curl, glibc, SQLite, Git, XML, compiler/browser
  libraries and distro BusyBox **CVE-2025-60876**. These reports do not supply a
  fixed distro revision. Existing Dockerfiles run apt/apk upgrades; compare new
  CI reports against vendor security/backport trackers before asserting a fix or
  exploitability. Retain the supported distro lines for now. Do not replace libc,
  OpenSSL or an OS SQLite library with an arbitrary upstream build. Next steps:
  consume vendor fixes with a pulled, uncached rebuild when available; separately
  validate a Debian/Alpine distribution migration with the complete browser,
  native-binary and multiarchitecture matrix if those fixes require it.
- **Generic Python/BusyBox CPE matches:** e.g. Python 3.13.18 reports
  **CVE-2025-15367**, **CVE-2026-103995**, **CVE-2026-116566**, with scanner
  fixes labelled 3.15.0a6 / 3.16.0. Those labels do not establish an available,
  compatible 3.13 fix (nor that a pre-release/future version is a deployable fix).
  Reconcile against upstream advisories/backports; do not jump to an experimental
  runtime or silently suppress the matches. The official 3.13 image's current
  patch remains 3.13.18. Python workspace and speech pip-audit are independently
  clean; that does not clear binary/interpreter or distro findings.

Report-only image scanning remains visible; application `npm audit` still blocks
CI. No authentication, networks, credentials, volume retention or security gate
was weakened. The ledger identifies its exact tested image source SHA; use the
final pushed candidate's new artifacts for final counts. The corrected Cargo
inventory was already verified in the cited successful run. It is not a claim about live deployments or other architectures.

## Current upstream pins and intentional holds

- **Unchanged/current:** agentgateway 1.7.0 (including its checksum-verified CI
  binary/fallback asset), MaxIO 0.4.2, Temporal server/admin-tools 1.32.2, Temporal
  UI 2.58.1, Collector 0.165.0, nginx 1.31.8, copied BusyBox 1.38.0-musl,
  Playwright 1.65.0 (agent/frontend/CI container), Pi 1.5.2 and its runtime guard.
  Actions were checked against current upstream releases: checkout 7.0.1,
  setup-node 7.1.0, upload-artifact 7.0.2, setup-java 6.2.0, setup-android 4.0.4,
  action-gh-release 3.0.4, setup-buildx 4.4.1, build-push 7.4.0, bake 7.5.0,
  login 4.8.0, setup-uv 10.3.3, sbom-action 0.24.3 and scan-action 7.4.2.
  Existing floating major tags stay on those compatible release lines.
- **PostgreSQL 18:** still a separate major data/directory migration requiring a
  verified backup/restore and explicit operational approval. No unattended jump.
- **huggingface-hub 2.4.0 / loguru 0.8.0 / python-dateutil 4.0.0:** current
  compatible versions remain 1.33.0 / 0.7.3 / 2.9.0.post0. FastEmbed 0.9.1
  constrains the first two; botocore constrains dateutil. Wait for compatible
  parent releases or test their replacements; never override the resolver.
- **npm 12.2.2:** [12.0's breaking changes](https://github.com/npm/cli/releases/tag/v12.0.0)
  include disabling git/remote dependencies by default, blocking dependency
  lifecycle scripts without a root allowScripts policy, rejecting formerly
  accepted CLI flags, removing shrinkwrap and changing JSON output. These affect
  the trusted stdio MCP/agent package runtime, not only frontend builds. Retain
  supported npm 11.21.2 until explicit package/install-script/remote-source policy
  and regression fixtures exist. Do not broaden all scripts/sources or alter
  arbitrary user MCP services to get this migration green.
- **Node 26 / Python 3.15 / distribution changes:** Node 24 (observed 24.22.3)
  and Python 3.13.18 remain supported existing runtime contracts. A Node 26/npm 12
  migration must validate native Electron/Capacitor tooling, stdio packages, Pi
  and both architectures, not just npm resolution. Current-major patch updates
  are consumed by pulled image builds. No unsupported engine override was used.
- **Model:** the actual downloaded repository is
  `Qdrant/bge-small-en-v1.5-onnx-Q`, not the display name BAAI. Its current upstream
  main revision remains **aa8f8b060edb00e03bfdd08813a2949946c8ba55**; recursive
  tree comparison is identical. The model was loaded and real offline semantic
  retrieval passed with ONNX Runtime 1.32.0. Moonshine Voice 0.1.6 remains current;
  speech/model selection is unchanged. No model substitution or vector identity
  change is needed for this runtime patch.
- **Waymark / vendored design skills:** dated spike package/image pins reproduce
  the committed benchmark, not deployment dependencies. Preserve its evidence;
  evaluate a new version by rerunning the experiment separately. Skills are
  reviewed, revision-pinned guidance/assets, not an executable runtime feed;
  their source/license inventory was read before frontend dependency changes.

## Tests and deployment evidence

Local evidence (Linux x86_64):

- Ruff lint and format; complete workspace pytest: **2,250 passed, 38 skipped**.
- Frontend **261** unit/desktop-filter tests; **19** website tests; Vite production build.
- Agent/Pi real transport/session/output/package RPC suite: **252 passed,
  2 skipped**; separately all **5** real new-ArtCraft native/RPC tests passed.
- Hash-verified speech install and all **11** streaming PCM/session tests passed;
  speech outdated list is empty; workspace and speech pip-audit found no known
  advisories. npm audit, including development/Electron tooling, found **zero**.
- Rust 1.100.0 locked tests: **3 passed**, exercising actual authenticated HTTP
  and bundled SQLite; all 26 native archive metadata digests verified; source
  fingerprints regenerated with the canonical tool.
- Pinned model download and offline semantic retrieval: **1 passed**.
- Gosu 1.20 source archive checksum verified; `go test`/build with Go 1.27.4
  succeeded (upstream has no Go unit test files), and embedded module/compiler
  versions were inspected. A local `gosu nobody id` was refused with `operation
  not permitted` by the agent sandbox; the real Docker PostgreSQL privilege-drop
  and existing-volume tests remain required CI evidence.

Local browser execution was attempted but cannot be certified in this agent
container: Chromium failed `pthread_create: Resource temporarily unavailable`
under its PID limit; repeated Chromium exits were left as zombies by PID 1.
WebKit/native mobile signing and real Docker builds/isolation/migrations are not
available in this sandbox. Those are **not** recorded as passing locally. GitHub
CI's normal Chromium/WebKit, real images, offline agent, existing-volume PG17,
MaxIO, miniapp isolation and Temporal migration/replay/restore jobs must all pass.
Android/iOS/macOS signing and aarch64 execution remain platform coverage limits.

Only the final pushed SHA's successful CI and normal staging Deploy (including
component, miniapp/workflow, OAuth, dictation and creative-app smokes) authorize
completion. Coolify must report that same SHA and be healthy; HTTP 200 or an old
successful deployment is insufficient. Production remains release-gated and is
not promoted by this maintenance task. Final run/deployment identifiers are
reported by the maintenance workflow after verification, not guessed here.
