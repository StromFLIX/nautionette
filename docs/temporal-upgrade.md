# Temporal: migrate off auto-setup without skipping server minors

## Scope and rollout status

The maintained target is **Temporal 1.32.2**, with matching `admin-tools`.
The reviewed path is **1.29.7 → 1.30.9 → 1.31.5 → 1.32.2**.
This change supplies explicit schema tools, an opt-in Compose overlay and a
PostgreSQL-backed CI rehearsal. **It does not migrate staging or production.**
Staging refresh additionally refuses different Temporal image IDs: copying old
production schemas into an already-upgraded staging server would bypass this
migration. Align and verify both images before re-enabling refresh (even builds
of the same version must match; this is intentionally conservative).

The base Compose file deliberately retains `auto-setup:1.29.7` until each stack
has completed its maintenance procedure. Do not change `TEMPORAL_VERSION` on the
legacy Dockerfile: newer `auto-setup` tags do not exist.

The overlay is a separate deployment configuration, not an instruction to
implicitly mutate live databases at every container start. Once staging and
production have independently migrated and passed checks, make the maintained
configuration the base in a follow-up and remove the legacy image. Do not leave a
migrated stack configured to redeploy the legacy base file.

## Why this is more than an image bump

- [1.30 image changes](https://github.com/temporalio/temporal/releases/tag/v1.30.1):
  auto-setup is deprecated. `server` no longer creates schemas/namespaces or
  includes the Temporal CLI. It uses an embedded Sprig configuration template.
- [1.31 schema prerequisites](https://github.com/temporalio/temporal/releases/tag/v1.31.0):
  core **1.19** and PostgreSQL visibility **1.14** must exist before server start.
- [1.32 behavior changes](https://github.com/temporalio/temporal/releases/tag/v1.32.0):
  the unified visibility query converter is now the default. Do not disable it to
  mask invalid application queries; test actual queries.
- [Upstream upgrade policy](https://docs.temporal.io/self-hosted-guide/upgrade-server):
  upgrade consecutive minor releases, schemas before servers, and allow each
  server version to process workloads before continuing. Database downgrades are
  not the rollback procedure.

| Server/tools release | Core schema | PostgreSQL visibility schema |
| --- | --- | --- |
| 1.29.7 baseline | 1.18 | 1.9 |
| 1.30.9 | 1.18 | 1.13 |
| 1.31.5 | 1.19 | 1.14 |
| 1.32.2 | 1.19 | 1.14 |

`Dockerfile.server` copies only the matching CLI from admin-tools to retain our
loopback health probe. The server stays non-root, keeps its dynamic config, and
retains private networking. `Dockerfile.tools` carries version-matched schemas
and a PostgreSQL client for fail-closed schema inspection. Passwords travel in
process environment, not CLI arguments. Schema tools have only the data network;
there is no Docker socket, host port, or automatic database reset.

## Evidence required before either live migration

CI job **Temporal PostgreSQL upgrade and restore** runs
`uv run --locked python -m scripts.check_temporal_upgrade`. It creates isolated,
randomly named containers/volumes and never connects to a live stack. It:

1. Starts the existing 1.29.7 image with real PostgreSQL 17.
2. Creates a paused schedule and a workflow that finishes an activity, then waits
   durably for a signal. Stops the server and takes dumps of **both** databases.
3. Migrates schemas and starts **every** intermediate minor. Replays retained
   history with the installed Python SDK, queries pending workflows, checks SQL
   visibility and schedule state, and writes a new suspended history at each hop.
4. Restarts the final server, signals/resumes all histories, unpauses/triggers the
   preserved schedule, and checks its workflow result.
5. Restores the pre-upgrade dumps into another PostgreSQL instance and resumes
   the original history on **1.29.7**. It never starts the old server on new data.
6. Separately tests explicit fresh schema initialization, namespace creation and
   workflow execution with the maintained image.

Offline `tests/test_temporal_upgrade.py` also checks mismatch/outage/jump refusal
and the Compose isolation contract. Offline tests alone are **not** migration
proof. Wait for the Docker CI job to pass at the exact candidate commit. The CI
fixture is not a clone of production: staging must additionally exercise copied
real histories and application workflows. Longer workloads may need a longer
soak between versions; a green synthetic test is not permission to skip that.

## Maintenance prerequisites

Start with **staging**, then production in its own maintenance window only after
staging evidence is accepted. This procedure needs a trusted Docker/Compose host
session or equivalent maintenance automation. The normal Coolify deployment API
cannot execute backups or intermediate schema migrations.

- Disable competing deploys/refreshes for the whole window, including automatic
  main-to-staging deployment. Use the existing persistent maintenance guard when
  operating in Coolify; a shell session is not a deployment lock. Do not run this
  concurrently with `Refresh staging data`.
- Record the actual deployed source, image IDs, Compose project/files/env paths,
  database role, namespace, retention, schedule states and **history shard count**.
  Both shipped templates default to 4, but an existing cluster's shard count must
  never change. Set `TEMPORAL_NUM_HISTORY_SHARDS` to its actual value for the
  overlay. Refuse an unexpected baseline version or schema pair.
- Keep the same PostgreSQL 17 instance and volume. Do not combine this with a
  PostgreSQL major upgrade, staging refresh or application schema migration.
- Verify capacity and restrict backup directory access (`umask 077`). Backups
  contain workflow inputs/results and credentials. Use the existing encrypted,
  off-host backup policy; never upload dumps to public CI artifacts or chat.
- Drain active agent calls and workers gracefully, pause schedules (save previous
  paused states), and stop new workflow submissions. Record how missed schedule
  times should be handled on resume. Do not terminate production workflows.
- Stop broker before worker/backend and other writers. Do not proceed while
  unexpected workers or submitters can still reach this cluster.
- Preserve the complete app/volume recovery set per [staging.md](staging.md).
  Restoring Temporal alone can leave backend SQLite/run state inconsistent.

## Explicit per-hop commands (ordinary Compose)

These examples assume repository root, the correct existing project's Compose
configuration and environment already selected. **Do not copy the default project
name onto a Coolify host**: use that application's rendered container/volume names
and equivalent generated configuration. An unreviewed alternate Compose project
can create an empty second database instead of migrating the intended one.

```sh
# In a trusted maintenance shell; do not enable shell tracing.
umask 077
backup=/secure/backups/temporal-before-upgrade
mkdir -p "$backup"
# Supply -p/--env-file and other EXISTING project flags here as necessary.
dc() { docker compose -f docker-compose.yaml "$@"; }
newdc() { docker compose -f docker-compose.yaml -f infra/temporal/compose.upgrade.yaml "$@"; }

# After application writers have drained/stopped:
dc stop temporal
# PG stays up for logical dumps; all Temporal writers are stopped.
dc exec -T postgres sh -c 'exec pg_dump -U "$POSTGRES_USER" -Fc temporal' > "$backup/core.dump"
dc exec -T postgres sh -c 'exec pg_dump -U "$POSTGRES_USER" -Fc temporal_visibility' > "$backup/visibility.dump"
dc exec -T postgres sh -c 'exec pg_dumpall -U "$POSTGRES_USER" --globals-only' > "$backup/globals.sql"
sha256sum "$backup/"* > "$backup/SHA256SUMS"
```

**Before touching the source database**, restore these backups into an isolated
PostgreSQL 17 instance with its own volume and networks. Validate both schemas,
namespace/retention, schedules and pending workflow history. Start the recorded
old server there without production workers/network access; rehearse recovery.
A successful dump or `pg_restore --list` alone is not a restore test. Record
backup checksums and restore evidence privately. If backup or restore fails,
stop: restart the unchanged old stack only after investigating.

For **each** row below, build the matching tools/server, update both schemas with
the old server stopped, then start only that new server:

```sh
# FIRST HOP ONLY. Repeat deliberately with the next row after verification/soak.
version=1.30.9
previous=1.29.7
newdc --profile maintenance build --build-arg TEMPORAL_VERSION="$version" temporal temporal-schema
newdc --profile maintenance run --rm --no-deps temporal-schema status
newdc --profile maintenance run --rm --no-deps temporal-schema "upgrade-from-$previous"
newdc up -d --no-deps --no-build temporal
newdc exec -T temporal temporal --address 127.0.0.1:7233 --command-timeout 4s operator cluster health
newdc exec -T temporal temporal-server --version
```

| Hop | `previous` | `version` |
| --- | --- | --- |
| First | 1.29.7 | 1.30.9 |
| Second | 1.30.9 | 1.31.5 |
| Final | 1.31.5 | 1.32.2 |

At **every** hop, verify the expected schema pair, namespace/retention, history
shards, server logs, paused schedules and SQL visibility. Temporarily bring back
controlled application workers/submission paths, process a representative safe
workflow and verify pending workflow resumption/history replay. Leave sufficient
soak time for your longest relevant workloads. Drain/stop writers and stop the
server again **before** the next hop. The commands intentionally do not loop
through versions unattended. A health probe alone is not a workload test.

The schema script accepts only the reviewed predecessor's schema pair. If it
fails after a partial update, it fails closed on retry: inspect
`schema_update_history`, the actual versions and backup before deciding how to
recover. Do not edit the schema version table, drop databases or pass a different
predecessor just to bypass the guard. Versions 1.31/1.32 share schemas: schema
checks alone cannot prove the 1.31 server actually ran; keep per-hop evidence.

After the final hop, make the **combined configuration** the stack's permanent
deployment source before re-enabling any auto-deployment. The overlay hard-pins
1.32.2, overriding old persisted build-argument defaults. Rebuild normally and
verify the final version survives recreation. Existing namespaces are preserved:
the maintained server never recreates them or resets retention. The backend still
reconciles configured retention as before.

For Coolify, deploy an equivalent reviewed merged Compose configuration with the
same application-owned resources. Verify rendered image build args, private
networks, volume names and the deployment owner's source config. Merely running
`newdc` once does **not** update Coolify's next deployment. Do not enable the
maintenance-profile schema service as a long-running production dependency.

Finish with authenticated application health, schedule readback, a disposable
safe scheduled workflow run and restart/resumption checks. Restore **only** the
previously active schedules, honoring the chosen catch-up policy. Keep previously
paused schedules paused. Remove maintenance guards only after verification.

## Fresh maintained installations

With no existing Temporal data, build the overlay's two images, start PostgreSQL,
then run `temporal-schema initialize-empty` explicitly. It refuses nonempty public
schemas. Start the server and create the configured namespace deliberately:

```sh
newdc --profile maintenance build temporal temporal-schema
newdc up -d postgres
newdc --profile maintenance run --rm temporal-schema initialize-empty
newdc up -d --no-deps --no-build temporal
# Wait for cluster health first. Use the real configured namespace/retention.
newdc exec -T temporal temporal operator namespace create --namespace default --retention 7d
```

If initialization partially fails, investigate rather than rerunning destructively.
Never use this fresh-install command to repair or upgrade an existing cluster.

## Failure and rollback

Stop on any schema, startup, replay, schedule or workflow failure; keep writers
blocked. Preserve logs, version checks and the failed database for diagnosis.
Do not automatically fall back to `auto-setup` or try to downgrade schemas.

Restore the verified pre-upgrade backup into **separate, empty PostgreSQL 17
volumes**, with the original roles/ownership, both databases and compatible
application state. Verify checksums and restore with `pg_restore --exit-on-error`.
Keep the upgraded volumes intact until recovery is validated. Start the recorded
1.29.7 server against the restored pair on isolated networks, verify namespaces,
retention, pending executions and schedules, then deliberately switch the stack's
configuration back to that recovery set. Only then resume writers/schedules.
Restoring rewinds execution history to the backup boundary: external activity
side effects are not undone. Reconcile such effects and application run records
before retrying work. Never promise exactly-once behavior from a database restore.
