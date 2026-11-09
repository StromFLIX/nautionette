# Denkzeug launcher artwork

`app-icon.svg` is the approved `branding/logo.svg` artwork, centered unchanged on
a square white canvas. It was opened/exported with native VectorCraft 0.23.0;
only the artboard padding and white background were added. The mark occupies
about 58% of the square so adaptive launcher masks have breathing room. No
corners are baked into the canvas: each OS applies its own launcher mask.

Committed raster exports (all from that SVG, no separate redrawn marks):

- `app-icon.png`: 1024 × 1024 opaque-white iOS App Store icon.
- `ic_launcher.png`: 192 × 192 Android xxxhdpi legacy/round launcher icon.
- `ic_launcher_foreground.png`: 432 × 432 Android xxxhdpi adaptive foreground.
- `../../build/icon.png`: the same 1024 × 1024 export, in electron-builder's
  default build-resources directory. Electron-builder derives platform icons
  without changing application IDs, signing identities or installer names.
- `../../desktop/icon.png`: the same export, bundled via `desktop/**/*` for
  Electron's runtime BrowserWindow icon (not just the installer/application icon).

After `cap add`/`cap sync`, the versioned platform sync scripts install these
assets into the generated native projects. Android removes only stock launcher
PNGs in the five known density directories and supplies xxxhdpi replacements;
Android scales them down on lower-density devices. Adaptive XML resource names
and their existing references remain unchanged; their background becomes white.
The iOS copy uses the current Capacitor template's single universal AppIcon slot.

Canonical launcher exports live in `branding/native/`; the files here, in
`services/frontend/build/`, and in `services/frontend/desktop/` are synchronized
copies. When the approved logo changes, reopen `branding/logo.svg` in VectorCraft,
add square white padding with the same proportions, and re-export into
`branding/native/` (512-point artboard: scales 2, 0.375 and 0.84375). Update
`branding/native/source.sha256` after verifying the artwork, then run
`python scripts/sync_brand.py` to update every platform copy. The tracked build
resource is intentionally retained despite the generic `build/` ignore rule.
Do not edit the canonical logo as a side effect of export.

Device/emulator launcher-mask checks and signed installer builds still belong
to the native release pipeline; unit tests do not substitute for those checks.
