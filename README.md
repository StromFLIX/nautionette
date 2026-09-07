# Nautionette

[Site](http://nautionette.94.130.151.7.sslip.io) · [App](http://app.nautionette.94.130.151.7.sslip.io)

## What it is

A chat app where chats and workflows are the same thing. You talk to the system. When a conversation is something you want again, you say so — *"run this every day at 8"* — and the chat becomes a durable Temporal workflow on a schedule.

Workflows run agent steps through **Pi**, reach tools and models through **agentgateway**, and stream progress back to the frontend, so you always see the system working for you. The system can extend itself: promoting a chat writes real workflow code and puts it live without redeploying the stack.

Three things decide every trade-off below.

- **Extensible.** A new agent set, a new tool, a new validation step is a directory or a list entry — not a refactor.
- **Open.** Workflows are Python files you can read, edit and commit. Models and storage sit behind standard interfaces, so you can swap what is behind them.
- **Friendly.** The agent validates changes, verifies real runs, and reports diffs and outcomes without routine approval prompts.

## Architecture

```mermaid
flowchart TB
    clients["Frontend (App + Web)"]
    ext["External systems<br/>triggers in, webhooks out"]
    be["Backend<br/>only entrypoint<br/>memory, search, metadata<br/>owns every agent call"]

    subgraph net["Internal network - nothing published"]
        broker["Docker broker<br/>owns the docker socket"]
        gw["agentgateway<br/>MCP tools + models"]
        wfmcp["workflow-mcp<br/>authoring tools"]
        orch(["Temporal orchestrator"])
        pg[("PostgreSQL")]
        wk["Temporal workers 1..n<br/>at least one always running"]
        pi["Pi run<br/>one container per call<br/>exits when the call ends"]
    end

    vol[("Shared workflow volume")]

    clients <--> be
    ext <--> be
    be --> gw
    be <-->|"start runs, stats"| orch
    be -->|"run_agent, restart_worker"| broker
    broker -.->|"docker run --rm"| pi
    broker -.->|"docker socket"| wk
    be <-->|"history in, JSON out"| pi
    wk <-->|"agent call: JSON object"| be
    wk --> orch
    orch --> pg
    wk <-->|"tools and models"| gw
    pi --> gw
    gw --> wfmcp
    gw -->|"Backend MCP tools"| be
    wfmcp --> vol
    wfmcp -->|"restart request"| be
    be --- vol
    wk --- vol
    pi --- vol
```

Five rules the diagram encodes:

- The backend is the only service that publishes a port. Everything else stays on an internal network.
- Only the broker has the Docker socket. It offers fixed commands (`run_agent`, `restart_worker`), never a general "run this container".
- Every agent call goes through the backend, whether a user or a workflow asked for it. The broker answers to the backend and to nobody else.
- One shared volume holds the workflow files. Backend, workers and Pi runs all mount it.
- Triggers come in and results go out through the backend only.

## Components

| Component | What it does |
| --- | --- |
| **Frontend** | One Quasar (Vue 3) codebase built with Vite. `npm run build` produces the web bundle served here; Capacitor wraps the same `dist/` as the app. The list is split into Chats and Workflows; every run appears as its own thread. |
| **Website** | The promotional site. Static, its own container and its own domain, so the app and the pitch never share a deploy. |
| **Backend** | The only entrypoint: auth, triggers, webhooks, streaming to clients. Also memory, search and metadata in a SQLite store. Calls the broker, but does not hold the Docker socket. |
| **Docker broker** | Holds `/var/run/docker.sock`. Runs one Pi container per agent call (`docker run --rm`), builds missing agent images, and reconciles worker containers using fixed configuration and an image allowlist. |
| **agentgateway** | Upstream image, run as-is. One data plane for tools and models: federates MCP servers on `/mcp`, fronts every configured model provider on `/v1`, adds per-tool authorization and an audit trail. Its checked-in config is the baseline; the model integrations and MCP servers added in the app persist as runtime resources in its own SQLite volume. Config in [services/agentgateway/config/config.yaml](services/agentgateway/config/config.yaml). |
| **workflow-mcp** | Our own MCP server, registered behind the gateway. Provides validated workflow authoring, immediate deployment, run controls and history, plus the REST side the backend uses to publish files. |
| **Pi runs** | The agent runtime ([Pi](https://pi.dev), `@earendil-works/pi-coding-agent`). Pi is a CLI, so a call is a container run: start, work, exit. The base image is Node plus the Pi CLI plus `agent-run`, the wrapper that turns a job into NDJSON. An agent set extends it with Pi extensions and packages. |
| **Temporal** | Orchestrator and workers. Durable runs, retries, schedules and history, stored in PostgreSQL. A workflow step can call MCP tools, invoke Pi, or run plain Python. |
| **Shared volume** | Where workflow files live. What an agent writes is what a worker loads. Run artifacts land on their own volume, until there is an object store. |

## Chats become workflows

A chat is the draft: interactive, streaming, temporary. A workflow is the saved version: durable, scheduled, repeatable. Turning one into the other is a normal user action.

Promotion does five things:

1. Read the transcript and find the repeatable steps.
2. Turn what was fixed in the chat (a date, a repo, a customer) into workflow inputs.
3. Convert interactive agent turns into activity steps with declared output schemas.
4. Write the workflow file and deploy it.
5. Attach a trigger: a Temporal schedule, a webhook, or another workflow.

There is no mandatory draft or approval step. The agent deploys validated code, runs it with relevant inputs, inspects results, and repairs failures without routine permission questions. Scheduling still follows the user's requested task.

The deploy itself:

1. Pi calls an authoring tool in `workflow-mcp` through the gateway — not a shell. The operations are validated and deterministic, so the same request always produces the same file.
2. The backend asks workflow-mcp to validate and atomically replace the live file on the shared volume. Invalid code leaves the previous version untouched.
3. The backend publishes the change event and asks the broker to reload workers. The deployment response includes its diff, validation report, and worker restart status.
4. The agent starts a run, inspects its history and results, and fixes/redeploys errors. A started run is not reported as successful until the result confirms it.

### Backend MCP

The backend exposes streamable HTTP MCP at `/mcp/`, authenticated with a bearer
`INTERNAL_TOKEN` or `APP_TOKEN`. With both tokens empty it follows the app's open
development mode; configure tokens for a shared deployment. At startup it probes
its own endpoint and registers an authenticated `backend` target in agentgateway,
so tools arrive as `backend_<operation>` without manual setup. `BACKEND_MCP_URL`
defaults to `http://backend:8080/mcp/` and must be reachable from agentgateway.

Tools are generated from the existing backend API schemas and dispatch through the
same routes and validation as the app. They cover system health, recent events,
workflow validation/deployment/settings/schedules, runs and execution history,
worker restarts, model integrations, and MCP server configuration. For example:
`backend_system_status`, `backend_deploy_workflow`, `backend_run_workflow`,
`backend_read_run`, and `backend_restart_workers`. JSON request bodies are passed
as the tool's `body` argument. Mutations emit audit events without their credentials
or request payloads.

The workflow tools also support this loop directly: `workflows_write_workflow`
deploys and reloads workers, `workflows_run_workflow` starts a run, and
`workflows_read_run` reports its results and failures. Check `ready` and
`worker_restart` after deployment; reload failures are returned, not hidden.
Backend source-code rewriting, arbitrary shell/proxy access, and recursive chat
invocations are not exposed. Existing drafts remain available for compatibility,
but new agent writes and chat promotions deploy directly.

## Workflows are Python files

A workflow is a plain Python module against the Temporal Python SDK, with a manifest at the top. No bespoke DSL and no database row: what an agent writes is what a person reads, edits and reviews.

```python
MANIFEST = {
    "schema": 1,
    "name": "daily-repo-digest",
    "inputs": {"type": "object", "properties": {"repo": {"type": "string"}}},
    "outputs": {"type": "object", "properties": {"summary": {"type": "string"}}},
    "agent_set": "default",
}
```

Where the files live:

| Stage | Where |
| --- | --- |
| Today | The shared volume. `workflows/` in this repo is the seed, copied in on first start. |
| Next | The volume as a git checkout: author anywhere, push, the backend pulls and restarts the worker. |

Git is the reason a workflow is a file at all. Nothing in the design assumes an agent wrote it — a workflow typed by hand in an editor and pushed from a laptop takes exactly the same path through validation and deploy.

### Interactive flow views

Workflows open on **Flow**. The definition diagram shows activities, child workflows,
conditions, loops, timers, parallel groups, and returns without executing the source.
Select a node to inspect its source; pan, zoom, search, or change the layout direction.
Run controls, code, and history remain in their own tabs.

Select a run from the flow source menu, or open **Runs**, for observed Temporal history.
Active runs refresh every three seconds while the page is visible. Nodes carry real
execution states, retry attempts, durations, inputs, results, and failures. Refreshes
preserve the camera and selection. The active step opens in view on short screens;
node details become a bottom sheet on mobile.

Legacy drafts open with a visual comparison: green additions, amber changes, and dashed red
removals, including changed connections. **Proposed**, **Deployed**, and **Code diff**
remain available for inspection and deployment. Changes outside diagrammed steps are reflected on
the workflow node and in the code diff.

Definition diagrams are structural previews, not a Python execution engine. Helper
calls, dynamic expressions, and complex exception handling stay as source blocks;
warnings flag partial diagrams. Run diagrams cover ordinary activities, child workflows,
timers, and received signals in a single execution, with edges based on observed command
and completion order, not inferred data dependencies. Local activity markers and child
execution internals are not expanded; child nodes link to their own run. History is
capped at 2,000 relevant events with an explicit warning, and payloads use the history
reader's existing truncation limits. A lost connection leaves the last graph visible
and marks updates as paused.

Frontend verification (from the repository root):

```sh
npm --prefix services/frontend ci
npm --prefix services/frontend test
services/frontend/node_modules/.bin/playwright install chromium
npm --prefix services/frontend run test:e2e
npm --prefix services/frontend run build
```

The browser tests use mocked API responses and the real Python graph builders via
`uv`, so the workspace's Python dependencies must also be installed. They do not
start workflows or require a running Temporal server.

## The authoring schema

An agent that writes code needs a narrow door. `workflow-mcp` is that door: every tool takes JSON-Schema-validated arguments, and every write runs the same checks, no matter whether Pi, the frontend or a future git sync asked for it.

| Tool | Does |
| --- | --- |
| `list_workflows` | Names, manifests, versions. |
| `read_workflow` | The current file. |
| `validate_workflow` | Runs the checks below and writes nothing. |
| `write_workflow` | Validates, deploys, reloads workers, and returns a diff and status. |
| `run_workflow` | Starts a deployed workflow with its input; returns a workflow ID. |
| `list_runs` / `read_run` | Finds runs and inspects progress, errors and final results. |
| `delete_workflow` | Removes a file. Run history stays in Temporal. |

The checks, in order:

1. Tool arguments against the tool schema.
2. Manifest against the workflow schema — name, `schema` version, input and output schemas, agent set.
3. The file parses, imports in a throwaway subprocess, and registers with the Temporal SDK.
4. Validated source is deployed atomically. The agent receives the diff and reload status, then tests the workflow and inspects results without an approval gate.

Extensible on purpose: the manifest schema is versioned and additive, unknown keys prefixed `x_` are preserved instead of rejected, and a new rule is a new step in that list. It is deliberately not a policy engine — good enough to stop broken code, cheap enough to change.

## Two ways Pi is called

| | Interactive | Activity |
| --- | --- | --- |
| Caller | Backend, for a user in a chat | A Temporal activity, through the backend |
| Returns | A JSON stream | One complete JSON object |
| Shape | Free-form, rendered live | Structured output, checked against the schema the activity declares |

In activity mode an agent step is a typed function. A stream would be wasted there: nobody is watching, and the next step cannot read half a stream. If the model returns text instead of the declared object, the activity fails and Temporal retries it. Progress events still go to the backend in both modes.

Both modes get a fresh container, so context is always passed in, never remembered:

- In a chat the backend hands over the conversation history it already owns.
- In a workflow the workflow decides what to hand over — the full history of a run, a summary, or nothing but the typed inputs. That choice is workflow code, visible in the file and replayable by Temporal.

The container contract is one environment variable in and NDJSON out. `AGENT_JOB` carries a
base64 JSON job; `agent-run` renders the prompt, runs `pi --mode json`, and translates Pi's event
stream into `delta`, `tool`, `error` and a final `result` line. Nothing else crosses the boundary.

## Scaling rules

| | Policy |
| --- | --- |
| Temporal workers | At least one always running. Never scale to zero, or triggers pile up with nothing to pick them up. |
| Pi | Always zero between calls. One `docker run --rm` per call, gone when the call returns — including for pinned or busy workflows. |
| State | Nothing survives in a Pi container. History, inputs and the workspace are handed in at start; results come back as JSON. |
| Images | Built in advance and checked periodically. If cleanup removes one, it is rebuilt; a call arriving during recovery waits for the build. |

### Execution health and recovery

The broker checks Docker at startup and every `CONTAINER_RECONCILE_SECONDS` (default: 30).
It rebuilds missing Pi base and agent-set images, restores the base-image alias, starts stopped
workers, restarts unhealthy workers with the configured drain timeout, and recreates deleted
workers up to `WORKER_REPLICAS` (minimum: one). Failures are reported and retried on later passes.
All worker operations are scoped to the broker's own Compose project.

Every worker loads all workflow files on the configured Temporal task queue; separate containers
per workflow are unnecessary. A Docker readiness probe checks a fresh worker heartbeat, successful
workflow loading, and whether the loaded source files still match the shared volume. Changed files
therefore trigger recovery even if the explicit deploy-time restart was missed. Invalid workflows
remain degraded until their code or dependencies are fixed; recovery cannot repair workflow code.

`/api/system` exposes the broker's live image status, missing images, desired/ready worker counts,
container states, and readiness errors. An HTTP-successful but degraded broker is not shown as healthy.
Pi containers themselves remain intentionally ephemeral: there is no idle Pi container to keep alive.

Recovery is eventual, not uninterrupted availability: allow for the reconciliation interval, image
builds, dependency installation, and worker startup. Worker probes run every 10 seconds, require
three failed checks, and allow 360 seconds for initial dependency installation. Docker and the broker
must remain running; the worker image must be locally available or pullable. Recreated workers use
the fixed image, environment, network, and volume configuration in the broker, not arbitrary container
arguments. Custom deployments must keep those settings aligned with their worker configuration.

Deploy these checks with `docker compose up -d --build worker docker-broker backend`. Compose starts
workers before the broker and stops the broker first on stack shutdown. Stop the broker before
intentionally stopping or removing individual workers for maintenance, or it will restore them.

## Layout

```
docker-compose.yaml            every component; only the backend and the site publish a port
pyproject.toml / uv.lock      one uv workspace for every Python service
.env.example                  copy to .env; no secrets are committed
libs/nautionette/             manifest schema and source helpers, shared by the services
services/
  backend/                    entrypoint, management, calls the broker
  docker-broker/              owns docker.sock, fixed verbs only
  agentgateway/config/        config for the upstream image
  workflow-mcp/               MCP + REST server for workflow authoring
  worker/                     Temporal workers
  frontend/                   one Quasar codebase, web and app targets
  website/                    the promotional site
images/
  pi-base/                    Node + the Pi CLI + the agent-run contract
  agent-sets/default/         the one agent set: gateway provider + MCP tool bridge
infra/temporal/               Temporal server config
tests/                        one pytest suite over every Python service
workflows/                    Python workflow files, seeded into the volume
```

Every Python service ships one distinctly named package — `nautionette_backend`,
`nautionette_worker`, `nautionette_workflow_mcp`, `nautionette_docker_broker` — so all
four can be installed into the one workspace venv without shadowing each other.

The backend is the largest of them, and is laid out by what each file is about:

```
services/backend/nautionette_backend/
  main.py            assembles the app and nothing else
  config.py          the environment, read once
  security.py        the bearer token and the internal token
  db.py              SQLite storage, and the one connection
  events.py          the in-process fan-out behind /api/events
  runtime.py         saved settings, the history budget, the catalog cache
  fields.py          declared form fields, and checking a payload against them
  gateway_config.py  writing to agentgateway's own config store
  catalog.py         which models and tools exist, and who serves each one
  mcp_servers.py     MCP targets: probe, write, remove
  runs.py            starting a run, following it, delivering its result
  clients/           one module per service this one talks to
  agent/             prompts, one agent call, and chat promotion
  integrations/      the provider registry, and what it becomes in the gateway
  routers/           the HTTP surface, one router per thing
```

Networks: `edge` (published), `internal` (services, outbound allowed so the gateway reaches the model provider), `control` (backend to broker only), `data` (Temporal to Postgres only).

## Run it

```
cp .env.example .env                    # provider keys, POSTGRES_PASSWORD, APP_TOKEN
docker compose up -d
```

The backend answers on `${BACKEND_PORT}` and the site on `${WEBSITE_PORT}`, both bound to
loopback. The broker builds `pi-base` and every agent set on first start; until that finishes,
`/api/system` reports the agent sets as not ready and says so in the UI.

Model providers are managed under **Settings > Agents > Model integrations**: pick one from the
list, add it, test it, and remove it again. OpenRouter, GitHub Copilot, OpenAI, Anthropic, Groq,
Mistral, DeepSeek and xAI ship as entries in one registry, and **Custom** covers any other endpoint
that speaks the OpenAI chat completions API. Every integration becomes an agentgateway route, so
they all behave the same way; OpenRouter is added automatically on first start for continuity.

Nautionette never stores a model list. It asks each provider what it serves — OpenRouter and custom
endpoints through their own catalog, Copilot through the authenticated account — and labels every
entry in the pickers by both integration and model vendor. A prefixed integration wins over the
OpenRouter wildcard for its own namespace, so `openai/*` goes direct once OpenAI is configured.

Credentials never touch this codebase. Paste a provider's API key into the integration form and
agentgateway keeps it in the `agentgateway-data` volume; the backend writes it once and never reads
it back to a client, so a phone is enough to add a provider. To hold keys in the environment
instead, put them in `.env`, recreate the gateway with
`docker compose up -d --force-recreate agentgateway`, and type `$OPENAI_API_KEY` (or whichever
variable) into the same field. Either way the secret stops at agentgateway.

Working on the Python services:

```
uv sync                                 # one workspace, one lock file
uv run pytest                           # every service, no Docker and no network
uv run ruff check . && uv run ruff format .
uv run --package nautionette-backend uvicorn nautionette_backend.main:app --reload
```

The suite in `tests/` covers all four services. Everything outside a process is faked:
agentgateway keeps its config resources in a dict, the broker replays a canned agent
stream, Temporal is a dictionary of executions, and outgoing HTTP is answered from a
routing table. So `uv run pytest` needs nothing running and finishes in seconds.

```
tests/backend/        the API surface, plus the modules behind it
tests/broker/         image tagging, the fixed verbs, the restart scoping
tests/worker/         the activities a workflow may name, and loading workflow files
tests/workflow_mcp/   the store, the check chain, the REST face
tests/lib/            the manifest schema and the source reader
```

The one test that needs a subprocess is the workflow import check; it is marked `slow`
and runs by default, since the committed workflows are validated with the real chain.

## Open points

- **Pi runs with full permissions.** The container is the only boundary. One container per call shortens the window but not the blast radius; Pi's docs offer stronger options (Gondolin micro-VM, OpenShell) if that is not enough.
- **Worker restarts** must let running activities finish instead of killing them. A stop grace period is a start, not a proof.
- **All authentication sits in the backend.** Internal services trust the network.
- **Git sync** needs a conflict story: what happens when a push and an agent write touch the same file.
- **Composable workflows.** Inputs and outputs are covered by the manifest; one workflow calling another still needs versioning and permissions.
- **Object storage.** The boundary is S3-shaped, but there is no store behind it yet — MaxIO is not ready, so artifacts stay on the shared volume.
