# GitHub repository rename: nautionette → denkzeug

This is a **source repository rename**, not a runtime identity migration. Preparation
can be deployed while the repository is still named `StromFLIX/nautionette`.
Do not rename the repository, switch live settings, or publish a release as part
of preparation. The actual cutover is a separate operation.

## Prepared safeguards

- Smoke uses GitHub repository ID **1353247647**, which survives a rename but is
  different for forks.
- Upstream CI keeps publishing tested images to **`ghcr.io/stromflix/nautionette/`**
  after the rename. Existing `AGENT_IMAGE_REGISTRY_PREFIX` values remain correct.
  The optional Actions variable `AGENT_IMAGE_REPOSITORY` controls a later,
  separately coordinated registry move. It is not necessary for this rename.
- Verified `repository`/`push` webhooks already refresh saved project names by
  numeric repository ID. Re-adding the renamed repository through the same
  GitHub connection also refreshes metadata if the webhook was missed, without
  creating a duplicate project or replacing its checkout. Agent startup updates
  origin on the next turn, preserving detached HEADs, local commits, dirty files,
  chat IDs and baseline refs. Active turns retain their old credential scope;
  finish them before cutover, then start a new turn.
- Public repository links, clone instructions and the release-preparation
  repository constant are synchronized from **`branding/brand.json`**. The
  current, working URL deliberately remains unchanged until cutover. Release
  tag/release inventory uses that same repository constant, not a second literal.

## Before the rename

1. Deploy preparation to staging and verify CI, project tests and an actual agent
   turn. Finish active development/release/refresh operations. Pause new deployments
   for the cutover window; record and later restore their current enablement, rather
   than assuming they were enabled. Let in-flight CI/deploy/refresh jobs finish.
2. Record the current main/staging/production SHAs, Coolify Git repository settings,
   application UUIDs, registry prefix and image publication override (if set).
   Keep the existing tested images for rollback and verify current backups.
3. Confirm `StromFLIX/denkzeug` is available. Rename the **existing** GitHub repository
   in Settings; do not create a replacement repository or transfer ownership.
   Issues, pull requests, history and the numeric repository ID must stay attached
   to the same repository.
4. Check external consumers not represented in this checkout: GitHub Apps, deploy
   keys, organization rulesets, required-workflow references, reusable Actions,
   external badges/docs, MCP grants scoped by owner/repo, and any OIDC trust policy
   containing `repo:StromFLIX/nautionette:…`. Secrets/environments normally remain
   attached to the repository, but verify access rather than copying credentials.

## Cutover

1. Rename the existing repository to `denkzeug` in GitHub Settings and verify its
   numeric ID is still `1353247647`. Do not reuse the old repository name: doing so
   removes GitHub's old-name redirects. Do not rely on redirects for agent Git:
   its repository-scoped helper deliberately disables HTTP redirects.
2. Update developer checkout origins, without resetting or recloning:

   ```sh
   git remote set-url origin https://github.com/StromFLIX/denkzeug.git
   git fetch origin
   ```

   For Denkzeug-managed checkouts, verify the signed rename webhook updated the
   existing project. If not, re-add `StromFLIX/denkzeug` under the **same** Projects
   GitHub connection. Confirm the project ID and saved work are unchanged. Start
   a fresh turn so origin, system context and credentials all use the new name.
   If GitHub access needs repair, repair the existing installation; do not delete
   the project or its volume to work around an authentication failure.
3. Set `repository` in `branding/brand.json` to
   `https://github.com/StromFLIX/denkzeug`, then run:

   ```sh
   python scripts/sync_brand.py
   python scripts/sync_brand.py --check
   python services/docker-broker/nautionette_docker_broker/image_context.py --write-lock
   uv run pytest tests/test_branding.py tests/test_readme.py tests/worker/test_prepare_release.py
   ```

   Review the diff, commit and push to main. Sync updates generated website,
   frontend, desktop and agent metadata, README/self-hosting clone instructions,
   pipeline/staging repository references, and the release workflow's `REPO`.
   Updating agent branding changes image contexts, so the fingerprint lock must
   also be regenerated. Do not hand-edit generated brand copies. Historical
   audit links and lowercase GHCR paths deliberately stay unchanged.
4. Update the **existing** Coolify applications' Git repository setting to
   `StromFLIX/denkzeug`:

   - staging: `tstmjujqyyl3zkvtwb0sx8nc`
   - production: `88veejhhzve4rjcnxzlyjzbo`

   Keep application UUIDs, branches, pinned SHAs, Compose configuration, storage,
   registry prefixes and domains. Verify GitHub App repository access and webhook
   delivery. The deployment scripts deliberately require the configured repository
   to equal `GITHUB_REPOSITORY`; stale settings should block, not be bypassed.
5. Update the **deployed** `prepare_nautionette_release` workflow from the reviewed
   repository source using the workflow deployment tool. Committing its file alone
   does not update `/workflows`. Inspect/diff the live version first: it may be
   older or independently edited. Keep its workflow name and schedule unchanged,
   avoid replacing code while an old execution is active, check deployment readiness,
   then run it and inspect the complete run result. Resolve real staging/release
   blockers; never interpret a started run as success or publish a release to test it.
6. Restore the recorded deployment enablement and run CI for the cutover commit.
   Verify successful image publication to the **existing** registry namespace and
   package access. Renaming a source repository must not require new GHCR paths.

## Verification gates

- CI succeeds at the exact intended SHA; Smoke remains eligible on the renamed
  repository and remains excluded on forks. Run Smoke on demand when its normal
  model integration/secrets are configured.
- Staging's deployment finishes at that SHA; authenticated `/api/system` is
  healthy, including worker/agent image readiness. A Coolify `running` container
  alone is insufficient. Verify webhook deliveries and no skipped deployment gate.
- Start an actual staging agent turn using the same project ID. Check Git origin,
  repository-scoped fetch/push access (use an authorized disposable test ref), and
  preservation of earlier local commits/uncommitted work. Remove only the test ref.
- Check website repository/download links and self-hosting clone instructions.
- Run release preparation: reads, comparison/source URLs, staging repository guard,
  and proposed tag/push commands must all target `StromFLIX/denkzeug`. Preparation
  must not tag, publish, or deploy anything.
- Promote to production only through the usual tested-commit/release gates. Verify
  production health and an actual agent operation after promotion.

## Keep these identities unchanged

Do **not** globally replace `nautionette`. Preserve Python imports/package names,
`NAUTIONETTE_*` configuration, Docker labels/networks/volumes, `STACK_NAMESPACE`,
Compose project identity, `/var/lib/nautionette-*` paths, Temporal task queues and
workflow names, browser storage keys, mini-app SDK/bridge names, mobile/desktop
`dev.nautionette.app`, `nautionette://` OAuth links, artifact filenames, public
instance domains and OAuth callback URLs.

For self-hosted upgrades, keep the existing checkout directory and Compose
project name. Moving from a directory called `nautionette` to `denkzeug` can
change Compose's default project name and attach **new empty volumes**, even if
`docker-compose.yaml` is unchanged. Record the current Compose project name;
explicitly retain it with `COMPOSE_PROJECT_NAME` or `docker compose -p` if moving
the checkout directory. Never run `down -v` or recreate storage for this rename.

## Rollback and optional image move

Keep deployment settings paused if a gate fails. Correct the repository setting,
App access or metadata in place; keep the same project and volume identities.
Use the normal deployment rollback process with a known tested image/commit;
never force-move release tags. If reverting the GitHub name is necessary, first
check availability and coordinate Git origins, Coolify, branding and the deployed
release workflow together. Do not assume GitHub redirects repair credentials.

A future GHCR move is separate: set Actions `AGENT_IMAGE_REPOSITORY` to the new
`owner/repository`, run CI and verify all three fingerprint-tagged packages
(`pi-base`, `pi-agent-default`, `miniapp-builder`) and their pull permissions.
Only then switch staging's `AGENT_IMAGE_REGISTRY_PREFIX` to the matching
`ghcr.io/<owner>/<repository>/pi-`, cold-pull and test agent/mini-app operations,
and promote the setting to production. Keep old packages and recorded settings
for rollback. Also confirm rollback commits' image fingerprints exist in the
selected namespace; a source repo rename is not a reason to prune old packages.
