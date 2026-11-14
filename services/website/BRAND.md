# Denkzeug — shared brand direction

The approved website direction now carries through the application. Display names,
logos, typography and visual hierarchy use Denkzeug. Repository links follow the
canonical repository in `branding/brand.json`, now `StromFLIX/denkzeug`. Domains,
environment variable names, package/app IDs, installer filenames and API contracts
retain their existing identities; a repository rename does not migrate them.

## Design intent

A German tool-word, presented as a dictionary entry, in an open workspace. The
living grid is the main expressive element: small agents move, sessions gather,
and workflows form trains. It is an illustration, not a live activity monitor.
No usage statistics or customer results are fabricated.

- **Paper:** `#f8f9fc`; **white:** `#ffffff`; **ink:** `#20232c`.
- **Agents:** `#657ade`; **sessions:** `#a397c8`; **workflows:** `#d3a26e`.
- **Wordmark / interface:** Sora, restrained weights, tightly set display type.
- **Definition:** Source Serif 4, for the dictionary-entry association rather than
  a decorative headline. Other writing systems fall back to installed fonts.
- **Layout:** centred name and definition; left-aligned text below; shared content
  edges; three principles, then three capabilities. No automatic scroll reveals.
- **Motion:** one cellular field; quiet crossfades at a capped 24fps, one generation
  per 420ms. Eight Life motifs have varied orientations, locations and lifetimes;
  short trains take newly generated routes, with occasional forks that rejoin.
  Cohorts arrive and disperse on independent schedules, not a shared loop/reset.
  A fresh seed changes each visit's composition; explicit seeds keep tests
  reproducible. Density is bounded, with mobile-scaled populations and quieter
  space behind the text. Simulation runs only on a generation change, never
  per rendered frame. Stops off-screen and in hidden tabs. Reduced motion starts
  still; the visitor can explicitly play or pause and that choice is saved locally.

The brief's own living-grid direction wins over generic SaaS hero treatments or
prescriptive website kits. The supplied icon and the word itself remain the hero.
Immediately below, a plain-language product explanation and actual desktop/phone
interface captures make the product concrete before the principles section.
Desktop shows a conversation and editable work; the phone shows the same chat's
Outputs. Screens are captured from the app with documented, deterministic sample
content, never production/user data, and labelled accordingly. Device framing is
CSS outside the captures, not a redrawn interface. Desktop leads on every screen,
followed by the remote phone view: make it, then pick it up. Visual and document
order stay aligned on mobile. Full-size image links work with keyboard navigation
and without JavaScript. The desktop image can be enhanced with a screenshot
click-through: actual app captures only, with outlined rectangles recorded from
real DOM controls. Clicking changes the image; no interface is recreated in HTML,
no simulation runs, and no pictured control acts as a live app. A screen picker
below the image provides usable touch/keyboard targets. Capture provenance and
measured rectangles live beside the images under `assets/product-tour/`.
The three later illustrations explain artifact types, workflows and miniapps.

## Brand assets

The canonical `branding/logo.svg` is the original solid single-path mark,
restored for crisp rendering at small sizes. The nested-line experiment is not
used in the product. It has a transparent background and no raster dependency.
`assets/denkzeug-icon.svg` and the application's mark are generated copies.
`favicon.svg` uses the same artwork and adapts to a dark browser theme.

Edit `index.template.html`, not the generated `index.html`. Brand text, assets,
fonts and colors are synchronized by `python scripts/sync_brand.py`; CI checks
for drift. See [`branding/README.md`](../../branding/README.md) for all consumers.

The locally served, Latin/Latin-extended WOFF2 subsets come from Google Fonts'
[Sora](https://github.com/google/fonts/tree/main/ofl/sora) and
[Source Serif 4](https://github.com/google/fonts/tree/main/ofl/sourceserif4)
variable fonts. Their SIL Open Font Licenses are in `assets/fonts/`. There is no
Google Fonts request or third-party font dependency at page load.

## Definition and language

The German text in `definitions.mjs` is exactly the supplied subtitle, a play on
the [German Wikipedia definition of Werkzeug](https://de.wikipedia.org/wiki/Werkzeug).
English is the default even when the browser prefers another language. The
visitor can choose the definition in English, German, Spanish, French, Portuguese,
Italian, Chinese, Japanese, Korean, Hindi, Arabic or Russian. Only an explicit
choice is remembered. The definition has its own `lang` and `dir`; selecting
Arabic does not turn the English navigation right-to-left. These are initial
translations; native-language editorial review is welcome during iteration.

## Run and check

Serve `services/website` with any static HTTP server, for example:

```sh
python -m http.server 9080 --directory services/website
```

Existing deployment configuration (`APP_URL`, `APP_ENVIRONMENT`,
`IOS_DOWNLOAD_URL`) still goes through `entrypoint.sh`. The container copies all
local modules, fonts and SVG assets. The release loader continues to link only
published `denkzeug-*` installers and legacy `nautionette-*` assets; unavailable platform builds never
produce a fabricated download URL.

```sh
cd services/frontend
npm ci
npm run test:website
npx playwright install chromium
npx playwright test tests/website.spec.js
```

Unit checks simulate seven minutes on mobile and desktop, guarding against short
loops, repeated routes, excessive density and unbounded populations.

The website browser suite runs in CI with the existing frontend browser checks.
It covers 1440/390/320px, every definition language, RTL, saved preferences,
reduced motion, paused activity, blocked storage, clipboard denial, missing
releases, runtime instance links, staging, keyboard access and no JavaScript.
Desktop and mobile emulation are not a substitute for testing on a physical
phone, particularly for safe areas and touch/browser chrome.
