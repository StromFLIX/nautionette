#!/usr/bin/env node
import { chromium, expect } from '@playwright/test'
import { createServer } from 'vite'
import { mkdir, writeFile } from 'node:fs/promises'
import { fileURLToPath } from 'node:url'
import { mockWebsiteProduct, captureTime, chatPath } from '../tests/website-product-fixture.js'

// Capture the real Vue application; never navigate to a production backend.
// This command intentionally produces source PNGs only. See the asset README
// for the native PhotoCraft lossless WebP export commands. Every variant is
// captured at its native device pixel ratio; no screenshot is resized.
const frontendRoot = fileURLToPath(new URL('../', import.meta.url))
const destination = fileURLToPath(new URL('../../website/assets/product/', import.meta.url))
await mkdir(destination, { recursive: true })
const server = await createServer({ root: frontendRoot, server: { host: '127.0.0.1', port: 0 } })
await server.listen()
const origin = server.resolvedUrls.local[0]
let browser
const report = []
try {
  browser = await chromium.launch({ headless: true })
  for (const [name, width, height, deviceScaleFactor] of [['desktop', 1280, 840, 1.5], ['desktop-small', 1280, 840, 1], ['phone', 390, 844, 2]]) {
    const context = await browser.newContext({ viewport: { width, height }, deviceScaleFactor, locale: 'en-US', timezoneId: 'UTC', colorScheme: 'light', reducedMotion: 'reduce', serviceWorkers: 'block' })
    const page = await context.newPage()
    await page.clock.setFixedTime(captureTime)
    const errors = []
    page.on('pageerror', error => errors.push(error.message))
    page.on('console', message => { if (message.type() === 'error') errors.push(message.text()) })
    page.on('response', response => { if (response.status() >= 400) errors.push(`${response.status()} ${response.url()}`) })
    const fixture = await mockWebsiteProduct(context)
    // Fail closed on remote traffic: only the local Vite application and local
    // intercepted SSE fixture can ever receive a request.
    await context.route('**/*', route => {
      const url = new URL(route.request().url())
      if (!['127.0.0.1', 'localhost'].includes(url.hostname)) throw new Error(`Unexpected external request: ${url}`)
      return route.fallback()
    })
    await page.goto(`${origin.replace(/\/$/, '')}${chatPath}`)
    await expect(page.locator('html')).toHaveCSS('font-size', '20px')
    await expect(page.locator('html')).toHaveAttribute('data-theme', 'denkzeug')
    await expect(page.getByRole('textbox', { name: 'Message', exact: true })).toBeVisible()
    await page.evaluate(() => document.fonts.ready)
    // Let the thread's normal automatic scroll-to-end finish before checking
    // framing. No injected CSS, zoom or DOM changes are used for composition.
    await page.waitForTimeout(600)
    if (name === 'phone') {
      await page.getByRole('tab', { name: /Outputs/ }).click()
      for (const file of ['product-brief.docx', 'product-slides.pptx', 'brief-and-slides.pdf']) {
        await expect(page.getByRole('button', { name: `Download ${file}`, exact: true })).toBeInViewport()
      }
    } else {
      await expect(page.locator('.tool-group__label')).toHaveText('Ran 3 tool calls')
      await expect(page.locator('.output-card--compact')).toBeInViewport({ ratio: 1 })
      await expect(page.getByText('Create a product brief and an editable slide deck from these notes.', { exact: false })).toBeInViewport({ ratio: 1 })
    }
    await expect(page.getByRole('alert')).toHaveCount(0)
    await expect(page.getByText(/Draft sync unavailable|Connecting|Reconnecting|Loading…|Something went wrong/)).toHaveCount(0)
    await page.evaluate(() => document.fonts.ready)
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
    expect(errors).toEqual([])
    expect(fixture.state.sent).toEqual([])
    expect(fixture.state.writes).toEqual([])
    // Require consecutive identical browser frames, including the idle SVG meter,
    // before retaining a capture. This avoids a transient compositor frame.
    let previous, capture
    for (let attempt = 0; attempt < 8; attempt++) {
      capture = await page.screenshot({ animations: 'disabled', caret: 'hide' })
      if (previous?.equals(capture)) break
      previous = capture
      capture = null
      await page.waitForTimeout(100)
    }
    expect(capture, `Stable ${name} browser frames`).not.toBeNull()
    await writeFile(`${destination}${name}.png`, capture)
    report.push({ name, viewport: { width, height }, deviceScaleFactor, pixels: { width: width * deviceScaleFactor, height: height * deviceScaleFactor }, clock: captureTime.toISOString(), timezone: 'UTC', errors, requests: fixture.requests })
    await context.close()
  }
  await writeFile(`${destination}capture-report.json`, JSON.stringify(report, null, 2) + '\n')
  console.log(`Captured and checked desktop + desktop-small + phone PNGs in ${destination}`)
} finally {
  await browser?.close()
  await server.close()
}
