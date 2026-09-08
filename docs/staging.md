# Production data in an independent staging environment

## Status / required access

The initial independent production-data clone was completed and verified on
2026-09-07 using temporary Coolify administrator access. See
[`staging-setup-status.md`](staging-setup-status.md) for actual deployment IDs,
verification results, and the remaining GitHub Actions configuration blocker.

For routine operation, use the [GitHub pipelines and exact secrets guide](github-pipelines.md):
it covers the manual **Refresh staging data** Action, recovery, and production releases.
This document retains the initial provisioning/manual cloning procedure for reference;
do not rerun the copy on ordinary deployments. GitHub Actions deployment is off until
its environment variables/secrets exist and `COOLIFY_DEPLOY_ENABLED=true` is configured.

Known production resource:

- Project `nautionette`: `gmmxgjx13nnmc9u3hw41vrpe`
- Application: `88veejhhzve4rjcnxzlyjzbo`
- Server: `hetzner-1`, `94.130.151.7`
- Repository: `StromFLIX/nautionette`, branch `main`, Docker Compose
- App: `https://app.nautionette.stromflix.com`
- Site: `https://nautionette.stromflix.com`

Desired staging domains (DNS records supplied by the owner):

- Backend: `https://app.nautionette.stage.stromflix.com`
- Website: `https://nautionette.stage.stromflix.com`

## 1. Provision without starting a cloned database

Create a `staging` environment in the same Coolify project and clone the production
application **configuration**, not references to production storage. Select the
same server, repository, branch and Compose build pack. Use the Compose file with
the isolation changes; compare Coolify's rendered Compose to it because saved
custom Compose content may override the repository file.

Disable Coolify auto-deploy for staging while initializing it. Set staging's
service domains to the two URLs above. Provision TLS through the Coolify proxy.
Keep production's Compose project, existing network names and existing volume
names unchanged. Never rename production storage as part of this setup.

Staging-specific runtime values:

```dotenv
STACK_NAMESPACE=nautionette-stage
APP_ENVIRONMENT=staging
BACKEND_PORT=18080
WEBSITE_PORT=18081
APP_URL=https://app.nautionette.stage.stromflix.com
GITHUB_APP_PUBLIC_URL=https://app.nautionette.stage.stromflix.com
CORS_ORIGINS=https://app.nautionette.stage.stromflix.com
```

Use a different Compose project (Coolify normally uses the new application UUID).
Inspect the final Compose: every staging network and volume must differ from
production. The broker's `TARGET_NETWORK`, `AGENT_NETWORK`, `AGENT_EGRESS_NETWORK`,
`WORKFLOWS_VOLUME` and worker image must match the *rendered* names. Set
`BROKER_WORKFLOWS_VOLUME=<application-uuid>_workflows` in each Coolify application
to match the backend's actual mount (including production). Coolify can
rewrite names; do not assume the repository's names survived parsing. Backend,
workers and Pi must all mount the same **staging** workflows volume. Ensure the
projects and artifacts mounts also resolve to staging only. Do not enable any
shared/additional production network on this application. The shared proxy edge
network is for inbound routing, not a reason to share internal/data networks.

The Compose change separates mutable image tags too. Agent-set Dockerfiles use
`ARG BASE_IMAGE`; the broker builds them against its content-addressed base image,
not a mutable production alias. Keep that convention for additional agent sets.

Retain the production `APP_TOKEN` if identical login is required; browsers will
still ask for it on the new origin. The app uses deployment-token authentication,
not a user-password database. Transfer secrets only through Coolify/server-side
backup mechanisms, never through chat or Git. Generate a new `INTERNAL_TOKEN` for
staging and configure it consistently on its services. Preserve the restored
PostgreSQL password initially (changing only an env var does not change the role
password of an existing database).

## 2. Take and restore a consistent snapshot

Do this once to seed staging. **Do not overwrite staging on every deployment.**
A later refresh is an explicit destructive operation on staging with its own backup.

Production owns these persistent datasets (verify actual mounts on the host):

| Dataset | Must include |
| --- | --- |
| `backend-data` | SQLite chats, messages, run index, settings, project metadata, GitHub App configuration |
| `agentgateway-data` | Runtime model/MCP integrations, stored credentials, gateway databases |
| `postgres-data` | Temporal execution history, visibility DB, namespaces and schedules |
| `workflows` | Live workflow Python and dependency files, not just Git seeds |
| `artifacts` | Workflow artifacts referenced by history |
| `projects` | Repository caches **and** `.sessions` worktrees and their shared Git metadata |

The safest exact cross-volume snapshot requires a short maintenance window:

1. Back up the existing application config and staging if it already has data.
2. Block new production writes/agent starts; drain or explicitly interrupt active
   agent calls. Stop the production broker (otherwise it restarts workers), workers,
   backend, workflow authoring and gateway writers; stop Temporal and then shut
   PostgreSQL down cleanly. Inventory dynamic Pi/worker containers too: stopping
   only Compose workers does not stop containers the broker has launched.
3. Snapshot/copy all six datasets from read-only source mounts, preserving numeric
   ownership, permissions, dotfiles and symlinks. Keep backups access-restricted or
   encrypted: they contain production data **and integration credentials**. Do not
   upload them to GitHub Actions artifacts or print their contents.
4. Restart production from its original volumes/config and verify it is healthy.
5. Restore the snapshot into separate, stopped staging volumes. Refuse any restore
   if a destination is also a production mount. Never `tar`/`cp` a live PostgreSQL
   data directory or a live SQLite main file without its transactional state.
   An online alternative needs SQLite's backup API, PostgreSQL logical backups of
   **all** Temporal DBs/roles, and coordination of the other writers; it is not an
   exact cross-database point-in-time copy by simply running `pg_dump`.

No snapshot/restore operation should be placed in the routine release pipeline.

## 3. Quarantine copied automation before first normal startup

**A cloned Temporal database is an active scheduler, not an inert history archive.**
Keep staging backend, broker, workers, Pi and workflow authoring stopped. Start only
staging PostgreSQL and Temporal on isolated networks. In the **staging** Temporal
namespace(s), use Temporal administration to:

- Pause every copied schedule, preserving definitions and input for inspection.
- Terminate copied open executions (including schedule-triggered/backlogged ones)
  with a reason such as `staging snapshot: do not replay production side effects`.
- Re-list schedules and open executions until all schedules are paused and none
  remain running. Repeat across every restored namespace.

These are intentional differences from production. Completed history and app data
remain available, but work already done in production must not execute a second
time. Do not rely on Nautionette's `disabled` setting alone: Temporal can fire
existing schedules without the backend. Do not start the broker early: it starts
missing workers automatically. When the staging backend first starts, its normal
recovery marks copied active chat turns interrupted and reconciles run statuses.

Review copied MCP destinations, deployment workflows, outbound webhooks and
scheduled tools before manually testing them. Separate local databases do **not**
isolate Fastmail, GitHub, Coolify or other remote accounts. Keeping identical
integration credentials means a manual staging action can still send real mail,
change real repositories or deploy production. The banner is not a sandbox.
Replace high-impact integrations with test accounts or leave their automation
paused until deliberately enabled. Keep model integrations if needed for the same
interactive chat behavior; those calls incur normal provider costs.

A copied GitHub App can still authenticate to the original installation, but its
registered callback/webhook URLs still belong to production. Do not redirect that
registration away from production. For independent registration/lifecycle webhook
tests, connect a separate GitHub App from staging Settings > Projects. Merely
changing `GITHUB_APP_PUBLIC_URL` does not rewrite a registered App's callbacks or
its copied database config. Clear unfinished copied registration attempts first.

Now start staging's remaining services and check:

- Both staging URLs have valid TLS; website buttons open the staging app.
- A persistent STAGING banner appears on app and site, including before login.
- The same access token works and representative chats, history, artifacts,
  workflow files, project worktrees and integration settings are present.
- Authenticated `/api/system` says `environment=staging`, `auth_enabled=true`,
  and every component is `ok`.
- Creating a chat/artifact and restarting workers in staging changes nothing in
  production; compare actual container labels and mounts, not only names in UI.
- Copied schedules remain paused. Explicitly enable only the desired staging jobs.
- Production is still healthy with its original data and routing.

## Recover a blocked project chat

`This chat's project worktree is still in use by another agent` comes from the
broker's in-memory claim or a surviving Docker container with matching project/chat
labels on the same projects volume. It is not a Git lock file copied in a snapshot.
An interrupted copy alone does not prove the cause. Older broker versions may
also lack the volume-scoped check needed when copied chat IDs exist in both stacks.

On the deployment host, list agent containers without printing their environment
(the environment contains credentials and chat content):

```sh
docker ps -a --filter label=nautionette.chat --format 'table {{.ID}}\t{{.Names}}\t{{.Status}}\t{{.Label "nautionette.chat"}}'
```

Match the affected chat ID from its browser URL. Inspect the candidate agent and
the affected environment's broker, substituting their IDs:

```sh
docker inspect --format '{{.Name}} status={{.State.Status}} mounts={{json .Mounts}}' AGENT_ID BROKER_ID
```

Compare the broker's `/projects` volume name with the agent's project mounts.
Never remove a container merely because its copied chat ID matches: the other
environment may still be using its own worktree. If the candidate is confirmed
exited/dead and mounts the affected volume, remove only that container using
`docker rm AGENT_ID` (no `--force` or `--volumes`), then retry the chat. Project
files remain on their named volume. Updated brokers do this cleanup automatically.

For running/paused/restarting/created containers, establish ownership and whether
work is still intended before stopping anything. Do not prune all containers,
delete `.sessions` or Git locks, or restart both environments. If no matching
container remains, an in-memory claim may still exist: drain other agent work
before restarting only the affected broker. A restart does not stop orphaned
dynamic agents. If both brokers mount the same projects volume, stop staging
writes and correct its storage isolation before retrying; do not bypass the guard.

## 4. Enable commit-pinned releases

The simpler path is GitHub Actions -> Coolify; Nautionette does not need a public
release webhook or a polling workflow. `.github/workflows/deploy.yml` handles:

```text
main push -> CI success -> staging at that exact commit
published stable release vX.Y.Z -> require that exact commit in staging -> production
```

Create GitHub environments named `staging` and `production` (no required reviewer
is necessary if publishing a release is the intended human promotion action).
Configure in **each** environment:

| Kind | Name | Value |
| --- | --- | --- |
| Variable | `COOLIFY_URL` | Direct HTTPS Coolify origin, without `/api/v1` |
| Variable | `COOLIFY_APPLICATION_UUID` | That environment's application UUID |
| Variable | `APP_URL` | That environment's HTTPS app origin |
| Variable | `DEPLOY_HEALTH_TIMEOUT_SECONDS` | Optional post-deployment readiness budget in seconds (default `600`, range `1`–`1800`) |
| Secret | `COOLIFY_TOKEN` | Server-side Coolify API token allowing application read/update and deploy |
| Secret | `APP_TOKEN` | That environment's Nautionette access token for component health checks |

In `production`, also set variable `COOLIFY_STAGING_APPLICATION_UUID` to the new
staging UUID. Use GitHub's secret UI/approved secret-management channel, never chat
or committed files. The MCP token and repository Git credential are not a substitute
for these GitHub Actions secrets.

Before enabling the workflow, disable Coolify's built-in push/preview auto-deploy
on **production** and staging, and remove conflicting deploy Actions/webhooks. This
pipeline owns both deployments. Keep production pinned to its currently deployed
commit while switching triggers; never let merging these changes release arbitrary
`main` to production. Confirm API access accepts `git_commit_sha` and
`is_auto_deploy_enabled` on this Coolify version. Then set repository variable
`COOLIFY_DEPLOY_ENABLED=true` and rerun CI on the current main commit to initialize
the staging pin. This feature gate prevents a half-configured pipeline firing.

Test the newly deployed staging app before publishing a non-prerelease release
whose tag matches `v1.2.3`. Annotated/lightweight tags are resolved to full SHAs;
`target_commitish=main` is never used as a pin. Tag protections should forbid
moving/replacing release tags. A late CI run for an older main commit is ignored.
A release of an older commit than current staging is deliberately rejected;
explicitly stage a rollback candidate first rather than silently deploying untested
code. Do not refresh staging data as part of a rollback.

Deployments are serialized, pins read back, the queued deployment is polled, its
actual commit checked, then authenticated component health is checked. After Coolify
reports `finished`, allow up to 10 minutes by default for worker images and components
to become ready; override with `DEPLOY_HEALTH_TIMEOUT_SECONDS` per environment.
Checks retry every 10 seconds (each HTTP request has a 30-second timeout, so the
last check can overrun the readiness budget by up to 30 seconds). Duplicate deliveries
use the same health wait without redeploying; healthy duplicates remain silent.
The job's 65-minute timeout accommodates the 30-minute Coolify deployment wait,
the maximum 30-minute readiness budget, and overhead.

Each failed health attempt logs elapsed time and specific failed checks: environment,
authentication, missing/duplicate/unexpected components, or each known component's
non-OK status. HTTP failures and malformed health responses are also reported and
retried. Logs never include response bodies, component details, tokens, or arbitrary
server-provided strings. A timeout retains the last diagnostic and is explicitly a
post-deployment verification failure, not proof that Coolify failed to deploy.
All environment, authentication and component checks must still pass; failures
stay failed in GitHub, with Coolify history available for diagnosis. A lost deploy
POST response is **not** retried blindly. Do not manually deploy or edit either
app while the pipeline is running. No automatic rollback of databases is attempted.

This pins **source commits**, not identical prebuilt image digests. Compose builds
can still resolve newer base images/dependencies. Immutable multi-image promotion
can be a follow-up if byte-identical artifacts are required.

## Quiet no-op workflows

Successful Nautionette workflows can return:

```python
return {"notify": False, "reason": "No new release / already processed"}
```

Declare `notify` (boolean) and `reason` in the output schema. The run and result
remain in history and `run.finished` is emitted, but no chat is created, no message
is appended and existing chat context/timestamps remain unchanged. A successful
`None` or empty-string result is also silent. Ordinary structured results are not
heuristically interpreted; empty counts alone don't suppress a report. Failures,
timeouts, cancellations and terminations are never hidden by this flag. Raise on
an error rather than returning `notify=False`.

This governs automatic result-to-chat delivery, not direct Fastmail/GitHub/chat
calls inside a workflow. Branch before those calls (and before agent calls) for
no-op inputs. The release Action makes **no Nautionette notification calls at all**;
unrelated events, prereleases and healthy duplicate deployments are silent.
