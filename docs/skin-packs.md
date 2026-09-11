# Custom skin packs

**Settings → Appearance → Skin packs** changes the app's design, not just its
palette. Install multiple packs, switch between them and the four built-in themes,
tweak the active design's tokens, and download packs to share. Preferences and the
library are saved on this device and synchronized between tabs; they are not
instance settings and do not travel with your account.

## Two complete examples

- [Winamp Classic](../services/frontend/src/skin-examples/winamp-classic.skin.json):
  brushed-metal texture, beveled buttons, square panels, phosphor-green displays
  and console typography.
- [Windows XP](../services/frontend/src/skin-examples/windows-xp.skin.json):
  Luna-inspired blue title bars, beige controls, original rolling-hills wallpaper,
  a green Start-style navigation control and a bottom taskbar on desktop.

Both are included in the library with **Use skin** and **Download** actions. Their
JSON files are self-contained examples for authors. All artwork and styling is
original: these are tributes, not licensed Winamp/Windows assets. Existing Winamp
`.wsz`/`.wal`, Windows visual styles and arbitrary ZIP archives are **not** supported.

## Import, customize and share

1. Download an example or create a UTF-8 `.skin.json` file using the format below.
2. Choose **Import design** in Appearance. Validation completes before anything
   changes. A valid pack is installed and activated immediately.
3. Use **Customize every detail** to override tokens for this pack only. **Reset
   theme** restores the pack's token defaults (with Undo); it does not uninstall
   the skin or modify its CSS.
4. **Export skin pack** / the card's **Download** includes metadata, CSS, embedded
   assets and your token tweaks. It never includes chat, account or workspace data.
5. **Remove** uninstalls a pack and its tweaks. Removing the active pack switches
   to its base theme. The two bundled examples remain available for reinstalling.

Reimporting an existing `id` replaces that pack and clears its local token tweaks;
use a different id to keep both versions. Legacy version-1 theme exports still
import/export as before. Importing a legacy theme disables the active skin but
keeps installed packs.

## Pack format (version 2)

```json
{
  "format": "nautionette-skin",
  "version": 2,
  "id": "my-studio",
  "name": "My Studio",
  "author": "Your name",
  "description": "Paper surfaces, serif typography and square controls.",
  "base": "sand",
  "tokens": {
    "accent": "#713e68",
    "font": "Georgia, serif",
    "radius-sm": 0,
    "radius-lg": 4
  },
  "css": "[data-skin-part=header] { background: linear-gradient(#fffaf0, #e6d8c0); } [data-skin-part=composer] { box-shadow: 3px 3px 0 #713e68; } @media (max-width: 900px) { [data-skin-part=composer] { box-shadow: none; } }",
  "assets": {}
}
```

- `id`: stable lowercase identifier, starting with a letter, followed by letters,
  digits or hyphens; maximum 64 characters.
- `name`: required, up to 80 characters. Optional `author` and `description` are
  plain text, up to 300 characters each.
- `base`: `orbit`, `nebula`, `daylight` or `sand`. This supplies unspecified tokens,
  light/dark mode, dependent colors and native system-bar fallback colors. A base
  is a fallback, not a constraint on your CSS layout.
- `tokens`: object with any tokens listed in Appearance or
  [`THEME_TOKENS`](../services/frontend/src/themes.js). Colors are `#RRGGBB` or
  `#RRGGBBAA`; fonts are locally installed or bundled family names. Numeric sizes
  are pixels at 100% interface size and become `rem` values at runtime. The
  existing token ranges apply. Custom CSS variables belong in your CSS, not here.
- `css`: stylesheet text, required (an empty string is allowed). Use CSS to change
  component surfaces, gradients, borders, typography, shadows and responsive
  grid/flex layouts. No HTML templates or scripts.
- `assets`: optional object mapping asset ids (same syntax as pack ids) to base64
  data URLs. Allowed MIME types: `image/png`, `image/jpeg`, `image/webp`, `image/gif`,
  `font/woff2`. SVG, HTML, remote URLs and filesystem paths are rejected.

Limits: 512,000 UTF-8 bytes per import, 64,000 bytes of CSS, 24 assets per pack,
12 installed packs, 1,500,000 bytes for the serialized library, and 2,000,000 bytes
for CSS plus expanded asset references. Browser storage quotas can be lower; a
visible warning identifies session-only changes if persistence fails.

### Bundled assets and fonts

```css
:root {
  --studio-paper: url("skin:paper");
}
[data-skin-part=conversation] {
  background-image: var(--studio-paper);
}
@font-face {
  font-family: my-studio-display;
  src: url("skin:display-font") format("woff2");
  font-display: swap;
}
```

Put `paper` and `display-font` in `assets`, with their full base64 data URLs. Use
`"font": "my-studio-display, sans-serif"` in `tokens`. To avoid repeating a large
asset in the generated stylesheet, reference it once in a custom property.
Asset references are expanded locally; importing a pack makes no asset requests
to external servers. Fonts and artwork must be yours to redistribute.

### CSS support and scoping

The importer parses CSS into an AST; it does not insert raw HTML. Every selector
is scoped to `html[data-skin="your-id"]`. Leading `html` and `:root` selectors are
supported, as are `body`, selector lists, pseudo-classes/elements, custom
properties and normal selectors. The stylesheet is removed when a pack is
switched off; CSS variables from that stylesheet disappear with it.

Use flat selectors (not CSS nesting). Supported at-rules are `@media`, `@supports`,
`@container`, `@font-face` and `@keyframes`. Keyframes and font faces are global CSS
constructs: prefix their names with your pack id. Common math, color, gradient,
transform, filter and timing functions are allowed; see the explicit allowlist in
[`skins.js`](../services/frontend/src/skins.js). Unknown functions/rules, CSS
escapes, parser recovery nodes, executable declarations and all non-bundled URLs
are rejected. In particular: no `@import`, `image-set`, `src()` or `expression()`.

CSS can override presentation throughout the app, including dialogs and menus.
Use tokens for the shared palette so native system bars and Quasar controls stay
consistent. Prefer `rem`, `minmax(0, 1fr)` and responsive rules over fixed pixel
layouts. Imported animation/transition declarations are omitted when the device
or Workspace requests reduced motion. Avoid animated image assets: CSS cannot
pause GIF playback. Preserve focus indicators, readable contrast, safe areas,
scrolling and the composer when the mobile keyboard is open.

## Styling hooks

These `data-skin-part` hooks are the stable starting points for version-2 skins:

| Hook | Surface |
| --- | --- |
| `shell` | Workspace grid |
| `navigation` | Navigation rail / mobile navigation |
| `sidebar` | Desktop chat/workflow/run list container |
| `main` | Main route container, including settings |
| `header` | Chat, workflow and run headers |
| `conversation` | Scrollable chat history |
| `message` | Message wrapper; `data-role="user"` or `"assistant"` selects the role |
| `composer` | Message composer, including new-chat composer |
| `welcome` | New-chat screen |
| `code` | Workflow code viewer |

Existing component classes such as `.bubble`, `.btn`, `.pick`, `.field`, `.chip`,
`.rail__item`, `.pane-head__title`, `.row-item` and `.composer__input` allow finer
control, but are less stable than these hooks. Prefer selectors combining a hook
and a class over generated Vue `data-v-*` attributes. The examples demonstrate
both. Never depend on chat text, secrets or DOM-generated ids in selectors.

The library previews are intentionally small, sandboxed, network-free mock
workspaces, not screenshots of every route. Check your pack against real chats,
menus, code, workflow graphs, settings, collapsed sidebars and narrow/touch
viewports. CSS alone does not replace the app's functionality or navigation model.

## Recovery and trust

Only install packs you trust. CSS cannot execute JavaScript or load remote assets
here, but it **can still hide, move or visually misrepresent interface controls**.
Validation does not guarantee a readable or accessible design.

- Press **Ctrl / Cmd + Alt + 0** to immediately restore stock Orbit. This shortcut
  is registered outside the styled interface and works even when `#app` is hidden.
- On touch devices, open `/settings/appearance?safe-appearance=1` on your instance.
  Safe mode runs before mounting the UI and persists the recovered selection when
  storage is available. It never deletes your packs.
- The active pack also exposes **Restore default appearance** in the library.

Corrupt stored packs are revalidated and dropped on startup/storage sync rather
than blocking app startup. You can remove or edit a broken pack after recovering.
