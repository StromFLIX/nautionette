# Managed Pi packages

**Settings → Extension library** manages workspace-wide installation and default
configuration. It does not require or select an agent. **Agent editors and chat
composers** select installed packages through the same searchable checkbox picker,
alongside tools and projects. No per-agent image or manual configuration-file
editing is required. Global defaults select no packages.

## Use

1. Open **Extension library → Discover packages** and search the npm `pi-package`
   catalog. Click **Install** on a result. Shared Settings search also links to
   packages. For an unlisted source, expand **Install from npm or public GitHub**.
   Accepted sources include `npm:package@version`, `npm:@scope/package@version`,
   and public `https://github.com/owner/repo@ref`. Private repositories, local
   paths, arbitrary hosts and credential-bearing URLs are not supported.
2. The **Installed** view shows installation progress, failures/retry and ready
   packages. Installing never enables a package in an agent or chat.
3. Under an installed package's **Configuration & resources**, optionally set
   resource types, environment variables and configuration files. **Save
   configuration** publishes the defaults for future selections in one step.
   Advanced Pi path filters remain available; themes do not change the web theme.
4. Select installed packages in an agent editor and **Save agent**, or open
   **Chat configuration → Extensions** below the message input. Chat selection
   saves immediately for future messages, without editing the saved agent.
   Deselecting does not uninstall a package. No agent is required for chat selection.
5. Optional **Agent-specific configuration** overrides the library defaults for
   that agent. Apply the override, then save the agent. Library changes do not
   overwrite existing agent/chat revisions. The picker offers **Use library
   configuration** to explicitly replace a pinned override with current defaults.
6. After Pi reports its commands on a turn, typing `/` offers discovered extension,
   prompt and skill commands. Historical messages retain native Pi roles.

Legacy installations receive an empty default revision the first time the library
is listed; private per-agent settings are never promoted into shared defaults.
Existing selection IDs and queued jobs remain untouched. Concurrent library
configuration saves use the previous revision ID and reject stale writes.

Downloads do not automatically change any agent. Editing an agent does not change
existing chats. Accepted/queued turns retain their package/configuration revision
IDs even if the agent or chat is subsequently edited or the agent is deleted.
Package order is significant. Updating means installing a new artifact and
selecting a new revision; there are no turn-start downloads or silent upgrades.

## Configuration and secrets

Packages do not have a universal configuration schema. Settings provides named
rows for environment variables and configuration-file paths/content, not guessed
package-specific forms. Follow the package's documentation. Previously saved
values are never shown; leave a saved row's value blank to keep it, or remove the
row to omit it. To replace a saved value with an empty string, remove and re-add
its row. The underlying API accepts:

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

The Pi image pins **0.85.1** as a full npm installation. Pi 0.84.4 lacked the
`@earendil-works/chord` and `chord/context` imports required by current
`pi-subagents` async runners. The image now verifies these host imports during
build; do not graft a second, unrelated chord copy onto the old Pi SDK.

Async subagents can run **within a turn**, provided the parent waits for their
completion (for `pi-subagents`, `bg_wait` with the returned run ID) before ending.
The runner includes this instruction whenever packages are selected. RPC reports
`hasUI=true`, so pi-subagents' headless auto-drain does not apply: its suggestion
to return control and await a later notification is incompatible with our
per-turn containers. This is not cross-turn background persistence. Use durable
Nautionette workflows for that.

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
it needs installed Pi, not model credentials or network access. Browser tests
mock APIs. The broker contracts cover isolation, failures, immutability and private
configuration delivery without Docker. A separate real-Docker smoke test requires
a rebuilt default agent image and explicit permission for public package downloads:

```sh
NAUTIONETTE_DOCKER_TESTS=1 NAUTIONETTE_PACKAGE_TEST_SOURCE='npm:<trusted-package>@<version>' \
  uv run pytest tests/broker/test_packages_docker.py
```

That opt-in test installs the chosen package with scripts disabled, checks cleanup
and immutability, then verifies a read-only runtime mount without loading extensions.

A separate opt-in regression uses real `pi-subagents` (verified with 0.66.0) and a
local mock gateway to launch an async child, await it and check its output. It does
not install packages or contact public services. Put Pi 0.85.1 on PATH and run:

```sh
NAUTIONETTE_SUBAGENTS_DIR=/path/to/installed/pi-subagents \
  node --test tests/agent/test_subagents_rpc.mjs
node images/pi-base/verify-pi-runtime.mjs /path/to/pi-coding-agent
```

The subagent regression reproduces the missing-chord error under Pi 0.84.4.
Rebuild the Pi base and derived agent images to deploy the runtime fix; rebuilding
only the frontend will not change the Pi version.
