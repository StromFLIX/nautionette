# Public workspace explorer

## Design contract

The product section tells one story: make work on a computer, then pick it up
remotely on a phone. Desktop stays first at every width. Actual app captures
remain available, including without JavaScript. **Explore demo** is a separate,
clearly labelled interactive wireframe, not an authenticated app or a recording
of real work.

- Palette: paper `#f8f9fc`, white `#ffffff`, ink `#20232c`, blue for agents,
  lilac for conversations and amber for workflows. Night appearance is local to
  the example, not a change to the website or saved app preferences.
- Typography: inherit Sora, with readable compact controls rather than scaled
  screenshot text. Focus rings and touch targets remain visible at 320px.
- Layout: a small navigation rail, contextual list and work area at large sizes;
  compact navigation and a readable work area inside narrow containers.
- Principle: show a few coherent interactions, not a grid of decorative cards or
  a facsimile that promises a functioning account. No unsolicited animation.

## Boundaries

`product-preview.mjs` progressively enhances the desktop frame. Actual screen is
the default. Only selecting Explore demo imports `workspace-demo.mjs`; a failed
import leaves the screenshot and the rest of the site usable. Tabs support arrow
keys, Home/End and native Enter/Space activation. Returning to the screenshot
preserves the in-memory example; Reset demo restores its initial state.

`workspace-demo.mjs` owns its sample state, rendering and scoped event handlers.
`workspace-demo.css` must remain scoped under `.workspace-demo`. The component
must not fetch, call an API, submit a form, open a socket, execute tools, access
storage/cookies, or request credentials. Sample topic input is plain text and
must be escaped when rendered. No real user/account data is used.

Visitors can inspect a conversation and sample output, explore workflow steps,
manually advance a simulated execution, inspect statistics derived from sample
runs, and try local appearance/agent settings. Execution times and statistics are
illustrative data, not performance or adoption claims. Output previews are
explicit examples, not pretend document downloads. No work occurs on a server.

## Validation and deployment

- `npm --prefix services/frontend run test:website` runs model, website and
  progressive-enhancement contract tests.
- `cd services/frontend && npx playwright test tests/website.spec.js --workers=1`
  checks the production page markup, screenshot fallback, interaction flow,
  keyboard use, responsive order, isolation and failure recovery.
- The website Dockerfile must copy both demo modules, the preview controller and
  stylesheet. A checkout-only development preview is insufficient.
- Change canonical `index.template.html`, then run `python scripts/sync_brand.py`.
  Bump CSS/entry-module query versions when changing cached public assets.
- Keep the capture provenance under `assets/product/README.md` separate: those
  are real application screenshots, while this explorer is a wireframe.
