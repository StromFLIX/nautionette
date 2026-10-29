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

Membership uses the latest explicit `agent_id` selection and a stored
`agent_customized` flag. Changing an effective model, environment, reasoning,
tool/project/package selection or system prompt marks the chat custom and leaves
the agent view for All without closing the chat. Undoing overrides does not restore
membership: choose/reapply an agent explicitly to do that. Renames, read state,
operational timeout controls and no-op configuration writes do not detach it.
Profile/default edits do not reclassify existing chats. The upgrade classifies
legacy customized chats once; subsequent restarts preserve that result.

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
