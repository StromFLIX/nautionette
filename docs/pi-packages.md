# Managed Pi packages

Saved agents can select Pi extensions, skills and prompt templates from immutable,
shared package artifacts. **Settings → Pi packages** is the configuration surface;
the agent editor also includes the same controls. No per-agent image or manual
configuration-file editing is required. Global defaults select no packages.

## Use

1. Search using the existing Settings search (or the package section's search).
   Results come from npm's `pi-package` keyword catalog. Search is cached,
   debounced and paginated; registry failures offer retry without losing the form.
2. Choose a saved agent. Download a result or enter `npm:package@version`,
   `npm:@scope/package@version`, or a public `https://github.com/owner/repo@ref`.
   Private repositories, local paths, arbitrary hosts and credential-bearing URLs
   are not supported. Tags/default branches are resolved once at installation.
3. Add a successfully downloaded artifact to that agent. Under **Configuration &
   resources**, choose resource types or Pi path filters. Omit a type for all its
   resources; `[]` disables it. Pi themes do not change Nautionette's web theme.
4. Enter any required environment variables/configuration files in Settings,
   choose **Use configuration revision**, then **Save agent packages** (or
   **Save agent** in the agent editor). These are explicit, separate steps.
5. Start a new chat with that agent, or explicitly reapply the agent in an existing
   chat. After Pi reports its commands on a turn, typing `/` offers discovered
   extension, prompt and skill commands. Historical messages have native Pi roles,
   so the current slash command still starts at input offset zero.

Downloads do not automatically change any agent. Editing an agent does not change
existing chats. Accepted/queued turns retain their package/configuration revision
IDs even if the agent or chat is subsequently edited or the agent is deleted.
Package order is significant. Updating means installing a new artifact and
selecting a new revision; there are no turn-start downloads or silent upgrades.

## Configuration and secrets

Packages do not have a universal configuration schema. This version provides an
advanced JSON editor, not guessed per-package forms. Follow the package's own
documentation to supply supported environment variables or configuration files:

```json
{
  "env": { "SERVICE_API_KEY": "value entered only in Settings" },
  "files": { "agent/service.json": "{\"region\":\"west\"}" }
}
```

- Values are write-only strings. Reads return `null`; submit `null` to keep a
  previous value. Omit a key to remove it. Each save creates an immutable revision.
- Files are limited to simple `agent/<name>.json|yaml|yml|toml|txt` paths in the
  per-call Pi home. Core settings/auth/models/trust files and runtime-control
  environment variables are reserved. Total configuration is limited to 64 KB.
- Conflicting environment keys/file paths across selected packages are rejected.
- Configuration is Fernet-encrypted in SQLite with an automatically created,
  mode-0600 `pi-packages.key` in the backend data volume. Back up that key with
  the database. Missing/corrupt keys fail closed; they are not silently replaced
  during reads. Encryption does not protect a compromised backend host.
- Agent/catalog/chat/turn records contain IDs, not secret values. Values are
  decrypted immediately before execution, sent through the internal broker API,
  copied separately to a mode-0600 private container file and unlinked after
  materialization. They are excluded from `/workspace/JOB.json`.
- Extensions run in the agent process and can access supplied configuration,
  tools and selected projects. They can reveal secrets through their own output
  or tools. This is **not** a sandbox against an untrusted extension.

Removing a selection does not revoke credentials or erase old revision values.
Historical snapshots intentionally retain those values. Rotate external
credentials to revoke them; artifact/revision garbage collection and secure
revision erasure are not implemented yet. Do not delete referenced Docker volumes.

## Installation and runtime boundaries

The broker installs using the existing default agent image and a fixed installer
entrypoint. The installer has a fresh artifact volume, a read-only root, bounded
resources, no application credentials, projects or Docker socket, and an isolated
temporary bridge network with egress. This avoids the application's container
network but is **not** a general firewall against hostile code/private IPs.
Npm lifecycle scripts are disabled unless explicitly enabled in Settings. Enabling
them executes third-party install code. Only install packages you trust.

Artifact volumes are stack-namespaced and immutable; runtime mounts are read-only.
A missing volume fails rather than creating an empty replacement. Install metadata
records the resolved npm version/integrity or Git commit and declared resources.
Failures leave the previous selection intact and can be retried as new installs.
Interrupted backend installs become failed; the broker bounds orphan installer
lifetimes. Orphan volumes are retained because a successful publication may have
lost its response. There is no automatic artifact garbage collection yet.

Managed local package sources are appended to Pi settings without replacing the
built-in gateway/internet extensions. Package tools do not change gateway MCP
selection/authorization. Native per-call session seeding preserves text/image
history but does **not** persist extension session entries, auth sessions, caches,
custom messages or extension state between turns.

Installation success is not a compatibility guarantee. RPC-compatible extensions,
Pi skills and text prompts are supported. Terminal widgets, interactive setup or
OAuth flows, packages needing extra OS dependencies/custom images, dependency
managers other than npm, and persistent extension state are not supported in this
version. Unsupported modal RPC requests are cancelled and fail with a Settings
message instead of waiting forever. Pi retries are allowed to settle before the
wrapper exits. Model-free extension commands can complete without an agent run.
Declarative Settings schemas/adapters and workflow profile integration are deferred.

## Deployment and tests

Rebuild/recreate backend, frontend-web and docker-broker together, allowing the
broker to rebuild the Pi base/default agent images. No per-agent image rebuild is
needed when installing packages. Back up backend data **and artifact volumes**.
New SQLite tables/chat columns are additive; legacy chats keep an empty selection.

```sh
docker compose up -d --build backend docker-broker frontend-web
uv run pytest tests/backend/test_pi_packages.py tests/broker/test_packages.py
node --test tests/agent/*.mjs
npm --prefix services/frontend test
npm --prefix services/frontend run test:e2e -- tests/packages.spec.js tests/agent-profiles.spec.js --workers=1
npm --prefix services/frontend run build
```

The real-Pi RPC test uses a local fixture package and a local mock model endpoint;
it needs installed Pi 0.84.4, not model credentials or network access. Browser tests
mock APIs. The broker contracts cover isolation, failures, immutability and private
configuration delivery without Docker. A separate real-Docker smoke test requires
a rebuilt default agent image and explicit permission for public package downloads:

```sh
NAUTIONETTE_DOCKER_TESTS=1 NAUTIONETTE_PACKAGE_TEST_SOURCE='npm:<trusted-package>@<version>' \
  uv run pytest tests/broker/test_packages_docker.py
```

That opt-in test installs the chosen package with scripts disabled, checks cleanup
and immutability, then verifies a read-only runtime mount without loading extensions.
