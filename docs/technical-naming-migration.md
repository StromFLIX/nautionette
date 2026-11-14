# Denkzeug technical-name migration

This is a staged compatibility migration, not a global string replacement. The
repo and public domains are already Denkzeug. Ship the changes below through CI
and staging, then use the normal release gate for production. A commit does not
publish a release or rewrite an independently deployed workflow.

## New names available in this release

| Surface | Canonical spelling | Compatibility retained |
| --- | --- | --- |
| Public links | `denkzeug.ai`, `app.denkzeug.ai`, `stage.denkzeug.ai`, `app.stage.denkzeug.ai` | Old hosts remain aliases until integrations migrate. |
| New installer downloads | `denkzeug-v…` | Existing `nautionette-v…` assets remain downloadable; canonical assets win per platform if both exist. |
| New mini-app source | `window.denkzeug`, `denkzeug-theme` | `window.nautionette` is the same frozen bridge, not a duplicate request stream. Both event names work. Saved app source and SQLite are untouched. |
| Skin exports | `denkzeug-skin` | Existing v2 legacy packs remain accepted. Loading a saved library preserves its markers; explicit exports use the new spelling. |
| Desktop bridge | `denkzeugDesktop` | The old global retains the same narrow IPC capabilities. New renderers also work with older installed preloads. |
| Desktop focus links | `denkzeug://oauth-return/<opaque-token>` | Old scheme accepted and registered. Tokens remain owner-bound, expiring and single-use across both spellings. OAuth codes never enter custom URLs. |
| Fresh desktop profile | `Denkzeug` | Existing legacy/custom directories are selected in place; no credentials or browser data are moved. |
| New installation template | `STACK_NAMESPACE=denkzeug`, queue `denkzeug`, bucket/access-key name `denkzeug-outputs` | Existing `.env` files are not changed. Compose's implicit legacy fallbacks remain to protect older installations. |
| New Python integrations | `denkzeug`, `denkzeug_backend`, `denkzeug_worker`, `denkzeug_docker_broker`, `denkzeug_workflow_mcp` | Original module objects, class/serialization identity and mutable singletons are shared. Distribution names and deployment entrypoints remain stable. |

`branding/brand.json` is the source for public origins, repository URL and download
prefix. Run `python scripts/sync_brand.py` after edits, then regenerate the image
fingerprint lock when generated agent-brand metadata changes. Do not edit its
generated copies individually. Historical audits and release records are not
rewritten to conceal the previous name.

Python aliases are **import interfaces**, not new `python -m` entrypoints. Keep
existing `uv --package nautionette-*` and Docker service commands until a separate
packaging/entrypoint migration is tested. For example, new integration code may
use `from denkzeug.brand import NAME` without creating another telemetry provider,
database connection, settings instance or event bus. Initialize service/package
trees on the startup thread before starting concurrent plugin imports. Do not
bootstrap interdependent parent packages concurrently under both spellings:
Python's cross-name import locks can form a cycle. The wheel tests cover both
serial import orders and concurrent leaf imports after package initialization.

## Existing installations: do not copy the new defaults over them

Retain current namespace, task queue, bucket, access key, app IDs, signing keys,
volume/network names and registry prefix. The new `.env.example` is for **new
installations**, not a replacement for an existing `.env`. Changing these values
is not a data migration and can attach empty volumes or abandon running work.

CI resolves three Compose configurations without starting containers: a fresh
Denkzeug installation, explicit legacy identities, and an older `.env` that
predates explicit namespace/queue settings. All must retain the intended volumes,
queue and bucket. Installed-wheel tests check both Python import orders and
nested imports for singleton identity. Browser tests exercise old/new mini-app
APIs on one isolated channel; native tests retain old IPC/protocol compatibility.

## Finish external URL migration before retiring old hosts

Do this **after production has the migration API**. Keep the old domains working
throughout. Never create a replacement GitHub App, rotate its credentials, or
remove a connected project just to change its public address.

1. Finish active GitHub App registration/installation flows and pause starting
   new ones during the change. Setup state is bound to the browser's origin.
2. In the **existing GitHub App's settings**, add the new callback URL and update
   its setup/webhook URLs. For the production instance use:

   ```text
   Homepage:     https://app.denkzeug.ai
   Callback URL: https://app.denkzeug.ai/api/projects/github-app/callback
   Setup URL:    https://app.denkzeug.ai/api/projects/github-app/installed
   Webhook URL:  https://app.denkzeug.ai/api/projects/github-app/webhook
   ```

   Keep old callback entries during transition where GitHub supports multiple
   entries. Do not move a production App's webhook to staging. Staging needs its
   own App for independent registration/lifecycle webhook tests; copied production
   credentials do not turn it into an independent installation.
3. If `GITHUB_APP_PUBLIC_URL` is explicitly set, change it to the matching new
   origin and redeploy the **same tested release** before updating saved metadata.
   Preserve secrets. `APP_URL` should already point to the new app origin.
4. From an authenticated owner session, call:

   ```text
   PUT /api/projects/github-app/<existing-connection-id>/public-url
   {"public_url":"https://app.denkzeug.ai","callbacks_updated":true}
   ```

   The `migrate_github_project_origin` backend operation exposes the same request.
   Read the connection ID from Projects first. The endpoint verifies GitHub's
   webhook destination with the existing App credentials, refuses pending setup
   attempts, and atomically updates only connections/registered metadata for the
   same App ID. Callback/setup URLs require the owner's explicit attestation
   because GitHub does not expose them through this API. No private key, webhook
   secret, project ID or checkout is replaced. If verification fails, metadata
   remains unchanged. Pending attempts can finish or expire (up to one hour);
   do not delete browser-bound setup state to bypass the guard.
5. Test a new setup/reinstall flow and a signed repository webhook delivery, then
   verify existing repository access. Do not assume a green settings page proves
   the interactive provider callback works.
6. Update GitHub **Actions → Environments → Variables → APP_URL** to
   `https://app.stage.denkzeug.ai` for staging and `https://app.denkzeug.ai` for
   production. Keep the same environment secrets and Coolify application UUIDs.
   The current automation can keep using old aliases until this is done.
7. Inventory other provider OAuth redirect allowlists, external triggers, browser
   bookmarks and native client server URLs. Update provider/client registrations
   in place and verify them before removing any old route.

The GitHub App settings UI and Actions environment variables are external owner
configuration. A code push cannot update them. Their migration is not complete
merely because public links now use the new names.

## Stateful identities deliberately not renamed automatically

| Identity | Why it remains | Requirement before changing it |
| --- | --- | --- |
| Compose project/volume/network names and S3 buckets | They identify existing data. | Exact resource inventory, verified backup/restore, explicit volume/bucket mapping and rollback rehearsal. Never `down -v`. |
| Docker `nautionette.*` labels and resource names | Broker reconciliation, cleanup and ownership discovery rely on them. | Dual-read ownership discovery before any relabel/recreate; test existing agents, worktrees, compiler, packages and apps. |
| Temporal namespace/queue and workflow/activity/schedule names | Existing durable histories and running work depend on them. | Drain/version old workers; explicitly migrate schedules and queue consumers. Keep handlers required by retained histories. |
| Browser storage, draft IndexedDB and session entry types | They contain credentials, unsent work and resumable context. | Tested read/write/delete migration, including rollback, blocked storage and cross-tab races. Never copy credentials across web origins automatically. |
| Git baseline refs and chat lock paths | They protect conversation changes and concurrent ownership. | Compatible discovery plus resumability/concurrency tests on old repositories and sessions. |
| Distribution/module implementation names | Existing workflows, commands and serialized identities use them. | Additive import aliases ship first. Test installed wheels and module state before moving implementations or deployment commands. |
| `dev.nautionette.app`, desktop package/launcher identity and signing keys | OS updates must recognize the installed application. | Keep these stable for in-place upgrades. A different app ID is a separate new-app/data-transfer project, not cosmetic cleanup. |
| Legacy GHCR namespace | Existing deployments and rollback commits pull it. | Publish all fingerprints in the new namespace, verify permissions and cold-pull staging, then coordinate deployment prefixes. Keep old images for rollback. |
| Persisted user workflows/mini-apps/skins and GitHub deduplication markers | Blind rewriting changes user code or can duplicate remote actions. | Explicit owner migrations with source/data backups and behavior tests. Existing aliases remain until users have migrated. |

A literal zero-match search is not an upgrade-safety criterion. Legacy strings in
compatibility code and its tests are intentional. Do not rewrite Git history,
old release assets, signed applications, user-authored code or backups just to
make `git grep -i nautionette` empty.
