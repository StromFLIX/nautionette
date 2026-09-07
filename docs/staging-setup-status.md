# Staging setup — verified 2026-09-07

## Live environments

| | Production | Staging |
| --- | --- | --- |
| Coolify application | `88veejhhzve4rjcnxzlyjzbo` | `tstmjujqyyl3zkvtwb0sx8nc` |
| App | https://app.nautionette.stromflix.com | https://app.nautionette.stage.stromflix.com |
| Site | https://nautionette.stromflix.com | https://nautionette.stage.stromflix.com |
| Deployed source commit | `1e4e50b54b33f3b2416fc450c5e6856de67b057a` | `1e4e50b54b33f3b2416fc450c5e6856de67b057a` |
| Push/preview auto-deploy | Disabled | Disabled |
| Automation | Original schedules retained | Both copied schedules paused |

Production was deliberately promoted to the staging-tested setup commit through
Coolify deployment `il4qiov6nggxls4vkigtavpo`, finished at
`2026-09-07T16:55:43Z`. This was a manual bootstrap deployment, **not a test of the
GitHub release trigger**. Source pins remain in place while Actions credentials
are missing.

## Independent data snapshot

Production agents were paused, writers stopped, and PostgreSQL shut down cleanly.
All six datasets were copied from read-only production mounts into a backup:

- backend SQLite data (chats, messages, history index, settings, project metadata)
- agentgateway runtime integrations and credentials
- PostgreSQL (Temporal databases)
- workflow files
- artifacts
- projects, including repository caches and per-chat `.sessions` worktrees

Production resumed at `2026-09-07T16:47:41Z`, before staging restoration and
quarantine. A separate watchdog was in place to recover production if the
orchestrator or agent died; normal recovery completed without watchdog intervention.

All six restored datasets were byte-compared against the snapshot. The initial
`diff` verification stopped on existing virtual-environment symlinks whose targets
are outside the copy container. Verification was corrected to use
`diff --no-dereference`; all datasets matched, including symlink targets. No second
production snapshot or production restore was needed.

Backup retained on `hetzner-1`: Docker volume
`nautionette-prod-snapshot-20260907`, with root directory permissions `0700`.
It contains sensitive production data and integration credentials; do not publish
it. Retention/removal is a separate maintenance decision. `progress.log` and the
`snapshot-complete`, `production-resumed`, `quarantine-complete`, and
`clone-complete` markers record the operation.

## Verified results

- Both applications report authenticated `/api/system` with every component `ok`.
- Staging reports `environment=staging`, production `environment=production`.
- The same nonempty application login token works in both environments; their
  internal service tokens differ.
- Both expose 38 copied chats, all six workflows, and the copied project.
- Public HTTPS endpoints return 200 with valid TLS verification.
- Website runtime configuration returns `staging` and the staging app URL; its
  served JavaScript includes the STAGING banner. The app banner is enabled by
  runtime environment and staging hostname, including before login.
- Both brokers' workflow-volume names match their actual backend/worker mounts.
  All persistent mounts inspected belong to the correct application UUID;
  staging internal networks and mutable images are separate.
- Staging's managed backend MCP successfully answers with 33 tools, confirming
  reconciliation with its different internal token.
- Quarantine found namespace `default`, paused two copied schedules, and found
  zero open Temporal executions to terminate. The copied
  `github_weekly_activity_digest` and `hn_daily_digest` schedules remain paused.
- Temporary `notify=False` workflows were deployed, run, and inspected in each
  environment. Both completed successfully; the chat list remained exactly
  unchanged. The temporary workflow files were deleted afterward; run histories
  remain for audit:
  - Staging: `staging_silent_noop_check-1788799975-b2d478`
  - Production: `production_silent_noop_check-1788800245-f28c85`
- Worker restarts during those tests targeted only the correct environment.

Staging contains the same remote integration credentials. **Manual staging actions
can still send real email, change real GitHub repositories, or deploy production.**
The local data is isolated; external accounts are not automatically sandboxed.
The copied GitHub App's registered callbacks still belong to production; use a
separate GitHub App for independent registration/webhook lifecycle tests.

## Remaining blocker: automatic GitHub releases

The release Action and commit-pinning script are committed, but automation remains
**off**, not half-enabled. The available repository credential does not provide
GitHub Actions environment/secret/variable management. The temporary Coolify root
setup token must not be used as the lasting deployment credential.

Configure the `staging` and `production` GitHub environments as documented in
[`staging.md`](staging.md#4-enable-commit-pinned-releases):

- Variables in each: `COOLIFY_URL=https://coolify.admin.stromflix.com`,
  `COOLIFY_APPLICATION_UUID` from the table, and that environment's `APP_URL`.
- Production variable: `COOLIFY_STAGING_APPLICATION_UUID=tstmjujqyyl3zkvtwb0sx8nc`.
- Secrets in each: permanent limited `COOLIFY_TOKEN` (read/update/deploy) and
  the existing `APP_TOKEN`, entered through GitHub secret settings.
- Then repository variable `COOLIFY_DEPLOY_ENABLED=true` and a new main push/CI
  completion to initialize staging at the latest commit.

Publishing a stable `vX.Y.Z` release then promotes the exact commit only if it is
currently deployed to staging. Neither the Action nor successful no-op workflow
results send Nautionette chat messages. Failures are not suppressed.

The existing Python lint/format CI failures were fixed in a formatting-only
follow-up: all tracked Python ASTs are unchanged, lint/format checks pass, the
full local test suite passes (557 passed, 3 skipped), and both seed workflows
validate. These checks do not substitute for the still-unconfigured end-to-end
GitHub release test.
