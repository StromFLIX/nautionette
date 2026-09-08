# Frontend design system

## Direction

Nautionette is a workspace first. Keep the conversation prominent and put
configuration one deliberate step away. Preserve capabilities; reduce their
competition for attention.

- **Octagonal identity:** `BrandMark.vue`, the favicon, selected identity surfaces
  and Send use the same eight-sided geometry. Decorative shapes do not clip hit
  targets or keyboard focus outlines.
- **Progressive disclosure:** agents, reasoning, tools and projects live behind
  the composer's configuration control. Session facts, tool catalogs and detailed
  diagnostics expand when needed. An expanded composer remains usable in a short,
  keyboard-sized viewport; its draft and advanced controls can scroll independently.
- **Consistent hierarchy:** shared surfaces, borders, typography and spacing replace
  isolated dark panels. Menus, dialogs, notifications, code and workflow graphs
  follow the selected theme too.
- **Small, meaningful labels:** give controls accessible names, visible keyboard
  focus and contextual help. Do not replace important errors or permissions with
  decorative status indicators.

## Settings contributions

`services/frontend/src/settings-registry.js` defines navigation, grouping, scope,
lazy-loaded panes and the search index. Add a section there rather than editing
`SettingsPane.vue`:

```js
// Example contribution; supply a real pane and matching setting IDs.
{
  key: 'example',
  label: 'Example integration',
  icon: 'extension',
  group: 'Connections',
  scope: 'Instance',
  load: () => import('./components/settings/ExamplePane.vue'),
  entries: [
    entry('example-endpoint', 'Endpoint', 'Connection address.', 'URL server')
  ]
}
```

Search entries point to DOM IDs inside their pane. The settings shell opens
ancestor `<details>` elements, scrolls to the target and focuses it. A pane with
filtered or conditionally rendered fields can expose `revealSetting(id)` to reveal
an absent target; see `AppearancePane.vue`. Searching does not unmount the current
pane or discard its unsaved form. Navigating to a different category mounts that
category normally.

Scope is explicit: **Instance**, **This device**, or **Per workflow**. Individual
entries can override their section's scope. Automation links to the actual
workflow's Run controls rather than duplicating scheduling or delivery forms.

Keyboard entry points are **Ctrl/Command + ,** for Settings and **/** to focus its
search field. On narrow screens, a grouped category selector replaces the tree.

## Device preferences

`preferences-schema.js` describes workspace controls, their defaults and allowed
values. The Workspace form and its search contributions are generated from this
schema. Consume the corresponding value from `preferences.js` in the relevant
component to implement its behavior.

Preferences are stored under `nautionette.preferences.v1` in local storage and
synchronized between tabs on the same origin. Legacy sidebar width, grouping and
activity-window preferences are carried forward. Invalid data falls back to
validated defaults. Storage failures keep the current session usable and display
an explicit warning instead of claiming the change was saved.

Device preferences never update instance settings. Resetting the workspace does
not reset the selected theme. There is no cross-device account synchronization.

## Global defaults and agent configurations

Instance configuration follows **global defaults → agent overrides → chat changes**.
`agent-config.js` owns the browser-side resolution, copying, comparison and labels;
`AgentConfigFields.vue` shares controls between General settings and the agent editor.
Adding a configurable field requires matching backend validation/defaults and extending
`CONFIG_FIELDS`, the shared control, settings search and both API/browser tests.

Never infer inheritance from a falsy value: an omitted agent key inherits, while
`tools: null` explicitly allows all MCP tools, `tools: []` allows none, and
`reasoning_effort: null` requests provider default. An explicit tool list stays
pinned, including unavailable names and lists matching the whole current catalog.
Changing models resets effort; merely loading defaults must not reset it.

`AgentProfiles.vue` manages presets, not container images. Existing agent sets remain
under **Container environments**. `AgentPicker.vue` applies a whole preset explicitly;
individual composer controls customize the chat, not the saved agent. Existing chats
read their server snapshots and never automatically adopt profile edits. Send waits
for pending chat configuration PATCHes before accepting a message.

Welcome drafts follow asynchronously loaded defaults until the user overrides a
field or selects an agent. They preserve typed text and deliberate selections during
catalog refresh. The first send waits for successful discovery rather than guessing
defaults. Successful settings/profile writes update the local catalog immediately,
even if subsequent discovery fails; older in-flight responses cannot undo the save.
Sidebar **New chat** posts an empty body so the backend resolves current defaults.
See [Agent configuration](agent-configuration.md) for the API and deployment contract.

## Themes

`themes.js` is the browser-independent schema for four presets:

| Theme | Mode | Palette |
| --- | --- | --- |
| Orbit | Dark | Graphite and mint |
| Nebula | Dark | Midnight and iris |
| Daylight | Light | Porcelain and cobalt |
| Sand | Light | Limestone and copper |

`THEME_TOKENS` drives the advanced editor, settings search, validation and theme
transfer. It includes surfaces, text, semantic colors, syntax highlighting,
workflow nodes, fonts, corner radii, widths and spacing. Related colors derive from
the selected surfaces and accent unless explicitly overridden. Customizations are
kept separately for each preset.

To add a token:

1. Declare its key, label, group, type and bounds/default in `THEME_TOKENS`.
2. Supply a value for each preset or derive it in `resolveTheme`.
3. Add its Orbit first-paint fallback to `styles/tokens.css`.
4. Consume its CSS variable in the component; avoid literal theme colors.
5. Extend the theme tests for any new derivation or contrast pairing.

`startPreferences()` applies variables and Quasar's light/dark and semantic colors
before mounting the app. Use the shared surface overrides in `styles/base.css` for
portaled menus and dialogs. Keep syntax styling in `styles/code.css`.

Theme exports contain only a version, preset ID and token overrides. Imports are
validated as a whole and limited to 64 KB: six/eight-digit hex colors, bounded
numbers and local font-family names. They cannot inject CSS declarations or load
remote fonts/assets. Low text or button contrast produces a warning without
preventing intentional customization. Theme reset has an Undo action.

System reduced-motion preferences are always respected. The Workspace setting can
also reduce motion independently of the system setting.

## Verification

From `services/frontend`, after installing the frontend and workspace Python
dependencies and Playwright's Chromium browser:

```sh
npm test
npm run test:e2e -- --workers=1
npm run build
```

Run the browser suite and build sequentially in memory-constrained containers.
Browser coverage uses mocked APIs, including both mouse/keyboard and emulated
touch layouts. It checks all presets, persistence, invalid imports, scoped resets,
search/deep links, collapsed controls, narrow screens and keyboard-sized viewports.
`agent-profiles.spec.js` also covers inheritance, default-agent selection, explicit
empty/pinned tools, atomic switches, delayed/failed discovery and failed saves.
Existing chat, attachment, project, scheduling and graph regressions remain in the
suite. No live workflows are started by these tests.
