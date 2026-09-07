import { test, expect } from '@playwright/test'

const defaults = {
  default_model: '', default_agent_set: 'default', history_chars: 0,
  git_authorship_mode: 'automation', git_human_name: '', git_human_email: '',
  git_automation_name: 'Nautionette', git_automation_email: 'nautionette@users.noreply.github.com'
}

async function mockSettings (context) {
  const state = { settings: { ...defaults }, writes: [], fail: false }
  await context.route('**/api/**', route => {
    const path = new URL(route.request().url()).pathname
    if (path === '/api/settings') {
      if (route.request().method() === 'PUT') {
        const payload = route.request().postDataJSON()
        state.writes.push(payload)
        if (state.fail) return route.fulfill({ status: 422, json: { detail: 'Git human name and email are required' } })
        for (const [key, value] of Object.entries(payload)) state.settings[key] = value ?? defaults[key]
      }
      return route.fulfill({ json: { defaults, settings: state.settings } })
    }
    if (path === '/api/events') return route.fulfill({ contentType: 'text/event-stream', body: ': connected\n\n' })
    if (path === '/api/system') return route.fulfill({ json: { version: 'test', components: [] } })
    const key = path.split('/').pop()
    return route.fulfill({ json: key === 'catalog' ? { models: [], tools: [], agent_sets: [{ name: 'default' }] } : { [key]: [] } })
  })
  return state
}

for (const width of [1440, 320]) {
  test(`authorship options save, reload and reset at ${width}px`, async ({ page, context }) => {
    const state = await mockSettings(context)
    const errors = []
    page.on('pageerror', error => errors.push(error.message))
    await page.setViewportSize({ width, height: 1000 })
    await page.goto('/settings/git')
    await expect(page.getByRole('heading', { name: 'Git authorship' })).toBeVisible()
    const mode = page.getByLabel('Attribution', { exact: true })
    await expect(mode.locator('option')).toHaveCount(4)
    await expect(mode).toHaveValue('automation')
    await mode.selectOption('human_author_bot_coauthor')
    await expect(page.getByLabel('Your name', { exact: true })).toHaveAttribute('required', '')
    await page.getByLabel('Your name', { exact: true }).fill('Jane Contributor')
    await page.getByLabel('Your Git email', { exact: true }).fill('jane@example.test')
    await expect(page.locator('.git-preview')).toContainText('Author: Jane Contributor <jane@example.test>')
    await expect(page.locator('.git-preview')).toContainText('Co-authored-by: Nautionette')
    await page.getByRole('button', { name: 'Save', exact: true }).click()
    await expect.poll(() => state.settings.git_authorship_mode).toBe('human_author_bot_coauthor')
    expect(Object.keys(state.writes[0]).every(key => key.startsWith('git_'))).toBe(true)
    await page.reload()
    await expect(mode).toHaveValue('human_author_bot_coauthor')
    await mode.selectOption('bot_author_human_coauthor')
    await expect(page.locator('.git-preview')).toContainText('Co-authored-by: Jane Contributor <jane@example.test>')
    await mode.selectOption('human_author')
    await expect(page.locator('.git-preview')).not.toContainText('Co-authored-by:')
    expect(await page.locator('form').evaluate(element => element.getBoundingClientRect().right <= innerWidth)).toBe(true)
    await page.getByRole('button', { name: 'Reset', exact: true }).click()
    await expect(mode).toHaveValue('automation')
    await expect(page.getByLabel('Your name', { exact: true })).toHaveValue('')
    expect(errors).toEqual([])
  })
}

test('failed save keeps inputs for correction and General never resaves Git settings', async ({ page, context }) => {
  const state = await mockSettings(context)
  await page.goto('/settings/git')
  await page.getByLabel('Your name', { exact: true }).fill('Jane')
  state.fail = true
  await page.getByRole('button', { name: 'Save', exact: true }).click()
  await expect(page.getByText('Git human name and email are required', { exact: true })).toBeVisible()
  await expect(page.getByLabel('Your name', { exact: true })).toHaveValue('Jane')
  await expect(page.getByRole('button', { name: 'Save', exact: true })).toBeEnabled()
  state.fail = false
  await page.goto('/settings/general')
  await expect(page.locator('select').first().locator('option')).toHaveCount(1)
  await page.locator('.settings__save').getByRole('button', { name: 'Save', exact: true }).click()
  await expect.poll(() => state.writes.length).toBe(2)
  expect(state.writes[1]).toEqual({ default_model: '', default_agent_set: 'default', history_chars: 0 })
})
