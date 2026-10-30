import { test, expect } from '@playwright/test'
import { readFile } from 'node:fs/promises'
import { mockDashboards } from './dashboard-fixture.js'
import { THEMES, resolveTheme } from '../src/themes.js'
import { PREFERENCES_KEY } from '../src/preferences-schema.js'

const rgb = hex => `rgb(${[1, 3, 5].map(index => parseInt(hex.slice(index, index + 2), 16)).join(', ')})`
const open = async page => { await page.goto('/dashboards/test-interface?agent=agent-1'); await expect(page.getByRole('heading', { name: 'Test interface', exact: true })).toBeVisible() }

for (const theme of THEMES) {
  test(`dashboard inherits ${theme.id} tokens and renders without executable markup`, async ({ page, context }) => {
    await mockDashboards(context)
    const errors = []; page.on('pageerror', error => errors.push(error.message))
    await page.addInitScript(({ key, theme }) => localStorage.setItem(key, JSON.stringify({ theme })), { key: PREFERENCES_KEY, theme: theme.id })
    await open(page)
    const tokens = resolveTheme(theme.id)
    await expect(page.locator('.dashboard-card').first()).toHaveCSS('background-color', rgb(tokens['surface-panel']))
    await expect(page.locator('.dashboard-part__metric')).toHaveCSS('color', rgb(tokens.success))
    await expect(page.locator('.dashboard-part__text')).toContainText('<img src=x onerror=alert(1)>')
    await expect(page.locator('.dashboard-card img')).toHaveCount(0)
    await page.getByRole('button', { name: 'Validate candidate', exact: true }).click()
    await expect(page.getByRole('dialog')).toBeVisible()
    await expect(page.locator('.dashboard-review')).toHaveCSS('background-color', rgb(tokens['surface-overlay']))
    await page.keyboard.press('Escape')
    expect(errors).toEqual([])
  })
}

for (const skinId of ['winamp-classic', 'windows-xp']) {
  test(`dashboard and workflow controls inherit ${skinId} skin`, async ({ page, context }) => {
    await mockDashboards(context)
    const skin = JSON.parse(await readFile(new URL(`../src/skin-examples/${skinId}.skin.json`, import.meta.url), 'utf8'))
    await page.addInitScript(({ key, skin }) => localStorage.setItem(key, JSON.stringify({ theme: skin.base, skin: skin.id, skins: [skin] })), { key: PREFERENCES_KEY, skin })
    await open(page)
    await expect(page.locator('html')).toHaveAttribute('data-skin', skin.id)
    await expect(page.getByRole('button', { name: 'Validate candidate', exact: true })).toBeVisible()
    await page.getByRole('button', { name: 'Validate candidate', exact: true }).click()
    await expect(page.getByRole('dialog')).toBeVisible()
    expect(await page.locator('.dashboard-pane').evaluate(element => element.scrollWidth <= element.clientWidth)).toBe(true)
  })
}

for (const width of [1440, 900, 390, 320]) {
  test(`dashboard layout and cold-start back navigation work at ${width}px`, async ({ page, context }, testInfo) => {
    await mockDashboards(context)
    await page.setViewportSize({ width, height: 900 })
    await open(page)
    expect(await page.locator('.dashboard-pane').evaluate(element => element.scrollWidth <= element.clientWidth)).toBe(true)
    expect(await page.locator('.dashboard-grid').evaluate(element => element.scrollWidth <= element.clientWidth)).toBe(true)
    if (width <= 600) {
      const boxes = await page.locator('.dashboard-card').evaluateAll(elements => elements.map(element => element.getBoundingClientRect().x))
      expect(new Set(boxes).size).toBe(1)
    }
    await page.screenshot({ path: testInfo.outputPath('dashboard.png'), fullPage: true })
    if (width <= 900) {
      await page.getByRole('button', { name: 'Back to dashboards', exact: true }).click()
      await expect(page).toHaveURL(/\/dashboards\?agent=agent-1$/)
      await expect(page.locator('.shell__side')).toBeVisible()
    }
  })
}

test('offline discovery is not presented as an empty dashboard collection', async ({ page, context }) => {
  await mockDashboards(context)
  await context.route(/\/api\/dashboards\?/, route => route.abort('failed'))
  await page.goto('/dashboards?agent=agent-1')
  await expect(page.locator('.dashboard-pane').getByRole('alert')).toContainText('Dashboard discovery is unavailable')
  await expect(page.getByText(/^No dashboards yet\./)).toHaveCount(0)
})

test('pins appear above chats, user can unpin and discover the dashboard again', async ({ page, context }) => {
  const state = await mockDashboards(context)
  await page.goto('/chats?agent=agent-1')
  await expect(page.getByRole('navigation', { name: 'Agent dashboards' }).getByRole('link', { name: 'Test interface', exact: true })).toBeVisible()
  await page.getByRole('navigation', { name: 'Agent dashboards' }).getByRole('link', { name: 'Test interface', exact: true }).click()
  await expect(page.getByRole('heading', { name: 'Test interface', exact: true })).toBeVisible()
  await page.getByRole('button', { name: 'Unpin dashboard', exact: true }).click()
  await expect(page.getByRole('button', { name: 'Pin dashboard', exact: true })).toBeVisible()
  expect(state.writes).toEqual([{ expected_revision: 1, pinned: false }])
  await page.goto('/chats?agent=agent-1')
  await expect(page.getByRole('navigation', { name: 'Agent dashboards' })).toHaveCount(0)
  await page.goto('/dashboards?agent=agent-1')
  await expect(page.locator('.dashboard-index').getByRole('link', { name: 'Test interface', exact: true })).toBeVisible()
})

test('live source errors and stale state are explicit without losing the last value', async ({ page, context }) => {
  const state = await mockDashboards(context)
  await open(page)
  state.view.sources.feed.state = 'stale'; state.view.sources.feed.error = 'Integration unavailable; showing last good data.'
  state.view.observed_at = Date.now() / 1000
  await expect(page.locator('.dashboard-card').first()).toContainText('stale')
  await expect(page.locator('.dashboard-card').first()).toContainText('Integration unavailable')
  await expect(page.locator('.dashboard-part__metric')).toHaveText('7')
  expect(state.snapshots).toBe(0) // Native SSE, not a browser polling loop.
})

test('changed live-bound inputs invalidate an open action review', async ({ page, context }) => {
  const state = await mockDashboards(context)
  await open(page)
  await page.getByRole('button', { name: 'Validate candidate', exact: true }).click()
  state.view.actions.check.context_hash = 'candidate-two'; state.view.actions.check.resolved_inputs.commit = 'def456'
  state.view.observed_at = Date.now() / 1000
  await expect(page.getByRole('dialog')).toContainText('Dashboard or source data changed')
  await expect(page.getByRole('button', { name: 'Run Validate candidate', exact: true })).toBeDisabled()
  expect(state.actions).toEqual([])
})

test('uncertain action submission keeps its exact request across reload and retries once', async ({ page, context }) => {
  const state = await mockDashboards(context)
  state.failAction = true
  await open(page)
  await page.getByRole('button', { name: 'Validate candidate', exact: true }).click()
  await page.getByRole('spinbutton', { name: 'Count *', exact: true }).fill('3')
  await page.getByRole('button', { name: 'Run Validate candidate', exact: true }).click()
  await expect(page.getByRole('dialog')).toContainText('Retry the SAME request ID')
  expect(state.actions).toHaveLength(1)
  const original = structuredClone(state.actions[0])
  expect(original.inputs).toEqual({ count: 3 })
  expect(original.context_hash).toBe('candidate-one')
  expect(original.confirmed).toBe(true)
  await page.reload()
  await expect(page.getByRole('button', { name: 'Validate candidate', exact: true })).toBeVisible()
  await page.getByRole('button', { name: 'Validate candidate', exact: true }).click()
  await expect(page.getByRole('button', { name: 'Retry same submission', exact: true })).toBeVisible()
  state.failAction = false
  await page.getByRole('button', { name: 'Retry same submission', exact: true }).click()
  await expect(page.getByRole('dialog')).toHaveCount(0)
  expect(state.actions).toHaveLength(2)
  expect(state.actions[1]).toEqual(original)
  await expect(page.locator('.dashboard-pane__runs')).toContainText('running')
  await expect(page.getByRole('button', { name: 'Validate candidate', exact: true })).toBeDisabled()
  await expect(page.locator('.dashboard-pane__runs')).not.toContainText('completed')
})
