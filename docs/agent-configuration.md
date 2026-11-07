# Agent configuration

## Scope and inheritance

**Global defaults → agent overrides → chat changes**. These are instance-wide chat
presets, stored in backend SQLite. They are separate from device preferences,
container agent sets, Git attribution, and deployed workflow activity configuration.
Agents inherit only global defaults, never another agent.

| Config field | Global setting | Meaning |
| --- | --- | --- |
| `model` | `default_model` | Model ID from the catalog |
| `agent_set` | `default_agent_set` | Container environment |
| `reasoning_effort` | `default_reasoning_effort` | Advertised effort, or `null` for provider default |
| `tools` | `default_tools` | `null` for all MCP tools; `[]` for none; an explicit list for a pinned selection |
| `project_ids` | `default_project_ids` | Ready, writable projects; `[]` for none |
| `packages` | None (always `[]`) | Ordered immutable Pi package/configuration revision IDs, selected in Settings |
| `system_prompt` | None (always `""`) | Up to 32,000 characters of additional persona instructions; supplements the app's operating rules |

An omitted **agent config** key inherits. Explicit null is valid only for tools
and reasoning; it is not an inheritance marker. To restore inheritance, remove the
key. Global settings use the existing settings contract: `null` removes a saved
override and restores the factory/environment default. `default_agent_id: null`
selects global defaults for new chats.

Reasoning belongs to its model. An agent that overrides the global model without
an effort uses provider default, not the global model's effort. An explicit model
change in a chat or the global settings similarly clears effort unless the API
caller supplies a new valid selection. Explicit agent efforts remain explicit;
if a changed provider/model no longer supports them, validation reports HTTP 422
rather than silently substituting a different effort.

A pinned tool list does not expand when new tools appear. Unavailable names remain
saved and removable; only explicit **All** enables future MCP tools. This is MCP
selection, not a sandbox: built-in file/shell tools remain available, and tool
filtering does not replace gateway authorization. Project access remains a separate
control. Sandboxes always have outbound internet access.

See [Managed Pi packages](pi-packages.md) for Settings-based installation,
resource selection, write-only configuration, runtime compatibility and backup
requirements. Package changes do not need per-agent image builds.

## Agent views and portraits

The chat workspace has an agent column between navigation and the chat list.
**All** is always its first, pinned item and shows every chat, including custom,
unassigned and deleted-agent conversations. Saved agents follow it. On phones the
same compact, icon-only list becomes a horizontal strip; All remains visible while
people scroll. Octagonal portraits identify agents, with names on hover. Right-click
an agent (or press and hold on touchscreens) to edit it or change its image.

Selecting a person filters the existing date/project/activity groups, counts and
search to that person's chats. The `?agent=<id>` URL preserves the view on reload
and mobile back navigation. **New chat** and the welcome composer use that agent,
ahead of remembered settings or the configured default. A deleted selection falls
back to All after successful catalog discovery, not during an outage.

Membership uses only the latest explicit `agent_id` selection. Changing a model,
environment, reasoning, tool/project/package selection or system prompt keeps the
chat in that agent's list, including search and activity counts. Only selecting a
different agent or global defaults changes membership. The stored
`agent_customized` flag still identifies per-chat overrides and allows reapplying
current agent defaults; it does not affect grouping. Unsent welcome drafts also
keep their agent scope when customized. Profile/default edits do not rewrite
existing chat settings.

**Settings > Agents & models > Agents** accepts an optional portrait file and a
system prompt. Click the portrait preview or **Choose image** to upload; **Change
image** and **Remove image** let you replace it or return to initials before saving.
Images are authenticated API data, not third-party URLs. PNG, JPEG
and WebP files up to 5 MiB / 25 megapixels are validated and converted to a static
WebP thumbnail, at most 256×256, without EXIF metadata. Avatars fall back to initials.
Images are used in the column, settings, agent picker and conversation header.
Duplicating an agent copies its image and prompt; removing either restores its
fallback. Prompts are copied into chats and accepted turn jobs, so edits never
rewrite running or queued work.

## API

All routes use the same authenticated backend API as other instance settings.

- `GET /api/agents`: raw and resolved agents, `global_chat_defaults`,
  `default_agent_id`, and the resolved `chat_defaults` for new chats.
- `POST /api/agents`: create an agent; returns the saved record with its ID and
  resolved configuration (HTTP 201). Metadata is `name`, `description` and `image`
  (an uploaded raster image as a base64 data URL, or `""` to clear).
- `GET /api/agents/{id}`: read a saved agent.
- `PATCH /api/agents/{id}`: edit metadata or **replace** `config`. Omitting
  `config` preserves it; sending `config: {}` restores inheritance for every field.
- `DELETE /api/agents/{id}`: delete the preset, not its chats. If selected as the
  default, new chats return to global defaults.
- `PUT /api/settings`: set the `default_*` fields above and `default_agent_id`.
  The full edit validates before any setting is written; lists remain JSON arrays.

For example, create a project-specific agent while inheriting its model and environment:

```json
{
  "name": "Code review",
  "description": "Review the selected repository without MCP tools",
  "config": {
    "tools": [],
    "project_ids": ["<ready project ID from /api/projects>"],
    "reasoning_effort": null
  }
}
```

Names are required, limited to 80 characters, and case-insensitively unique under
SQLite's `NOCASE` collation. All and Global defaults are reserved names. Descriptions are optional (500 characters). Config
keys and types are validated. Project selections must be ready and available;
stale project defaults fail closed and can be cleared explicitly. Missing
explicitly selected agent IDs return HTTP 404, not a silent global fallback.

`/api/catalog` includes the same raw/resolved configurations alongside existing
model, tool and container-environment discovery. Mutations invalidate its cache
and emit `settings.changed` or `agent.profile.changed` events. Backend MCP exposes
`list_agents`, `get_agent`, `create_agent`, `update_agent`, and `delete_agent`
(under the gateway's `backend_` prefix), with the same API validation.

## Chat snapshots

`POST /api/chats` with `{}` uses the selected default agent, or global defaults.
Pass `agent_id` to select another agent, or `agent_id: null` to explicitly choose
global defaults. Additional config fields override that selection. The resulting
chat stores the fully resolved values plus the agent's ID/name for reference.

`PATCH /api/chats/{id}` with `{"agent_id": "..."}` atomically reapplies that agent's
current configuration. `{"agent_id": null}` applies current global defaults.
Additional config fields override the applied preset. A PATCH without `agent_id`
changes only the supplied fields (with the model/effort dependency above).

Editing defaults or agents does not rewrite existing chat records. Applying a
profile is explicit, and it does not change accepted or queued turn-job snapshots.
Deleting an agent retains its historical chat association/name and configuration;
the composer labels it removed and lets the user choose a replacement. Profiles
are not automatically applied to workflow agents or deployed workflow source.

## Agent-to-agent delegation

Delegation requires **both** the selectable **Agents** tool group and an agent's
explicit allowlist. The catalog groups `list_subagents`, `call_subagent`, and
`wait_for_subagent` under `server: "Agents"`, not the backend administration group.
Group selection uses the existing tool list contract: `null` includes it, `[]`
disables it, and pinned lists must name its tools. Transport authorization checks
the accepted turn's selection as well as local tool registration.

`POST/PATCH /api/agents` accepts top-level `allowed_subagents: ["<saved ID>", ...]`.
It is not a config override, has no inherited default, and starts empty. Omitting it
on PATCH preserves it. At most 100 target IDs; duplicates are removed. A target
must exist. Deletion removes references from other allowlists. Cycles/self-calls
are permitted only within the same depth/width/parallel bounds. Allowlist edits
apply at the next call, even in existing chats; they do not stop already accepted
children. Identity comes from the accepted job, never caller-supplied agent IDs.

Three instance settings (`PUT /api/settings`, `null` resets):

| Setting | Default | Range | Meaning |
| --- | --- | --- | --- |
| `subagent_max_depth` | 3 | 0–10 | Root is 0; its child is 1 |
| `subagent_max_width` | 5 | 0–100 | Total accepted/reserved child calls per parent turn, including completed calls and continuations |
| `subagent_max_parallel` | 4 | 0–100 | Unfinished subagent turns across the entire root tree, not per branch; excludes root, includes waiting parents |

Limits are strict integers; zero disables admission. Trees snapshot their initial
ceilings. Later calls use the minimum of those ceilings and current settings:
lowering applies immediately; increasing requires a new root turn. Lowering does
not kill active calls. At capacity, admission returns a clear error instead of
queueing: nested parents still occupy slots while waiting, so queueing could
otherwise deadlock. Wait for existing children before submitting more work.
Each independent root has its own parallel budget, not an instance-wide semaphore.

`call_subagent(agent_id, text, chat_id?)` starts work asynchronously, returning
`id`, `chat_id`, `turn_id`, `depth`, and `state`. Without `chat_id` it creates a chat
using the target's current resolved configuration, projects and packages. Scratch
space and Git worktrees are separate from the caller's. To continue, supply a chat
previously opened by the same initiating chat for the same target. Modified, busy,
paused, unrelated and ancestor chats are rejected, never steered or silently queued.
A continuation counts as another child call. Agent input includes a provenance
header and `meta.sender_agent` for UI attribution; answer identity is snapshotted
as well, so later renames/deletions cannot turn it into a human message.

`wait_for_subagent(call_id, timeout_seconds=300)` waits for a direct child; 0 polls.
The local wrapper long-polls in bounded 20-second HTTP requests (maximum total 300
seconds per invocation). Timeout returns current state without cancelling work.
Repeat if still running. Completion returns bounded answer text, `ok`, and `error`;
a started call is not a successful result. Child failures and explicit stops are
visible. A normal asynchronous parent finish does not kill its children. A stopped,
failed, deleted or restart-interrupted ancestor stops descendants through their
normal broker controller/cleanup path. A cancelled wait does not replay a call.

The private `/api/subagents/list`, `/call`, and `/wait` transports require an active
turn capability plus normal backend authentication. They are hidden from model
schemas; local wrappers inject the existing private turn capability. Call request
IDs are derived from tool-call IDs and durably scoped to the parent turn. Exact
retries return the original call; argument mismatches fail. SQLite retains ancestry
and results across reconnects/restarts; uncertain admissions and interrupted work
are reported, not automatically resubmitted. Raw transports are not selectable
MCP tools. This is orchestration policy, not isolation from trusted administrative
tools or shell access; limit those separately if agents should not edit settings.
The shipped tools are available to saved-agent chat turns with the default agent
set. Workflow activities do not implicitly gain a profile or chat identity.

## Deployment and verification

Rebuild/recreate **backend**, **frontend-web**, and **docker-broker** together; the
broker acquires/rebuilds the changed Pi base and default agent-set images:

```sh
docker compose up -d --build backend docker-broker frontend-web
```

SQLite automatically adds the agent-profile table and nullable chat association
columns. Existing chats, local project worktrees and commits are preserved; no
re-download or destructive migration is needed. Legacy unset-model behavior is
unchanged. Back up the backend data volume as for any schema upgrade.

The wrapper now passes `NAUTIONETTE_TOOLS_JSON`. Its JSON `null`, `[]`, and named
lists remain distinct through runtime registration. The legacy CSV variable is
still emitted/read for compatibility, but cannot represent **No tools**; update
custom agent extensions to honor the JSON contract too. Do not deploy the new
frontend/backend while retaining an old default agent image that interprets an
empty tool list as all tools. Already running containers keep their original setup.

Focused checks from the repository root (Node 24 and installed workspace dependencies):

```sh
uv run pytest tests/backend/test_agent_profiles.py tests/backend/test_agent_grouping.py tests/backend/test_backend_mcp.py
node --test tests/agent/test_tool_selection.ts tests/agent/test_images.mjs
npm --prefix services/frontend test
npm --prefix services/frontend run test:e2e -- tests/agent-profiles.spec.js tests/agent-views.spec.js --workers=1
npm --prefix services/frontend run build
```

Browser tests use mocked APIs and cover desktop, 320px layouts, all four themes,
inheritance, atomic switches, retries, unavailable selections and delayed catalogs.
Run browser tests and production builds sequentially on memory-limited machines.
