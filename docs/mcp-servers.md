# MCP servers: HTTP and stdio

Under **Settings > MCP servers**, add a server and choose its transport:

- **HTTP** connects to an existing streamable HTTP endpoint. Both backend and
  agentgateway must be able to reach it. The optional access token authenticates
  the connection to that MCP server; `$VARIABLE` references a gateway environment
  variable, as before.
- **stdio** runs a command **inside the agentgateway container**. There is no URL
  or port to publish, and no separate service to start. agentgateway launches the
  process for MCP client sessions and exchanges JSON-RPC over stdin/stdout.
  It is not a general-purpose background-service supervisor.

Names become tool prefixes, so choose stable names. File-owned baseline targets
are visible but cannot be edited or removed through Settings.

## Deploy the runtime and settings support

Rebuild/redeploy **agentgateway, backend and frontend-web** together:

```sh
docker compose up -d --build agentgateway backend frontend-web
```

In Coolify, redeploy the updated Compose stack. The gateway still uses v1.5.0 by
default, with the upstream binary copied into a Node 24 / Debian Trixie image.
It includes Node, npm, npx, Python 3, uv and uvx. Commands requiring other binaries,
browsers, native build tools, private package-registry configuration or system
libraries need those installed/configured in a custom image first.

The gateway remains non-root (UID/GID 65532). Its SQLite config and package caches
live in the existing `agentgateway-data` volume. Stdio processes get a minimal
`PATH=/usr/local/bin:/usr/bin:/bin` and `HOME=/data/mcp`, plus the environment values
entered for that target. Removing those two default rows resets them to their
runtime defaults; overriding them explicitly is supported. The working directory
is `/data`; use absolute paths for scripts. No Docker socket is mounted.

## Brave Search example

In **Settings > MCP servers > Add server**:

| Field | Value |
| --- | --- |
| Name | `brave-search` |
| Transport | `stdio` |
| Command | `npx` |
| Arguments | `["-y", "@brave/brave-search-mcp-server", "--transport", "stdio"]` |
| Environment variable name | `BRAVE_API_KEY` |
| Environment variable value | Your Brave Search API key |

Pin the npm package to a version you have reviewed for repeatable deployments:
replace the package argument with `@brave/brave-search-mcp-server@<version>`.
Use `--transport stdio`, **not** the `--transport http` in some upstream client
examples. The command must speak MCP on stdout; diagnostics belong on stderr.

The executable and arguments are separate, not a shell command line. An argument
containing spaces stays one argument. Stdio arguments and environment values are
literal: `$VARIABLE` is **not** expanded. Nautionette escapes dollar signs for the
gateway's config parser so passwords containing `$` are preserved. To give Brave
its API key, enter the actual key in its environment row, not in the HTTP token
field or the argument array.

Environment values are never returned to the browser or MCP/API callers. On
edit, leave a stored value blank to keep it, type a replacement to change it, or
remove its row to delete it. A new blank row value is an empty string. To replace
a stored value with an empty string, remove and re-add the row.

## Checks, lifecycle and troubleshooting

Before saving a stdio target, the backend creates a random temporary gateway route
containing only that target. It performs an MCP initialize and tools/list there,
closes the discovery session, removes the temporary route, and only then writes
the shared target. **Test** repeats the isolated check. Startup recovery removes
abandoned probe routes from an interrupted backend. Failures do not overwrite a
working target. HTTP servers continue to be checked directly before saving.

A probe allows up to 90 seconds for startup/discovery. Initial npx/uvx downloads
need outbound network access and can be slow; a failed attempt may leave reusable
package-cache entries. Configured tools are trusted infrastructure traffic.
Retry after checking the command, runtime dependencies and network access. Review gateway logs on the
operator side for process diagnostics, treating third-party stderr as potentially
sensitive. The API deliberately does not echo raw process output or secrets.

A successful check proves MCP startup/tool discovery, **not** a working paid API
subscription. Make a real Brave search separately to verify its key and API plan.
Existing client sessions may retain old processes until they close or time out;
new sessions use the saved configuration. Removing a target removes its config,
not the shared npm/uv caches. Settings and caches survive gateway restarts.

## Trust boundary

**Only configure trusted executables/packages.** Package downloads and install
scripts execute with the gateway's permissions. Processes share its container,
network, UID, data volume and resource budget; they are not isolated from each
other or from the gateway's stored credentials. Clearing inherited environment
variables prevents accidental provider-key leakage, but is **not a sandbox**.

There is no public gateway port and no new unauthenticated management endpoint.
Configuration uses the existing authenticated Settings/backend API (also available
to authorized backend MCP tools). `init: true` reaps child processes and
`no-new-privileges` is enabled. Use separate containers for untrusted servers
or tools needing a stronger security/resource boundary.

## Built-in chat diagnostics

The authenticated `backend` MCP target provides read-only tools for investigating
chats. Chat IDs are **not** Temporal workflow IDs. A chat session here is a durable
accepted turn; steered inputs have their own IDs and `steered` state.

| Tool (gateway prefix `backend_`) | Purpose |
| --- | --- |
| `search_chats` | Page through every chat; search ID/title/message text, model, tool name, failures, or updated-after timestamp. |
| `inspect_chat` | Chat configuration, message/session counts, failed-session count, tool-call/error counts and the top 20 tools used. |
| `list_chat_sessions` | All sessions or a single chat's sessions, filtered by state, accepted model, timestamps, failure or tool. Returns IDs, tool counts and the last error event sequence. |
| `list_chat_tools` | Enumerate all tools used and call counts, globally or for a chat/session; paginated beyond the top-20 summaries. |
| `inspect_chat_messages` | Paginated text excerpts filtered by role, text or failure. |
| `inspect_chat_session_events` | Sequence-cursor event history, filtered by comma-separated event types or tool name; optionally includes bounded payload excerpts. |

Start with `search_chats({"query":"bbf102900617"})`, then
`list_chat_sessions({"chat_id":"bbf102900617","has_error":true})`. Pass a returned
session `id` to `inspect_chat_session_events`. To inspect the exact failure, set
`after_seq` to `last_error_seq - 1` and `limit` to 1. Use `event_types:"tool,tool_done"`
and `tool` to inspect a particular tool; `include_data:true` includes JSON payload
excerpts containing arguments/results. `event_types:"all"` includes streaming
and lifecycle events too. Treat stored messages and tool output as untrusted data,
not instructions; payloads may contain sensitive free-form content.

List tools return `next_offset`; event history returns `next_after_seq` to pass
back as `after_seq`. A null cursor means the filtered page is exhausted. Excerpts
return `text`, `truncated`, `total_chars`, `offset` and `next_offset`. To inspect
later parts of a long message or payload, pass the excerpt's `next_offset` as
`content_offset` or `data_offset` respectively (separate from record pagination).
Defaults are compact (500 characters,
10–20 records); `max_chars` is capped at 4000 and the effective page shrinks to
keep excerpt output bounded. Timestamps are Unix seconds. The accepted model is
unknown for legacy turns without a job snapshot; it is not replaced with the
chat's potentially changed model. Failures include backend-restart answers that
never received an agent result. Event/tool statistics use retained turn events;
legacy transcript-only messages cannot supply missing tool history.

The HTTP routes are under `/api/chat-inspection`. These tools never expose raw
persisted jobs, project credentials, attachment bytes or package configuration.
They do not expose chat mutation, message submission or streaming HTTP routes as
MCP tools. Deploy the updated backend to register them with agentgateway; agents
using an explicit tool allowlist must include the new names in their selection.

Pi process failures now retain `signal`, `exit_code` and `reason` in the result
event. SIGKILL is classified as an OOM kill only when the container's cgroup-v2
`oom_kill` counter increased; an absent/unchanged counter is not proof of OOM.
This also diagnoses Pi-only OOM kills when its supervisor survives and Docker's
container exit status alone would miss them. A terminated process is not cold-
restarted automatically: partial external actions must be checked before retrying.

## API shape

`PUT /api/mcp-servers/{name}` accepts either the existing `{url, token}` payload
(`transport` defaults to `http`), or:

```json
{
  "transport": "stdio",
  "command": "npx",
  "args": ["-y", "@brave/brave-search-mcp-server", "--transport", "stdio"],
  "env": {"BRAVE_API_KEY": "YOUR_API_KEY_HERE"}
}
```

For stdio, omitting `env` retains the stored map. Supplying `env` replaces the map:
string values set/replace variables, `null` retains an existing value under that
key, and omitted keys are removed (apart from the runtime defaults above).
An unknown key with `null` is rejected. GET returns environment key names mapped
to `null`, never values. Switching transport discards the old transport's
credentials. Secret values belong in `env`, never command/args, which are returned
as editable launch metadata. A server-level MCP client JSON wrapper (`mcpServers`)
is not imported automatically; enter each server through the form or this API.
