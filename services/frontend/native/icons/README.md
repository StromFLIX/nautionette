# Denkzeug launcher artwork

Canonical launcher exports live in `branding/native/`. These files and the copies
in `services/frontend/build/`, `services/frontend/desktop/` and `public/brand/`
are synchronized by `python scripts/sync_brand.py`. Do not edit the canonical
`branding/logo.svg` as a side effect of export.

## iOS

`app-icon.svg` is the approved `branding/logo.svg` artwork, centered unchanged on
a square white canvas. It was opened/exported with native VectorCraft 0.23.0;
only the artboard padding and white background were added. The mark occupies
about 58% of the square. No corners are baked into the canvas.

- `app-icon.png`: 1024 × 1024 opaque-white iOS App Store icon.

The iOS sync uses the current Capacitor template's single universal AppIcon slot.
Re-export the 512-point artboard at scale 2 after changing the logo.

## Desktop (Windows, Linux and macOS)

`desktop-icon.svg` / `desktop-icon.png` are separate VectorCraft exports for the
small OS icon slots. The mark is scaled **156.5% around (256, 256)** on a
512-point **transparent** artboard, occupying about **90%**, rather than iOS's
58%. The black tile has transparent outer margins/corners and an opaque white
glyph, like Android; there is no white canvas, border or matte. The glyph's open
tail ends flush with the tile's bottom edge. Mobile exports are unchanged.
Export at scale 2 for the 1024 × 1024 RGBA PNG. Preserve alpha when exporting and
do not copy iOS's opaque padded icon back here.

- `../../build/icon.png`: desktop export in electron-builder's default build
  resources directory; it derives multi-resolution Windows ICO and other OS icons.
- `../../desktop/icon.png`: identical desktop export, bundled via `desktop/**/*`
  for Electron's runtime BrowserWindow/taskbar icon, not just the installer.

Both copies are synchronized from `branding/native/desktop-icon.png`. Pixel
regressions measure the actual mark at 16/24/32/48/256/1024px to catch excess
padding, lost transparency, exterior white fringes and a transparent inner glyph.

## Android

Do **not** reuse the iOS square for Android. Adaptive launchers display the middle
72dp of a 108dp layer and apply their own circle, squircle or rounded-square mask.
The old padded square enlarged an already-rounded mark inside that mask, leaving
uneven white slivers around its corners.

- `android-foreground.svg`: the original mark's white negative-space contour on
  a transparent 512-point artboard. Only the open tail is extended to the bottom
  edge; the curved glyph is unchanged. No outer rounded tile or white matte.
- `ic_launcher_foreground.png`: 432 × 432 transparent xxxhdpi adaptive layer,
  exported from that SVG at scale 0.84375. The native background color is solid
  black all the way to the edges. Android alone determines the outer shape.
- `android-icon.svg`: the same glyph on full-bleed black, enlarged 1.5× around
  the artboard center to match the adaptive layer's visible middle two thirds.
- `ic_launcher.png`: its 192 × 192 legacy/round launcher export (scale 0.375).

After `cap add`/`cap sync`, the versioned platform sync scripts install these
assets into the generated native project. Android removes only stock launcher
PNGs in the five known density directories and supplies xxxhdpi replacements;
Android scales them down on lower-density devices. Adaptive XML resource names
and references remain unchanged; their background becomes black.

The Android sources and PNGs were opened/exported with VectorCraft 0.23.0.
After verifying both platform variants, update `branding/native/source.sha256`
to the canonical logo's SHA-256, run `python scripts/sync_brand.py`, and commit
the exports and synchronized copies. The tracked build resource is intentionally
retained despite the generic `build/` ignore rule.

Pixel regression tests check transparent foregrounds, matching legacy/adaptive
scale and clean circle/squircle/rounded-square mask edges. Sync tests use the real
Capacitor Android template. Device/emulator checks and signed installer builds
still belong to the native release pipeline; unit tests do not replace them.
