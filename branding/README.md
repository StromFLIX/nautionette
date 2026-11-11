# Denkzeug display identity

`brand.json`, `logo.svg`, `fonts/` and `native/` are the canonical display identity. The logo
is the original solid single-path mark. The nested-line experiment is not used:
its thin contours were too soft at interface sizes. Keep small marks solid and crisp.

Run `python scripts/sync_brand.py` after edits, and commit its generated copies.
`--check` fails on drift without writing. Each Docker context remains independent;
services do not reach outside their build contexts or fetch branding at runtime.
Logo URLs and favicons carry a generated content hash. The app shell and small
branding resources revalidate, so a later logo change cannot leave stale marks
behind a long-lived asset cache.
The website's editable HTML is `services/website/index.template.html`; its generated
`index.html` remains usable without JavaScript.

Frontend components import `{ brand }` from `src/brand.js` (`name`, `tagline`,
`description`, `logo`, `colors`, `defaultTheme`). Its generated `src/brand-data.json`
is bundled by Vite, rather than importing a public-directory file as a module.
Python services use `from nautionette.brand import NAME`. Electron uses
`desktop/brand.json`; Pi runtime/extension manifests and the agent-instruction
Markdown are synchronized too. Edit `AGENTS.template.md` for those instructions.
Use existing semantic theme tokens, not hardcoded light colors in components.
The default Denkzeug palette uses paper/white/ink with blue agents, lilac sessions
and amber workflows. Keep dark themes and user-imported designs functional.
Existing valid preferences are never silently reset; new devices and appearance
recovery start with Denkzeug. The 125% interface-size default is retained.

## Native artwork

`native/` holds the VectorCraft-exported artwork and 1024/432/192px PNGs.
iOS uses the opaque padded square; desktop uses a separate 90%-occupancy export
with transparent outer margins/corners and an opaque white glyph on the black
tile (no white matte), so the mark stays legible in small Windows taskbar and
other OS icon slots. Android uses a separate transparent white
negative-space glyph on a full-bleed black adaptive background, with no baked-in
outer corners. Its open tail intentionally extends to the launcher's mask.
After changing the canonical logo, re-export all platform variants with the
documented proportions and update `native/source.sha256` to the canonical SVG's SHA-256.
The sync script refuses stale source provenance, then copies the exports into
Android/iOS sync resources, the Electron installer resources, and the window icon.
Do not independently redraw or manually copy marks into each platform. See
`services/frontend/native/icons/README.md` for launcher sizing.

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
