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
package-cache entries. Configured tools are trusted infrastructure traffic, not
controlled by a chat's direct-internet permission. Retry after checking the
command, runtime dependencies and network access. Review gateway logs on the
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
