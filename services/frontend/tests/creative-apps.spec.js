import { test, expect } from '@playwright/test'
import { mockAgents } from './agent-fixture.js'

const names = ['PhotoCraft', 'VectorCraft', 'FilmCraft', 'LightCraft', 'PdfCraft', 'EffectCraft', 'DesignCraft', 'FigCraft', 'WordCraft', 'SoundCraft', 'DeckCraft', 'CADCraft', 'GridCraft']
async function mockApps (context) {
  await mockAgents(context)
  const state = { writes: [], actions: [], fail: false, outage: false, releaseOutage: false, updates: { automatic: false, checking: false, checked_at: Date.now() / 1000, revision: 0 }, apps: names.map(name => ({
    id: name.toLowerCase(), name, description: `Native ${name} headless tools`, repository: `https://github.com/storytold/${name.toLowerCase()}`,
    version: '0.8.0', bundled_version: '0.8.0', bundled: true, enabled: true, revision: 0, status: 'ready', pending_version: null, error: '',
    latest_version: '1.0.0', update_available: true, checked_at: Date.now() / 1000, update_error: ''
  })) }
  state.finish = (id, error = '') => {
    const app = state.apps.find(app => app.id === id)
    if (!error) { app.version = app.pending_version; app.bundled = false; app.update_available = app.version !== app.latest_version }
    Object.assign(app, { pending_version: null, status: 'ready', error, revision: app.revision + 1 })
  }
  const response = () => ({ apps: state.apps, updates: state.updates, architecture: 'x86_64' })
  await context.route('**/api/artcraft/**', async route => {
    const request = route.request(), path = new URL(request.url()).pathname
    if (path === '/api/artcraft/apps') return state.outage ? route.fulfill({ status: 503, json: { detail: 'Apps unavailable' } }) : route.fulfill({ json: response() })
    if (path.startsWith('/api/artcraft/updates')) {
      state.actions.push({ path, body: request.postDataJSON() })
      if (path.endsWith('/check')) {
        state.updates.checked_at = Date.now() / 1000
        for (const app of state.apps) app.update_error = state.releaseOutage ? 'Could not check releases; upstream rate-limited. Retry later.' : ''
      } else if (path.endsWith('/apply')) {
        const queued = state.apps.filter(app => app.update_available && !app.update_error && app.status === 'ready')
        queued.forEach((app, index) => Object.assign(app, { status: index < 2 ? 'installing' : 'queued', pending_version: app.latest_version, revision: app.revision + 1, error: '' }))
        return route.fulfill({ json: { ...response(), queued: queued.map(app => app.id) } })
      } else {
        const body = request.postDataJSON()
        if (state.fail || body.expected_revision !== state.updates.revision) return route.fulfill({ status: 409, json: { detail: 'Update preferences changed; reload before saving' } })
        state.updates.automatic = body.automatic; state.updates.revision++
      }
      return route.fulfill({ json: response() })
    }
    const id = path.split('/')[4], app = state.apps.find(item => item.id === id)
    if (path.endsWith('/releases')) return state.releaseOutage ? route.fulfill({ status: 502, json: { detail: 'Release lookup rate limited; retry later' } }) : route.fulfill({ json: { versions: ['1.0.0', '0.8.0'] } })
    const body = request.postDataJSON(); state.writes.push({ id, ...body })
    if (state.fail) return route.fulfill({ status: 422, json: { detail: 'Release failed validation' } })
    if (body.expected_revision !== app.revision) return route.fulfill({ status: 409, json: { detail: 'App settings changed; reload before saving' } })
    if ('enabled' in body) app.enabled = body.enabled
    if (body.version === 'bundled') Object.assign(app, { version: app.bundled_version, bundled: true, error: '', update_available: true })
    else if (body.version) Object.assign(app, { pending_version: body.version, status: 'queued', error: '' })
    app.revision += 1
    return route.fulfill({ json: app })
  })
  return state
}

for (const width of [1440, 320]) {
  test(`outdated apps are visible immediately and one-click updates/default restore work at ${width}px`, async ({ page, context }) => {
    const state = await mockApps(context)
    await page.setViewportSize({ width, height: 1000 })
    await page.goto('/settings/creative-apps')
    await expect(page.getByRole('heading', { name: '13 app updates available', exact: true })).toBeVisible()
    await expect(page.getByRole('article')).toHaveCount(13)
    await page.screenshot({ path: test.info().outputPath(`creative-apps-${width}.png`) })
    const app = page.getByRole('article', { name: 'CADCraft', exact: true })
    await expect(app).toContainText('Latest stable: 1.0.0')
    await expect(app.getByLabel('Exact version', { exact: true })).not.toBeVisible()
    await app.getByRole('checkbox', { name: 'Enable CADCraft', exact: true }).uncheck()
    await expect(app.getByRole('checkbox')).not.toBeChecked()
    expect(state.writes[0]).toEqual({ id: 'cadcraft', enabled: false, expected_revision: 0 })
    await app.getByRole('button', { name: 'Update to 1.0.0', exact: true }).click()
    await expect(app.getByRole('status')).toContainText('Queued 1.0.0')
    await expect(app).toContainText('Active: 0.8.0')
    state.finish('cadcraft')
    await expect(app).toContainText('Active: 1.0.0')
    await expect(app).toContainText('Up to date')
    await app.locator('summary').click()
    await app.getByRole('button', { name: 'Restore default 0.8.0', exact: true }).click()
    await expect(app).toContainText('Active: 0.8.0')
    expect(state.writes.at(-1)).toEqual({ id: 'cadcraft', version: 'bundled', expected_revision: 3 })
    expect(await page.locator('.settings__body').evaluate(element => element.scrollWidth <= element.clientWidth)).toBe(true)
  })

  test(`update all queues every app, preserves disabled apps and reports completion at ${width}px`, async ({ page, context }) => {
    const state = await mockApps(context)
    state.apps.find(app => app.id === 'cadcraft').enabled = false
    await page.setViewportSize({ width, height: 1000 })
    await page.goto('/settings/creative-apps')
    await page.getByRole('button', { name: 'Update all (13)', exact: true }).click()
    await expect(page.getByRole('region', { name: 'App updates' })).toContainText('13 app updates queued')
    await expect(page.getByRole('article', { name: 'CADCraft', exact: true })).toContainText('Queued 1.0.0')
    expect(state.apps.filter(app => app.status === 'installing')).toHaveLength(2)
    for (const app of state.apps) state.finish(app.id)
    await expect(page.getByRole('heading', { name: 'Your apps are up to date', exact: true })).toBeVisible()
    await expect(page.getByRole('button', { name: 'Update all', exact: true })).toBeDisabled()
    await expect(page.getByRole('checkbox', { name: 'Enable CADCraft', exact: true })).not.toBeChecked()
    expect(state.actions.filter(item => item.path.endsWith('/apply'))).toHaveLength(1)
    expect(await page.locator('.settings__body').evaluate(element => element.scrollWidth <= element.clientWidth)).toBe(true)
  })
}

test('failed verification preserves active version and offers a one-click retry', async ({ page, context }) => {
  const state = await mockApps(context)
  await page.goto('/settings/creative-apps')
  const app = page.getByRole('article', { name: 'CADCraft', exact: true })
  await app.getByRole('button', { name: 'Update to 1.0.0', exact: true }).click()
  await expect(app.getByRole('status')).toBeVisible()
  state.finish('cadcraft', 'Update failed verification. The previous version is unchanged.')
  await expect(app.getByRole('alert')).toContainText('previous version is unchanged')
  await expect(app).toContainText('Active: 0.8.0')
  await app.getByRole('button', { name: 'Update to 1.0.0', exact: true }).click()
  state.finish('cadcraft')
  await expect(app).toContainText('Active: 1.0.0')
  await expect(app.getByRole('alert')).toHaveCount(0)
})

test('automatic updates are opt-in, revision guarded and survive reload', async ({ page, context }) => {
  const state = await mockApps(context)
  await page.goto('/settings/creative-apps')
  const automatic = page.getByRole('checkbox', { name: 'Automatically update all apps' })
  await expect(automatic).not.toBeChecked()
  await automatic.check()
  await expect(automatic).toBeChecked()
  expect(state.actions.at(-1).body).toEqual({ automatic: true, expected_revision: 0 })
  await page.reload()
  await expect(automatic).toBeChecked()
  await automatic.uncheck()
  expect(state.actions.at(-1).body).toEqual({ automatic: false, expected_revision: 1 })
  state.fail = true
  await automatic.check()
  await expect(page.getByRole('alert')).toContainText('Update preferences changed')
  await expect(automatic).not.toBeChecked()
})

test('unknown discovery is never shown as up to date and automatically checks on entry', async ({ page, context }) => {
  const state = await mockApps(context)
  state.updates.checked_at = null
  for (const app of state.apps) { app.latest_version = null; app.update_available = false }
  state.releaseOutage = true
  await page.goto('/settings/creative-apps')
  await expect(page.getByRole('heading', { name: 'Release status not yet known', exact: true })).toBeVisible()
  await expect(page.getByRole('article', { name: 'CADCraft', exact: true })).toContainText('Check failed')
  expect(state.actions.some(item => item.path.endsWith('/check'))).toBe(true)
  await expect(page.getByRole('button', { name: 'Update all', exact: true })).toBeDisabled()
})

test('load/release outages are retryable, attention filtering works and rejected saves retain drafts', async ({ page, context }) => {
  const state = await mockApps(context)
  state.outage = true
  await page.goto('/settings/creative-apps')
  await expect(page.getByRole('alert')).toContainText('Apps unavailable')
  state.outage = false
  await page.getByRole('button', { name: 'Retry loading', exact: true }).click()
  const app = page.getByRole('article', { name: 'CADCraft', exact: true })
  state.releaseOutage = true
  await page.getByRole('button', { name: 'Check for updates', exact: true }).click()
  await expect(app.getByRole('alert')).toContainText('rate-limited')
  await page.getByLabel('Show', { exact: true }).selectOption('attention')
  await expect(page.getByRole('article')).toHaveCount(13)
  state.releaseOutage = false
  await page.getByRole('button', { name: 'Check for updates', exact: true }).click()
  await expect(page.getByRole('article')).toHaveCount(0)
  await page.getByLabel('Show', { exact: true }).selectOption('all')
  await app.locator('summary').click()
  await app.getByRole('button', { name: 'List versions', exact: true }).click()
  await expect(app).toContainText('Available: 1.0.0')
  state.fail = true
  await app.getByLabel('Exact version', { exact: true }).fill('1.0.0')
  await app.getByRole('button', { name: 'Apply version', exact: true }).click()
  await expect(app.getByRole('alert')).toContainText('Release failed validation')
  await expect(app.getByLabel('Exact version', { exact: true })).toHaveValue('1.0.0')
  await expect(app).toContainText('Active: 0.8.0')
})

test('settings search discovers native apps without visiting the pane first', async ({ page, context }) => {
  await mockApps(context)
  await page.goto('/settings/general')
  await page.getByRole('searchbox', { name: 'Search settings', exact: true }).fill('CADCraft')
  await page.getByRole('link').filter({ hasText: 'ArtCraft applications' }).click()
  await expect(page).toHaveURL(/\/settings\/creative-apps#creative-apps/)
  await expect(page.getByRole('article', { name: 'CADCraft', exact: true })).toBeVisible()
})
