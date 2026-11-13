# Self-hosting Denkzeug

[README](../README.md#run-it) · [Documentation](README.md) · [Configuration template](../.env.example)

Denkzeug is a multi-service Docker stack. The browser, desktop and mobile clients
connect to that stack; they do not bundle a server or a model. Start with a trusted
local instance, then put HTTPS in front of it for remote access.

## Requirements

- **Docker Engine 28+**, with **Docker Compose v2** and permission to use Docker.
  Native miniapp backends need Engine 28’s isolated network support; older engines
  do not provide the full feature set. See [miniapp isolation](mini-apps.md).
- **Git**, plus outbound internet for container builds, tool/model downloads and
  the model providers or tools you choose.
- Enough host memory and disk for the services and concurrent agents. An agent’s
  default limit is 4 GiB; the dictation service has a 3 GiB limit. These are limits,
  not a benchmarked minimum host specification. See [.env.example](../.env.example).
- A model provider API key or an account with supported GitHub Copilot access.
  Configure it after startup. Provider charges/subscriptions are separate.

The runtime uses Linux containers. Native creative tools include x86_64 and aarch64
release pins, but CI exercises Linux x86_64. Node.js, Python and Rust on the host
are not required for this Docker setup.

## Configure a new instance

The examples use a POSIX shell; on Windows, use Git Bash or WSL for these commands.

```sh
git clone https://github.com/StromFLIX/nautionette.git
cd nautionette
cp .env.example .env
```

Edit `.env`. Run the following separately for **each** secret, then put the values
in the corresponding fields. Do not reuse a secret across fields or paste it into
an issue/chat.

```sh
openssl rand -hex 32
```

| Field | Set it to |
| --- | --- |
| `APP_TOKEN` | A new secret used to connect your clients. |
| `INTERNAL_TOKEN` | A different secret used by private services. |
| `POSTGRES_PASSWORD` | A different secret for Temporal’s PostgreSQL database. |
| `MAXIO_SECRET_KEY` | A different secret for private output storage. |
| `MAXIO_ACCESS_KEY` | Keep the supplied `nautionette-outputs` value, or choose your own nonempty access-key name. |

Provider environment variables may stay empty. You can add integrations in the app
instead. For a local-only instance, optionally set `APP_URL=http://localhost:8080`
so the landing page points to your local app.

Keep `.env` private; it is ignored by Git. Both access tokens should be configured
even for a new instance you initially use only from localhost. Do not change the
`STACK_NAMESPACE`, volume names or database credentials of an existing instance
as a shortcut for an upgrade: that does not migrate stored data or database roles.

## Start and verify

```sh
docker compose config --quiet
docker compose up -d --build
docker compose ps
```

With the default ports:

| Address | What it serves |
| --- | --- |
| <http://localhost:8080> | The app, through the backend’s frontend/API proxy. |
| <http://localhost:8081> | The separate promotional website. |
| <http://localhost:8080/healthz> | Basic backend health and source-version metadata. |

Both published ports bind to `127.0.0.1`. Internal services, databases and storage
are not published. The optional `debug` profile also publishes Temporal UI on
loopback; it is not needed to use Denkzeug.

Open the app and enter the exact `APP_TOKEN` from `.env`. Initial builds and tool
preparation can take time. A responsive app does not by itself mean the worker and
agent images are ready: check **Settings > System** for component/image readiness.
For startup failures, inspect the service that reports the problem:

```sh
docker compose logs --tail=100 backend docker-broker worker
docker compose logs -f docker-broker
```

Pressing Ctrl+C in `logs -f` stops following logs, not the stack. Avoid posting logs
without reviewing them for private information.

## Connect your models and tools

1. Open **Settings > Agents & models > Model integrations**.
2. Add a provider, enter its credential, and use **Test**. For Copilot, use **Sign
   in with GitHub** and an account with Copilot access; leave the form open until
   the device sign-in finishes.
3. Choose a model. Create a saved agent if you want reusable instructions, tools
   and defaults, then start a chat.
4. Add optional tools under **Settings > MCP servers**. Connect repositories under
   **Settings > Projects** only when you want agents to work on those repositories.

Models and tools route through agentgateway. Credentials entered in the integration
form persist in its private data volume, not in this checkout. Referencing an
existing `$VARIABLE` from `.env` is also supported; recreate agentgateway after
changing that environment value.

[Agents and defaults](agent-configuration.md) · [MCP setup](mcp-servers.md) ·
[GitHub project setup](reference.md#github-projects)

## Remote access with HTTPS

Use a reverse proxy with TLS and a stable public origin:

- Route the **app** domain to `http://127.0.0.1:8080` (or your `BACKEND_PORT`).
- Optionally route a separate **website** domain to `http://127.0.0.1:8081`.
- Set `APP_URL=https://your-app.example` in `.env`, then recreate the affected
  services with `docker compose up -d --build`.
- Support streaming responses and WebSockets through the app proxy. HTTPS is also
  required for browser microphone access outside localhost.
- Do not publish broker, gateway, storage, database or Temporal ports as an
  alternative entrypoint. Keep authentication enabled.

For GitHub App registration/webhooks, GitHub must reach the same stable HTTPS app
origin. You can set `GITHUB_APP_PUBLIC_URL` or enter it during project setup. Native
clients connect with this app URL and `APP_TOKEN`, not the promotional website URL.
Do not use the project’s own production/staging domains for your installation.

Coolify and isolated staging procedures are documented in [staging](staging.md)
and [deployment pipelines](github-pipelines.md); they are not prerequisites for a
plain Docker Compose installation.

## Security and data

This is trusted, self-hosted automation—not a hardened multi-tenant service.
Agents can execute commands, use network access and call the tools you allow.
Docker access is a host-level trust boundary; only the broker mounts the socket,
but containers are not a guarantee of safe execution for mutually untrusted users.
Start with limited project/tool access and review external side effects carefully.

Output files use private MaxIO storage. Its upstream currently warns it is not
production-ready. Keep it private, pin versions, monitor disk space and maintain
backups. Do not change database/storage configuration or delete volumes merely to
clear an error.

Back up these datasets consistently, including their permissions and private keys:

| Dataset | Contents |
| --- | --- |
| `backend-data` **and** `maxio-data` together | Chats/settings/output manifests plus original files, editable sources, previews and MaxIO metadata/master keys. Database-only backups do not contain output files. |
| `agentgateway-data` | Model/MCP configuration and stored credentials. |
| `postgres-data` | Temporal history, namespaces and schedules. |
| `workflows` | Deployed workflow files, not only the checked-in seeds. |
| `projects` and `chat-sessions` | Git caches/worktrees, unpushed work, native agent sessions and original attachments. |
| `miniapps` and `artifacts` | Miniapp runtime state and workflow artifacts. |

The actual volume names depend on the Compose project/deployment platform. Do not
copy live SQLite/PostgreSQL files as if they were a consistent snapshot. Let active
work finish or stop it deliberately, coordinate a stopped/consistent snapshot, and
test restores. Protect backups as secrets. See [output storage](chat-outputs.md),
[native sessions](chat-sessions.md) and [snapshot precautions](staging.md#2-take-and-restore-a-consistent-snapshot).

## Update or stop

Back up before an update and inspect release notes. Temporal's migration off the
legacy auto-setup image requires a separate maintenance window: follow the
[consecutive-minor upgrade and restore runbook](temporal-upgrade.md), not a tag
substitution. The maintained overlay is opt-in until each stack is migrated.

In a normal branch checkout:

```sh
git pull --ff-only
docker compose up -d --build
docker compose ps
```

Use the intended release/ref if you pin deployments rather than follow a branch.
The backend drains active chat turns during a graceful restart; a whole-stack
update can still interrupt work and external side effects are not exactly-once.
Check **Settings > System** and verify a chat/workflow after an update.

`docker compose stop` stops the services without deleting their volumes. Never use
`docker compose down -v` unless you deliberately want to destroy the stack’s data.
For detailed limits, recovery and health checks, see the [technical reference](reference.md#execution-health-and-recovery).
