# Denkzeug display identity

`brand.json`, `logo.svg` and `fonts/` are the canonical display identity. The logo
is the approved nested-profile version, with curved bases and a softer inner
contour. The SVG viewBox crops only presentation padding; its artwork is unchanged.

Run `python scripts/sync_brand.py` after edits, and commit its generated copies.
`--check` fails on drift without writing. Each Docker context remains independent;
services do not reach outside their build contexts or fetch branding at runtime.
The website's editable HTML is `services/website/index.template.html`; its generated
`index.html` remains usable without JavaScript.

Frontend components import `{ brand }` from `src/brand.js` (`name`, `tagline`,
`description`, `logo`, `colors`, `defaultTheme`). Python services use
`from nautionette.brand import NAME`. Electron uses `desktop/brand.json`.
Use existing semantic theme tokens, not hardcoded light colors in components.
The default Denkzeug palette uses paper/white/ink with blue agents, lilac sessions
and amber workflows. Keep dark themes and user-imported designs functional.

## Redesign contract

- Main navigation stays Chats / Workflows / Runs / Apps; URLs are unchanged.
- Agents are reusable configurations; chats are conversations with those agents;
  workflows are repeatable processes; runs are their history; miniapps are interfaces.
- Settings should distinguish this device's experience, tools/connections and
  instance operations, with scope explained beside the controls.
- Sora for the interface, Source Serif only for reflective/definition copy.
- Title-case labels, clear primary actions, quiet borders, bounded content widths,
  rounded squares rather than the old octagon motif. No decorative dashboard motion.
- Keep forms, keyboard navigation, authentication, drafts, saved themes/skins,
  accessibility preferences and small-screen safe areas intact.

## Deliberately unchanged until the next migration

Domains and instance addresses; environment variable names; repository URLs;
Python/npm package names and imports; Docker image names; app IDs and signing
identities; OAuth/custom-URI schemes; IPC/miniapp bridge names and message types;
API paths and tool IDs; database, telemetry and local-storage namespaces;
installer artifact names and updater identity. Existing user-selected names,
Git identities and old saved content are data, not branding to rewrite.

Display names, visible copy, favicons, window/app labels and permission descriptions
should say Denkzeug. Legacy technical tokens such as `nautionette-skin` stay accepted
for compatibility, even when their UI calls them Denkzeug design packs.
