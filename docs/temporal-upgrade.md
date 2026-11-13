# Automatic Temporal upgrades during deployment

**No GitHub host SSH keys are required.** GitHub continues to use its existing
Coolify API token. Coolify's native pre/post-deployment hooks execute trusted,
candidate-pinned maintenance code in the application's existing Docker broker.
The normal Compose configuration now uses maintained `temporalio/server:1.32.2`.
Do not deploy it over old data without the migration hook or the manual procedure.

## Staging first, production on promotion

```text
main push → CI (real migration + restore tests)
          → staging pre-hook migrates staging's own database
          → normal Coolify container replacement
          → post-hook verifies the new server and deployed source
          → authenticated health and staging application smoke tests

publish/promote stable release at that exact tested staging commit
          → require successful staging deployment AND smoke checks
          → production pre-hook migrates production's own database
          → normal production deployment + post-hook + authenticated health
```

There is no production data copy and no automatic production release from a main
push. Production retains the existing exact-commit staging/release gates. A
migration failure prevents promotion/deployment success; it is not a silent skip.
Routine later deployments check the existing version/schema and avoid taking a
backup or running schema updates when already current.

## Native deployment integration

`scripts/coolify_release.py` validates the source, target, active deployments and
production staging evidence **before** installing managed hooks and queueing a
deployment. `scripts/temporal_deploy.py` obtains the scripts, image context and
`upgrade-plan.json` from the exact candidate Git commit, not a moving main branch.
The compressed hook contains source code and identifiers, never DB credentials.
Existing custom pre/post hooks are refused rather than silently overwritten.

The broker already has the Docker socket. The trusted hook uses that existing
control boundary to build/start one private maintenance controller for this
application. It has no published port, no HTTP execution API and no SSH key. This
is a temporary deployment-only Docker administrator, not an agent container or a
new generic broker API. Its Docker socket and the two dedicated host maintenance
directories are mounted explicitly; it does not mount the host root filesystem.
Normal agent/mini-app isolation and authentication are unchanged.

The installed Coolify **4.7.5** implementation was checked for these behaviors:

- A pre-hook runs in an existing container before replacement, may run more than
  once, and failure aborts deployment. Multi-service apps must name the container
  (`docker-broker`). Hooks can be skipped when no old containers are running.
  Therefore the maintained server also has a **startup fence**: automatic deployments
  must present a read-only, matching-revision `current`/`prepared`/`completed` marker
  before Temporal can start. Skipping a hook cannot start a server while migration
  is still in progress. The mounted fence directory contains only revision/phase,
  never backups, logs or credentials.
- Post-hook failures are logged but **do not make Coolify report failure**. Therefore
  the new broker's read-only health check additionally requires the controller to
  report `completed` for the exact `APP_VERSION`. GitHub's authenticated component
  health gate cannot mistake a failed post-hook for a successful upgrade.

The controller is detached from the broker/hook process and persists its state,
private logs and backups under `/var/lib/nautionette-temporal`. It survives runner
loss, broker/container replacement and Docker/host restart. It does **not** retry
possibly applied schema operations on restart. The restart path fences the old
Temporal/writers and records a recovery requirement when schema writes may have
started. The same host lock and active-operation markers exclude staging refresh.

## What the first automatic migration does

The reviewed plan is **1.29.0 → 1.29.7 → 1.30.9 → 1.31.5 → 1.32.2**.
Live staging preflight discovered that an old persisted Coolify build argument had
kept its actual binary at 1.29.0 despite the repository's 1.29.7 default. Upgrade
from the observed version, not an assumed image pin. Already-patched 1.29.7 stacks
start at that entry; the older baseline first receives the same-minor patch hop.
The 1.29.7 hop uses its published legacy bundle for both server and tools;
`admin-tools:1.29.7` is not published. Only this pre-schema-transition patch step
uses auto-setup; 1.30 and later use maintained server/admin-tools images.

| Server/tools release | Core schema | PostgreSQL visibility schema |
| --- | --- | --- |
| 1.29.0 observed baseline | 1.18 | 1.9 |
| 1.29.7 patched baseline | 1.18 | 1.9 |
| 1.30.9 | 1.18 | 1.13 |
| 1.31.5 | 1.19 | 1.14 |
| 1.32.2 | 1.19 | 1.14 |

1. Check actual stack ownership, running server version, schema pair, namespace,
   data-network isolation, shard count and backup capacity. Refuse active agent
   containers/external volume writers instead of killing their work. Unexpected
   versions/schemas fail closed. No database is initialized over existing data.
2. Build all matching tools/server images before pausing writes. Record immutable
   image IDs; recheck the inventory before touching services.
3. Pause the broker (including worker reconciliation), stop submission/worker
   services cleanly and save namespace identities/retention, original schedule
   states and fingerprints of pending user histories. Pause schedules. Create a
   synthetic durable workflow/schedule on a unique queue, never an app queue.
4. Stop the remaining services cleanly and take a verified, stopped cross-volume
   backup: PostgreSQL, backend state, workflow files, gateway state, artifacts,
   projects, chat sessions and output objects. Preserve metadata and permissions.
5. Restore the PostgreSQL backup to another volume on an isolated network, boot
   the recorded old server, and check its namespaces, schedules, retained history
   fingerprints and synthetic replay/workflow execution. A successful copy alone
   does not authorize migration.
6. Start the original PostgreSQL. At each consecutive minor, upgrade core and
   visibility schemas with matching admin-tools **before** starting that server.
   Run only on the private data network: normal app workers cannot reach the
   intermediate servers. Check history prefixes, namespaces and paused schedules;
   replay/resume the synthetic history and execute activities on the private
   queue. Soak each minor for two minutes before advancing.
7. On the final version, verify durable signal/resumption and schedule dispatch,
   restore only schedules that were previously active, and stop the intermediate
   server. The old auto-setup server remains stopped; it is never restarted on
   new schemas. Unpause the broker so the pre-hook can return.
8. Coolify deploys the maintained configuration normally. The post-hook verifies
   actual server/schema versions, preserved shard count and the backend's exact
   source revision, restores any restart policies on reused containers, and marks
   the operation completed. Only then can broker/component health pass.

Pending application histories are checked for intact serialized prefixes without
executing arbitrary user workflow functions during maintenance. The CI fixture
also explicitly replays SDK histories. Real staging application smoke tests run
only after the final deployment. Paused schedules remain paused; active schedule
catch-up follows their existing policies. Restoring a backup does not undo external
activity effects or provide exactly-once execution.

## Configuration and prerequisites

No additional GitHub secrets or host SSH configuration are introduced. Existing
Coolify application read/update/deploy permissions must allow the documented
pre/post-deployment command fields. Root-equivalent Docker access stays with the
existing broker and the deployment-only controller. Do not configure conflicting
manual hooks or manually deploy/refresh while maintenance is active.

The automatic path supports the shipped PostgreSQL 17 database names/layout and
standard stack topology. It does not combine a PostgreSQL major migration with a
Temporal upgrade. Custom history shard counts must be explicitly configured in
`TEMPORAL_NUM_HISTORY_SHARDS` to match the existing cluster **before deployment**;
the shipped count is 4. A mismatch aborts before downtime/schema changes. Never
change a cluster's history shard count to make a deployment pass.

Allow a maintenance window: this is a safe stopped migration, not zero downtime.
Large backup/restore sets can take longer than the small CI fixture. The controller
has a 100-minute operation budget; the GitHub Deploy job allows migration plus
normal deployment/smokes. Coolify's server-level deployment timeout must also cover
the migration and image builds; inspect **Server → Advanced → Builds** rather than
silently changing a shared server setting. A timed-out runner/deployment must inspect
and reuse the existing controller, not launch a second migration.

Backups/logs remain root-only on the deployment host. They can contain workflow
identities, inputs/results and integration credentials. Keep the existing private,
encrypted off-host backup policy and monitor retention/capacity. Do not publish
these directories as CI artifacts or chat outputs. Successful later deployments
retain earlier backup directories; deleting an idle old controller is not deleting
its backups.

## CI and acceptance evidence

CI job **Temporal PostgreSQL upgrade and restore** runs two real Docker tests:

- `scripts.check_temporal_upgrade`: legacy PostgreSQL-backed histories through all
  minors, SDK replay, resumption, schedules, visibility, restart, restoration onto
  the old server and explicit fresh initialization.
- `scripts.check_temporal_deployment`: the actual candidate hook and detached
  controller on a disposable stack, real stopped-volume backup/isolated restore,
  broker pause/unpause, all server hops, application-history resumption and duplicate
  hook delivery. It needs no live Coolify credentials or SSH access.

Unit tests cover ordering, schema mismatch/jump refusal, recovery before/after
possible writes, hook ownership and the health fence against ignored post-hook
errors. Only CI-successful candidates reach staging. Verify the exact commit's
staging Deploy run before promoting it; a finished Coolify deployment alone is not
the acceptance criterion.

## Failure, retry and recovery

The application-specific controller is named `nautionette-temporal-<app-uuid>`.
Inspect it with your normal trusted host administration path:

```sh
docker exec nautionette-temporal-APP_UUID python /opt/maintenance/temporal_deploy_job.py status
```

Only fixed status/verification operations are exposed internally; do not make a
public generic execution endpoint to control it. Private run state/logs/backups
are in `/var/lib/nautionette-temporal/runs/<run-id>`.

- **Before any schema writes:** if writers were stopped, recovery resumes the
  unchanged old stack and original schedule states. A verified safe preflight
  failure can be retried with a new controller; no backup is overwritten.
- **After any possible schema write:** stop/fence the old server; record
  `needs_recovery`. Do not automatically start auto-setup, downgrade schemas or
  retry a partly applied schema command. Inspect `schema_update_history`, the
  recorded phase and private tool logs. Never edit schema version tables to skip
  the guard.
- **Prepared, deployment interrupted:** a rerun for the same candidate reuses the
  prepared controller; it does not rerun migrations. A different candidate cannot
  take over an unfinished operation. Post-hook verification/health is still required.
- **Rollback:** preserve failed volumes. Restore the verified pre-upgrade backup
  into separate empty PostgreSQL 17 volumes, with the original roles, both
  databases and compatible application state. Boot the recorded old server in
  isolation, reconcile external activity effects, then deliberately switch the
  recovery set. A tag rollback is not a database rollback.

## Manual/fresh Compose installations

Coolify hooks intentionally do not run inside `docker compose up`. Existing manual
installations must perform the same backup/restore and consecutive-minor procedure
before starting the maintained default. `Dockerfile.legacy` is retained only for
old-server recovery/rehearsal. `compose.upgrade.yaml` is a compatibility overlay;
the maintained service configuration now lives in the root Compose file.

A genuinely empty installation explicitly initializes schemas and its namespace:

```sh
docker compose --profile maintenance build temporal temporal-schema
docker compose up -d postgres
docker compose --profile maintenance run --rm temporal-schema initialize-empty
docker compose up -d --no-deps temporal
# Wait for cluster health, then use the real configured namespace and retention.
docker compose exec temporal temporal operator namespace create --namespace default --retention 7d
docker compose up -d
```

`initialize-empty` refuses nonempty public schemas. Never use it to repair an
existing database. The backend continues reconciling configured namespace retention;
the server itself no longer initializes/reset namespaces on startup.

Upstream references: [upgrade policy](https://docs.temporal.io/self-hosted-guide/upgrade-server),
[1.30 image changes](https://github.com/temporalio/temporal/releases/tag/v1.30.1),
[1.31 schema prerequisites](https://github.com/temporalio/temporal/releases/tag/v1.31.0),
[1.32 visibility behavior](https://github.com/temporalio/temporal/releases/tag/v1.32.0).
