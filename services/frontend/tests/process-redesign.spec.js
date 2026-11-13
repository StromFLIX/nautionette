import { test, expect } from '@playwright/test'
import { THEMES } from '../src/themes.js'
import { PREFERENCES_KEY } from '../src/preferences-schema.js'
import { mockDesign } from './design-fixture.js'

async function mockProcesses (context) {
  const state = await mockDesign(context)
  const run = { workflow_id: 'digest-record', workflow: 'digest', status: 'completed', input: { topic: 'Product updates' }, result: { delivered: true }, created_at: 1700000000, trigger: 'manual' }
  const app = { id: 'digest-reader', title: 'Digest reader', icon: 'article', revision: 1, active_revision: 1, pinned: false, archived: false,
    active: { revision: 1, manifest: { description: 'Read the latest workflow results.', frontend: { html: '<h1>Digest reader</h1>' } } },
    releases: [{ revision: 1, state: 'ready', created_at: 1700000000 }] }
  await context.route('**/api/runs**', route => {
    const path = new URL(route.request().url()).pathname
    return route.fulfill({ json: path === '/api/runs' ? { runs: [run] } : path.endsWith('/graph') ? { ...state.workflow.graph, mode: 'execution', status: 'completed' } : { run, temporal: { status: 'COMPLETED', close_time: '2023-11-14T22:14:00Z' } } })
  })
  await context.route('**/api/miniapps**', route => {
    const path = new URL(route.request().url()).pathname
    return route.fulfill({ json: path === '/api/miniapps' ? { apps: [app] } : path.endsWith('/status') ? { runtime: { status: 'frontend-only', data_bytes: 0 } } : app })
  })
  return state
}

async function noOverflow (page) {
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
  const pane = page.locator('.shell__main')
  expect(await pane.evaluate(element => element.scrollWidth <= element.clientWidth)).toBe(true)
}

test('empty process views explain workflows, runs, and apps with real next steps', async ({ page, context }) => {
  await mockProcesses(context)
  for (const [path, title, action] of [
    ['/workflows', 'Choose a workflow', 'Open chats'],
    ['/runs', 'Inspect an execution', 'Open workflows'],
    ['/apps', 'An interface for your task', 'Open chats']
  ]) {
    await page.goto(path)
    const empty = page.locator('.workspace-empty')
    await expect(empty.getByRole('heading', { name: title, exact: true })).toBeVisible()
    await expect(empty.getByRole('link', { name: action, exact: true })).toBeVisible()
    await expect(empty.locator('.workspace-empty__glyph')).toHaveCSS('clip-path', 'none')
    await noOverflow(page)
  }
  await page.getByRole('link', { name: 'Manage app runtimes', exact: true }).click()
  await expect(page).toHaveURL(/\/settings\/app-runtimes$/)
})

test('workflow disable shows saved intent and retries schedule synchronization without toggling it back', async ({ page, context }) => {
  const state = await mockProcesses(context)
  const writes = []
  const errors = []
  page.on('pageerror', error => errors.push(error.message))
  await context.route('**/api/workflows/digest/settings', async route => {
    const payload = route.request().postDataJSON()
    writes.push(payload)
    state.workflow.settings = { disabled: payload.disabled }
    if (writes.length === 1) return route.fulfill({ status: 503, json: { detail: {
      message: 'Settings saved, but schedule synchronization is pending. Temporal may still start scheduled work.',
      settings: state.workflow.settings,
      schedule_sync_pending: true
    } } })
    return route.fulfill({ json: state.workflow.settings })
  })
  await page.goto('/workflows/digest?tab=Run')
  await page.getByRole('button', { name: 'Workflow actions', exact: true }).click()
  await page.getByRole('button', { name: 'Disable', exact: true }).click()
  await page.keyboard.press('Escape')
  await expect(page.getByRole('alert')).toContainText('schedule synchronization is pending')
  await expect(page.getByRole('button', { name: 'Run now', exact: true })).toBeDisabled()
  await page.getByRole('button', { name: 'Retry sync', exact: true }).click()
  await expect(page.getByRole('alert')).toHaveCount(0)
  expect(writes).toEqual([{ disabled: true }, { disabled: true }])
  expect(errors).toEqual([])
  await noOverflow(page)
})

test.describe('touch process controls', () => {
  test.use({ hasTouch: true, viewport: { width: 320, height: 700 } })
  test('graph targets and workflow fields accommodate touch without overflowing', async ({ page, context }) => {
    await mockProcesses(context)
    await page.goto('/workflows/digest')
    const targets = page.locator('.flow__toolbar .btn--icon, .flow__zoom .btn--icon')
    await expect(targets.first()).toBeVisible()
    for (const target of await targets.all()) {
      const box = await target.boundingBox()
      expect(box.width).toBeGreaterThanOrEqual(44)
      expect(box.height).toBeGreaterThanOrEqual(44)
    }
    await noOverflow(page)
    await page.getByRole('button', { name: 'Run', exact: true }).click()
    const fields = page.locator('.schedule-grid .field')
    await expect(fields.first()).toBeVisible()
    for (const field of await fields.all()) {
      expect(await field.evaluate(element => parseFloat(getComputedStyle(element).fontSize))).toBeGreaterThanOrEqual(16)
    }
    await noOverflow(page)
  })
})

for (const theme of THEMES) {
  for (const width of [1440, 320]) {
    test(`${theme.name} process details stay readable and actionable at ${width}px`, async ({ page, context }) => {
      await context.addInitScript(({ key, theme }) => {
        if (window === window.top) localStorage.setItem(key, JSON.stringify({ theme }))
      }, { key: PREFERENCES_KEY, theme: theme.id })
      await page.setViewportSize({ width, height: 900 })
      await mockProcesses(context)
      const errors = []
      page.on('pageerror', error => errors.push(error.message))
      await page.goto('/workflows/digest?tab=History')
      await expect(page.getByRole('heading', { name: 'No runs yet', exact: true })).toBeVisible()
      await page.getByRole('button', { name: 'Configure a run', exact: true }).click()
      await expect(page.getByRole('heading', { name: 'Run this workflow', exact: true })).toBeVisible()
      await expect(page.getByRole('button', { name: 'Run now', exact: true })).toBeVisible()
      await expect(page.locator('.avatar-sq')).toHaveCSS('clip-path', 'none')
      await noOverflow(page)
      await page.screenshot({ path: test.info().outputPath('workflow-run.png') })

      await page.goto('/runs/digest-record')
      await page.getByRole('button', { name: 'Details', exact: true }).click()
      await expect(page.getByRole('heading', { name: 'Input', exact: true })).toBeVisible()
      await expect(page.getByRole('heading', { name: 'Output', exact: true })).toBeVisible()
      await expect(page.getByRole('link', { name: 'Open workflow', exact: true })).toHaveAttribute('href', '/workflows/digest')
      await noOverflow(page)
      await page.screenshot({ path: test.info().outputPath('run-details.png') })

      await page.goto('/apps/digest-reader#info')
      await expect(page.getByRole('heading', { name: 'Overview', exact: true })).toBeVisible()
      await expect(page.getByRole('heading', { name: 'Resources', exact: true })).toBeVisible()
      await expect(page.getByRole('heading', { name: 'Releases', exact: true })).toBeVisible()
      await expect(page.getByRole('button', { name: 'Open app', exact: true })).toBeVisible()
      await noOverflow(page)
      await page.screenshot({ path: test.info().outputPath('app-details.png') })
      expect(errors).toEqual([])
    })
  }
}
