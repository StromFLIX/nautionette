# Nautionette Git/release evidence dashboard

Native dashboard: `/dashboards/nautionette-git-release?agent=d75b7c63a380`.
The saved owner is the agent profile, not a chat ID. This is a declarative
configuration for the existing dashboard platform, not a separate web app.

## Sources and controls

- `nautionette-git-release.json` is the published manifest. All evidence comes
  from authorized read-only integration sources or a schema-checked snapshot;
  no literal data pretends to be live.
- `nautionette-source-revision.json` is the registered recipe
  `nautionette-source-revision@1`, reused by main, staging, production and runtime.
  Existing cards keep that immutable revision when another recipe is published.
- `../workflows/nautionette_git_release_status.py` is the deterministic collector.
  Its only mutation publishes bounded dashboard data. It does not call a model,
  tag/release/deployment tool, or notification tool. Successful completion returns
  `notify: false`; failures raise rather than being hidden as successful no-ops.
- **Refresh exact-SHA evidence** binds the current main SHA server-side. The
  workflow rereads main, verifies exact-SHA repository/main CI and actual staging
  deployment history, and detects main/staging changes during collection. A source
  pin alone does not prove deployment. The latest release tag is shown as the
  production version only if its resolved commit matches production deployment
  history and the authenticated production runtime SHA.
- **Prepare release notes (never publish)** references the existing read-only,
  model-assisted `prepare_nautionette_release` workflow. It requires confirmation
  and is not part of automatic refresh.
- There is **no production publishing or deployment control**. Release write
  permissions and authenticated staging component health are explicitly unverified.
  Container health is identified as Coolify health, not application-level proof.
  Evidence is never permission to deploy: promotion must independently revalidate
  exact SHA, fresh CI, permissions, immutable unused tag/idempotency, deployment
  serialization and authenticated post-deployment component health.

## Freshness and execution

While open, the platform refreshes direct feeds deterministically (60-second
refresh interval, 180-second staleness threshold). The verification snapshot has
an independent 300-second threshold and is recomputed by the refresh action.
No scheduled model loop or chat polling is involved. No unattended workflow
schedule is installed: scheduling requires an explicit cadence and IANA timezone.

Native `empty`, `fresh`, `stale`, `denied` and `error` states remain visible. A
fresh PR table with zero rows is a successful empty result, not a failed request.
Denied sources must not expose cached data; integration errors may retain the
last good value with an explicit error. Stale evidence is not release readiness.
The public GitHub Actions read can fail/rate-limit; the collector raises and the
previous snapshot ages rather than presenting fabricated success.

Action requests use the platform's context hash, revision fence, fresh server-side
binding validation and durable request ID. Retry an uncertain invocation with
**the identical request and request ID**, never a new request ID. Check run history
for completion; a started run is not success. Snapshot publication also checks
the current owner and revision. The application UUIDs and repository constants
are intentionally installation-specific; credentials stay in integrations.

## Editing and recovery

Git stores portable source, not a live database or permission configuration.
Committing these files does not automatically replace dashboard state.

1. Read the registry, allowed read tools, existing dashboards and current revision.
2. Reuse the existing recipe and dashboard; validate the full proposed manifest.
3. Publish through `put_dashboard` with the just-read `expected_revision`. Preserve
   the user's current pin/navigation choice rather than copying an old pin value.
4. On a conflict, reread and reconcile. Never overwrite another editor's revision.
5. Restore an earlier layout with `restore_dashboard`; this creates a new revision
   and retains all history. Do not archive/recreate the dashboard to roll back.
6. Publish workflow changes through the workflow deployment tools, check `ready`
   and worker restart results, execute a read-only test and inspect its run history.
   Do not assume a Git edit deployed the live workflow.

## Verification

```sh
uv run pytest tests/test_git_release_dashboard.py \
  tests/backend/test_dashboards.py tests/backend/test_dashboard_transports.py
cd services/frontend
npx playwright test tests/dashboards.spec.js --workers=1
```

The evidence workflow's tests also run in CI with the platform dashboard tests.
They cover foreign/fork/preview/wrong-SHA evidence, incomplete and failed CI,
missing/failed deployment history, denied/failed API reads, main/staging races,
owner changes, malformed structured results, publication revision conflicts,
release/runtime mismatch, silent success and the saved declarative contracts.

The live dashboard was additionally exercised with a rejected invalid component,
a stale-write conflict, restoring revision 1 as a new revision, an empty snapshot,
valid publication, rejected snapshot schema, expiration of a 15-second diagnostic
snapshot, a transient integration error retaining good data and recovery. The
harmless refresh action completed and replaying its identical request returned
the same completed workflow ID. The temporary diagnostic card was then removed.
No permissions were revoked from the user's agent to simulate denial; denial is
covered in isolated platform and workflow tests instead.

A native snapshot of the published composition was rendered locally through the
real frontend with mocked transport at 1440, 900, 390 and 320px and all four base
themes (eight passing checks, no overflow or mobile reordering). The generic
platform browser suite separately covers skins, source errors, navigation and
uncertain action replay. These are local browser checks, not a claim that an
unauthenticated browser session exercised the production UI.
