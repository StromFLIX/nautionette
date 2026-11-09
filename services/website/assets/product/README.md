# Website product screenshots

These are **real Chromium screenshots of the current Vue application**, rendered
from `services/frontend` at app revision
`fe87eb9da6103e88b4aae1f5e7553e0219610c9f`. They are not illustrations,
composites or hand-built HTML facsimiles. No device frame, watermark, injected
styles, hidden controls or UI source changes were used. The website supplies its
own device framing.

## Sample-content disclosure

All conversations, profile names, timestamps, completed tool-call records and
published-output metadata are deterministic **fictional demonstration data**.
The same sample chat is shown on both devices: desktop shows Chat, phone shows
Outputs. Profiles are Research, Builder and Writing. No customers, usage metrics,
production account, real user data, model calls, backend mutations or generated
DOCX/PPTX/PDF contents are involved. File names, byte counts and ready states are
fixture metadata illustrating the app's real OutputCard components; this capture
is not an end-to-end document-generation or download test. There are deliberately
no preview files, so the app does not fetch or display synthetic/broken previews.

`tests/website-product-fixture.js` reuses `mockAgents`, `mockDesign` and their
persistent local SSE helper. All API requests are intercepted, including the
valid empty composer-draft response (`text`, `attachments`, `revision`).
Read-state acknowledgements are handled locally; unexpected mutations fail.
Remote requests are blocked. The token in localStorage is an inert fixture
string, never an authentication credential. Vite and the SSE helper listen only
on loopback.

## Deliverables

| File | Natural pixels | Bytes | Encoding |
| --- | --- | ---: | --- |
| `desktop.webp` | 1920 × 1260 | 61,212 | Lossless WebP |
| `desktop-small.webp` | 1280 × 840 | 40,080 | Native DPR 1 browser capture, lossless WebP |
| `phone.webp` | 780 × 1688 | 40,458 | Lossless WebP |
| `desktop.png` | 1920 × 1260 | 174,012 | Original browser capture |
| `desktop-small.png` | 1280 × 840 | 107,457 | Original browser capture at DPR 1 |
| `phone.png` | 780 × 1688 | 127,793 | Original browser capture |

Combined distribution WebPs: **141,750 bytes** (under 200 KB total). Lossless
WebP decoding was checked pixel-for-pixel against all three original PNGs. The
small variant is a third actual browser capture, **not a resize**: it uses the
same desktop viewport, fixture state and framing assertions, rendered directly
at DPR 1. No resampling or lossy encoding softens the native UI text.

Desktop: viewport **1280 × 840**, DPR **1.5**. Desktop-small: the same viewport
**1280 × 840**, DPR **1**. Phone: viewport **390 × 844**, DPR **2**.
All variants use the unmodified Denkzeug light theme, original solid logo and
**125% interface size** (`html` font-size 20 px, no browser zoom).
Locale `en-US`; timezone `UTC`; fixed browser clock `2026-06-15T10:30:00Z`;
reduced motion. Capture environment: Node 24.22.2, Playwright 1.64.1, Chromium
156.0.8078.4, Linux. Browser fonts come from the app's own assets.

## Refresh the screenshots

From the repository root:

```sh
cd services/frontend
npm ci --ignore-scripts
# Only needed if this Playwright version has no installed Chromium:
node node_modules/@playwright/test/cli.js install chromium
npm run capture:website
```

The equivalent capture command is `node scripts/capture-website.mjs`. It starts
and stops its own loopback Vite server, checks the rendered theme, font size,
full desktop message/card framing, phone download-button visibility, lack of
horizontal overflow, browser/HTTP errors, loading/error notices and accidental
sends/settings writes. It retains `desktop.png`, `desktop-small.png`, `phone.png`
and `capture-report.json` (three variants, viewports, clock, observed API requests
and empty error lists). Each retained image requires two consecutive identical
browser screenshot frames. Desktop and phone were verified byte-identical by
SHA-256 to their original captures. Chromium can vary the small idle SVG context
meter's rasterization between browser contexts; content and layout are fixed.

When UI or API contracts change, update only the fixture and capture assertions
to the new real app contracts; do not inject layout overrides to preserve an old
look. Re-run the command, inspect all three PNGs and WebPs visually, then
re-encode and update the table above. The dotted header ring with a dash is the
app's idle context meter, not a loading spinner.

### Native PhotoCraft attempt and concrete infrastructure blocker

PhotoCraft **0.22.0** was discovered via `craft_apps` / `craft_tools`. Its
`doc_export` schema supports lossless WebP by omitting `quality`. However the
original capture session's PhotoCraft engine had **no filesystem read authority**:

```text
doc_open({"path":"/workspace/desktop.png"})
→ automation path rejected: absolute paths are not allowed: `/workspace/desktop.png`
doc_open({"path":"desktop.png"})
→ automation filesystem access is not granted: read authority is absent
```

No screenshot was loaded, edited or exported by PhotoCraft, and no native source
is claimed. With filesystem authority enabled, the intended native sequence is:

```text
doc_open({"path":"desktop.png"})
doc_export({"path":"desktop.webp","format":"webp"})
doc_open({"path":"desktop-small.png"})
doc_export({"path":"desktop-small.webp","format":"webp"})
doc_open({"path":"phone.png"})
doc_export({"path":"phone.webp","format":"webp"})
```

To finish distribution files despite that concrete blocker, the **actual**
encoding used Pillow 12.2.0 / libwebp 1.6.0 with lossless compression, no image
retouching. Reproduce from the repository root:

```sh
uv venv /workspace/image-env
uv pip install --python /workspace/image-env/bin/python Pillow==12.2.0
/workspace/image-env/bin/python - <<'PY'
from pathlib import Path
from PIL import Image
root = Path('services/website/assets/product')
for name in ['desktop', 'desktop-small', 'phone']:
    with Image.open(root / f'{name}.png') as image:
        image.save(root / f'{name}.webp', lossless=True, method=6)
        with Image.open(root / f'{name}.webp') as exported:
            assert exported.convert('RGB').tobytes() == image.convert('RGB').tobytes()
PY
```

Capture checks passed for all three variants (two viewport sizes, three DPRs).
The desktop-small follow-up preserves the original full desktop and phone PNG
and WebP bytes unchanged; only the small distribution WebP is replaced, and its
native browser PNG is retained. No PhotoCraft authority bypass was attempted.

At the original capture commit, frontend unit/script tests: **247/247
passed**. `vite build` passed. These checks do not claim a production deployment
or real document-generation run.
