# GitHub owns deployments and staging refreshes

Both operations run from **StromFLIX/nautionette → Actions**, not from a Nautionette
workflow. Replacing production cannot kill the GitHub runner. Neither pipeline
creates Nautionette chats or sends result notifications. Skips/no-ops stay silent
in Nautionette; failures remain visible in GitHub Actions.

The initial staging data clone is already live; see [the verified bootstrap
record](staging-setup-status.md). The new repeatable refresh Action must still be
configured and dry-run against the host before its first real use. Local tests
simulate orchestration and failures; they are not a live volume restore test.

## 1. Create these GitHub environments

Repository **Settings → Environments → New environment**:

- `staging`
- `production`
- `staging-refresh`

Limit all three to the **main** deployment branch. The deploy job checks out its
control script from main; stable release events also need access to `production`.
In the production environment's deployment rules, allow release tags matching
`v*` in addition to main (GitHub evaluates the event ref, not checkout's ref).
Protect main and release tags against unreviewed changes/tag replacement.
Environment reviewers are optional; publishing a stable release and explicitly
confirming a destructive refresh are already deliberate operations.

### Staging environment

| Kind | Name | Value |
| --- | --- | --- |
| Variable | `COOLIFY_URL` | `https://coolify.admin.stromflix.com` |
| Variable | `COOLIFY_APPLICATION_UUID` | `tstmjujqyyl3zkvtwb0sx8nc` |
| Variable | `APP_URL` | `https://app.nautionette.stage.stromflix.com` |
| Secret | `COOLIFY_TOKEN` | Permanent Coolify API token with application read/update/deploy access |
| Secret | `APP_TOKEN` | Existing staging Nautionette login/access token |

### Production environment

| Kind | Name | Value |
| --- | --- | --- |
| Variable | `COOLIFY_URL` | `https://coolify.admin.stromflix.com` |
| Variable | `COOLIFY_APPLICATION_UUID` | `88veejhhzve4rjcnxzlyjzbo` |
| Variable | `COOLIFY_STAGING_APPLICATION_UUID` | `tstmjujqyyl3zkvtwb0sx8nc` |
| Variable | `APP_URL` | `https://app.nautionette.stromflix.com` |
| Secret | `COOLIFY_TOKEN` | Permanent Coolify API token with production read/update/deploy and staging read access |
| Secret | `APP_TOKEN` | Existing production Nautionette login/access token |

The two APP_TOKEN values are currently the same because staging deliberately uses
the same login. They are **not** Coolify tokens and are **not** INTERNAL_TOKEN.
Use the narrowest abilities/resource/team scope your Coolify version supports;
do not claim application-scoped permissions if the installed version only offers
team-wide abilities. Never use the temporary administrator token from chat here.

### Staging-refresh environment

| Kind | Name | Value |
| --- | --- | --- |
| Variable | `PRODUCTION_APPLICATION_UUID` | `88veejhhzve4rjcnxzlyjzbo` |
| Variable | `STAGING_APPLICATION_UUID` | `tstmjujqyyl3zkvtwb0sx8nc` |
| Variable | `REFRESH_SSH_HOST` | `94.130.151.7` |
| Variable | `REFRESH_SSH_PORT` | `22` (change if the server actually uses another port) |
| Variable | `REFRESH_SSH_USER` | `root`, or a dedicated account with passwordless sudo |
| Variable | `COOLIFY_URL` | `https://coolify.admin.stromflix.com` |
| Secret | `REFRESH_SSH_PRIVATE_KEY` | Entire private key of a **new dedicated** SSH key authorized on this host |
| Secret | `REFRESH_SSH_KNOWN_HOSTS` | Verified SSH host-key line for the exact host/port above |
| Secret | `COOLIFY_TOKEN` | Permanent Coolify token allowing staging application read/update (for the maintenance guard) |

Copying private volumes, controlling Docker, and installing a systemd recovery
unit need **root-equivalent host access**. A Coolify API token is not an SSH key.
Do not reuse your personal SSH key or the Coolify server's private SSH key.
The refresh key belongs only in `staging-refresh`, not release environments or
repository-wide secrets. Restrict who can modify main/workflows and access this
environment. No port forwarding, SSH agent forwarding or interactive PTY is needed.

Generate a dedicated unencrypted automation key on your trusted workstation:

```sh
ssh-keygen -t ed25519 -N '' -f ~/.ssh/nautionette-refresh -C github-nautionette-refresh
```

Using your normal trusted server console/admin connection, append its **public**
key to the selected account's `~/.ssh/authorized_keys`, prefixed with `restrict `.
Keep the private file on the workstation and put its full contents in the GitHub
secret. For a non-root account, the pipeline runs `sudo -n /usr/bin/python3 ...`;
permission to execute this Python is root-equivalent, not a limited deployment role.

Obtain the server's **public** host key from the trusted console, for default port 22:

```sh
sudo awk '{print "94.130.151.7 " $1 " " $2}' /etc/ssh/ssh_host_ed25519_key.pub
sudo ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub
```

Put the first output line in `REFRESH_SSH_KNOWN_HOSTS`. For a nonstandard port,
use `[94.130.151.7]:PORT` instead of `94.130.151.7`. Verify against your trusted
console, not an unauthenticated `ssh-keyscan` result fetched during the workflow.
The runner enforces StrictHostKeyChecking; a changed server key fails closed.

The deployment host must have `/usr/bin/docker`, `/usr/bin/python3` (3.11+),
`/usr/bin/systemd-run`, `/usr/bin/systemctl`, and GNU `/usr/bin/du`, and be reachable
from the GitHub-hosted runner over SSH. If your firewall restricts SSH, use an
appropriate runner/private access path rather than disabling SSH authentication.
Backup disk must have ample free space; preflight conservatively reserves twice
the combined production/staging data size plus 512 MiB on relevant filesystems.

### Repository-level variable (last step)

**Settings → Secrets and variables → Actions → Variables → New repository variable**:

```text
COOLIFY_DEPLOY_ENABLED = true
```

Set this only after staging/production environment secrets and variables are ready.
Keep Coolify's built-in push and preview auto-deploy disabled on both apps. GitHub
is the single deploy owner. No GitHub PAT, webhook secret, Nautionette webhook,
Coolify CLI installation, or root Coolify token is required by these Actions.
The temporary root setup token can be revoked; replace it with lasting tokens in
the environment secrets above, not with another chat-pasted credential.

## 2. Routine releases

```text
main push → successful CI → staging deployment at CI's exact commit
publish stable vX.Y.Z release → require that commit currently in staging → production
```

After enabling, use **Actions → CI → Run workflow → main** to initialize staging
without making a dummy commit. Successful main-branch manual CI runs are accepted,
as are successful push CI runs. PRs, failing CI, late results for older main commits,
invalid tags, drafts, prereleases and healthy duplicate deployments cause no deploy.

Test staging, then publish a **non-prerelease** GitHub release (e.g. `v1.2.3`) tagged
at that tested commit. The Action resolves the tag to a full SHA, checks staging,
pins Coolify, verifies the queued deployment's actual commit, and checks authenticated
component health. It never means "deploy whatever is now on main". It promotes
source commits, not identical prebuilt image digests. No data copy happens on release.

A newer staging commit supersedes the previous release candidate. Publishing a
release for an older commit is rejected instead of silently deploying untested code.
Rollback requires deliberately staging/testing that candidate first; never restore
production databases automatically during a code rollback.

### Faster Pi image startup

CI now builds the Pi base and default agent set as one BuildKit graph with
persistent GitHub Actions layer caches (`images/agent-images.hcl`). The expensive
Chromium/OS installation is separate from the Pi npm package, so a Pi version
bump does not download/install Chromium again. Runtime JS and extensions remain
in the final, cheap layers.

After the offline image tests pass, **main push/manual CI only** publishes:

- `ghcr.io/stromflix/nautionette/pi-base:<base-context-hash>`
- `ghcr.io/stromflix/nautionette/pi-agent-default:<agent-and-base-context-hash>`

The publish step uses the job's scoped `GITHUB_TOKEN` (`packages: write`); PRs do
not log in or publish. Publication must succeed before CI can trigger Deploy.
Fingerprints use the same stdlib module as the broker, not the commit SHA or
`latest`: backend/frontend-only changes reuse the same agent images. These are
source-context tags, not OCI digest pins; protect package write access. Only the
runner's native Linux/amd64 platform is published for now. Other platforms fall
back to a native local build. Adding an agent set requires adding its Bake target,
offline tests and publication step alongside the existing default set.

**One-time rollout:**

1. Merge the changes and wait for a successful main CI image publication. Ensure
   repository/organization policy permits this job's `packages: write` permission.
2. For this public repository's non-secret runtime images, make the two GHCR
   packages public in their package settings if anonymous pulls are desired.
   New GHCR packages are private by default. Do not make packages public if you
   have added private code/configuration to the image contexts. Alternatively,
   keep them private and supply read-only registry auth via a read-only Docker
   `config.json` mount in the **broker** (the Python Docker client's config, not
   only a login on the deployment host). Never put registry credentials in agent
   environment variables or the Compose file.
3. Set `AGENT_IMAGE_REGISTRY_PREFIX=ghcr.io/stromflix/nautionette/pi-` on staging,
   redeploy and verify `/api/system` reports `image_status: ready`. Broker logs
   should say `pulling` / `pulled`, or `already present`, not `building`.
4. After staging verification, configure the same registry prefix in production
   and promote the tested commit normally. Keep each stack's `IMAGE_PREFIX` and
   `BASE_IMAGE` namespaced as before.

The prefix defaults to empty to preserve offline/local installs. With it enabled,
startup uses **local exact tag → pull exact source tag → local build on failure**.
Registry failures are visible in the broker logs. A forced `/images/rebuild`
intentionally bypasses the registry. Source build contexts remain in the broker
for recovery after pruning or a registry outage. Missing/pruned images are still
unhealthy until actually ready; this does not weaken the deployment health gate.

The first cold CI build remains expensive. Subsequent CI builds reuse cached
layers; deployments download missing layers rather than running apt/npm/browser
installation. This reduces the post-startup degraded window, but is not a promise
of zero downtime: Coolify's Compose build, worker draining and Temporal startup
still take time. Verify a cold-pull staging deployment before measuring the gain.

## 3. Refresh staging data explicitly

**Actions → Refresh staging data → Run workflow → branch main**:

1. Keep operation `refresh` and **dry_run=true** first. This checks authenticated
   health, actual container/volume ownership, separate networks, PostgreSQL image
   and role compatibility, and free disk. No service is stopped or data copied.
2. Finish/stop agent calls in both environments before a real refresh. Unexpected
   active agents/other containers mounting these volumes cause a refusal, not a kill.
3. Plan a maintenance window: a physical, cross-volume consistent snapshot pauses
   production writes and makes the app temporarily unavailable. Duration depends on
   dataset sizes and disk throughput; copying and byte verification are not instant.
4. Run again with **dry_run=false**, entering exactly **OVERWRITE STAGING**.

The real run:

1. Prefixes staging's Coolify description with a run-specific maintenance marker.
   The release script rejects deployments while this marker exists. It persists if
   GitHub is cancelled/disconnected, unlike GitHub's concurrency lock.
2. Starts an independent root-owned systemd job on the host. Original container IDs,
   restart policies and actual volume mappings are recorded privately and durably.
3. Stops staging writers and makes a verified `staging-before` backup.
4. Stops production writers (broker before workers/backend, Temporal before Postgres).
   PostgreSQL must stop cleanly. Copies all six datasets from **read-only** source
   mounts into `production-snapshot`, preserving ownership, permissions, dotfiles,
   symlinks and hardlinks. Verifies file bytes and ownership/modes/symlink targets.
5. Restarts and health-checks production **before** modifying staging volumes.
6. Restores the snapshot to independent staging volumes and verifies it. Starts only
   staging Postgres/Temporal, pauses schedules in all user namespaces, terminates
   copied open executions, and requires repeated clean checks. No staging worker or
   backend runs during quarantine. The internal `temporal-system` namespace is not
   treated as a user namespace.
7. Starts staging, checks both environments, records completion, and removes only
   this run's Coolify maintenance marker, preserving the original app description.

The datasets are backend SQLite, gateway integrations/credentials, PostgreSQL
(all Temporal databases), workflow files, artifacts, and projects including caches
and per-chat worktrees. It does not copy/change Coolify configuration, URLs, service
INTERNAL_TOKENs, Docker images or release pins. STAGING banners and isolated volume
names remain. Ensure staging code can read/migrate production's DB schema; incompatible
schema changes can fail staging health and require a deliberate restore/code repair.
A physical PostgreSQL refresh requires matching image IDs and role env configuration.

The local data is cloned, **not external accounts**. Copied integrations still use
real model/email/GitHub/Coolify accounts. Manual staging actions can have real effects.
Schedules/open workflows are quarantined; startup also pauses copied active/queued
chat turns on versions with queue recovery. Review integrations before resuming work.

## 4. Disconnects, failures, backups

Deploy and refresh share a GitHub concurrency group; they do not overlap through
these workflows. Do not manually deploy/edit either Coolify app while refreshing.
The persistent description marker additionally blocks GitHub deployment after a
runner cancellation. Do not remove it just to make a release pass.

The host job has a 90-minute runtime limit. `ExecStopPost` removes any copy helper
before recovering production. A boot recovery service also handles a host reboot.
An interrupted, **unquarantined** staging restore stays stopped with restart policies
disabled. It never gets started merely because a cleanup handler ran. A host/disk
failure can still require operator recovery; this is not high availability.

If the runner disconnects, choose **operation=check-existing-run** and enter the
original host run ID printed in GitHub (e.g. `123456789-1`). This only polls the
existing host job; it never takes another snapshot or overwrites staging. It clears
the matching maintenance marker **only if that original run completed**. If the guard
was already cleared, inspect the successful original run instead of replaying it.

Root-only host inspection (substitute the real run ID):

```sh
sudo python3 /var/lib/nautionette-refresh/runs/RUN-ID/staging_refresh_host.py status /var/lib/nautionette-refresh/runs/RUN-ID
sudo systemctl status nautionette-refresh-RUN-ID.service
sudo journalctl -u nautionette-refresh-RUN-ID.service
```

To abort a still-running host copy, stop **its systemd unit** so recovery executes;
do not kill/prune all Docker containers. If an operator needs to retry recovery
after a recorded recovery failure, inspect the private state and run the script's
`recover` command on the same path after the unit has stopped. Do not recreate
containers first: recovery refers to the recorded original IDs.

If failure happened before host launch, or the host recovered production but staging
needs repair, keep the maintenance marker until an operator verifies the host is idle
and both apps are safe. Then remove only `[nautionette-refresh:RUN-ID] ` from staging's
Coolify description. A failed backup does not qualify as a successful refresh.
There is no automatic production data restore, and no blind automatic staging rollback.
The `staging-before` backup supports a deliberate staging-only recovery.

Private backups and state remain at:

```text
/var/lib/nautionette-refresh/runs/RUN-ID/
  state.json
  staging_refresh_host.py
  staging-before/{six datasets}
  production-snapshot/{six datasets}
```

No backups are uploaded to GitHub artifacts. These contain production secrets and
personal data: the parent directory is root-only (0700). Set your own retention policy;
there is **no automatic deletion**. After verifying a newer backup and deciding old
copies are no longer needed, remove only reviewed old dataset directories. Retain
active-run state/scripts required by `nautionette-refresh-recovery.service`. The
one-time bootstrap snapshot Docker volume documented in the setup record is separate.
