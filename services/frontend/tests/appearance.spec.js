import { test, expect } from '@playwright/test'
import { readFile } from 'node:fs/promises'
import { THEMES, resolveTheme } from '../src/themes.js'
import { PREFERENCES_KEY } from '../src/preferences-schema.js'
import { mockDesign } from './design-fixture.js'

const rgb = hex => `rgb(${[1, 3, 5].map(index => parseInt(hex.slice(index, index + 2), 16)).join(', ')})`
const stored = page => page.evaluate(key => JSON.parse(localStorage.getItem(key)), PREFERENCES_KEY)

for (const theme of THEMES) {
  test(`${theme.name} updates the entire workspace and survives reload`, async ({ page, context }) => {
    const state = await mockDesign(context)
    const errors = []
    page.on('pageerror', error => errors.push(error.message))
    await page.goto('/settings/appearance')
    await page.getByRole('button', { name: `${theme.name} theme`, exact: true }).click()
    await expect(page.locator('html')).toHaveAttribute('data-theme', theme.id)
    await expect(page.locator('html')).toHaveCSS('color-scheme', theme.mode)
    await page.reload()
    await expect(page.getByRole('button', { name: `${theme.name} theme`, exact: true })).toHaveAttribute('aria-pressed', 'true')
    await page.goto('/chats/alpha')
    const tokens = resolveTheme(theme.id)
    await expect(page.locator('body')).toHaveCSS('color', rgb(tokens.text))
    await expect(page.locator('.shell__main')).toHaveCSS('background-color', rgb(tokens['surface-app']))
    await expect(page.locator('.msg--user .bubble')).toHaveCSS('background-color', rgb(tokens['bubble-out']))
    await expect(page.locator('.msg--user .bubble')).toHaveCSS('color', rgb(tokens['bubble-text']))
    await expect(page.locator('.code-block')).toHaveCSS('background-color', rgb(tokens['surface-code']))
    await expect(page.locator('.code-block .hljs-keyword')).toHaveCSS('color', rgb(tokens['syntax-keyword']))
    await page.getByRole('button', { name: 'Select model', exact: true }).click()
    await expect(page.getByPlaceholder('Search models')).toBeVisible()
    await expect(page.locator('.q-menu')).toHaveCSS('background-color', rgb(tokens['surface-overlay']))
    await page.keyboard.press('Escape')
    await expect(page.locator('.q-menu')).toHaveCount(0)
    await expect(page.getByRole('button', { name: 'Select model', exact: true })).toBeFocused()
    // Escape restores keyboard focus, which can show the model button's tooltip.
    // Leave both hover and focus before asserting an unobstructed screenshot.
    await page.mouse.move(0, 0)
    await page.getByRole('textbox', { name: 'Message', exact: true }).focus()
    await expect(page.getByRole('tooltip')).toHaveCount(0)
    await page.screenshot({ path: `/tmp/nautionette-theme-${theme.id}-chat.png` })
    expect(state.writes).toEqual([])
    expect(errors).toEqual([])
  })
}

for (const width of [1440, 901, 320]) {
  test(`all settings categories remain navigable at ${width}px`, async ({ page, context }) => {
    await mockDesign(context)
    const errors = []
    page.on('pageerror', error => errors.push(error.message))
    await page.setViewportSize({ width, height: width === 320 ? 568 : 1000 })
    await page.goto('/settings/general')
    const categories = [
      ['Appearance', 'Appearance'], ['Workspace', 'Workspace'], ['Agents & models', 'Agents & models'],
      ['Extension library', 'Extension library'], ['MCP servers', 'MCP servers'], ['Projects', 'Projects'], ['Git authorship', 'Git authorship'],
      ['Automation', 'Automation'], ['System', 'System health'], ['Activity', 'Activity'], ['General', 'General']
    ]
    for (const [name, heading] of categories) {
      if (width <= 760) {
        const category = page.getByRole('button', { name: 'Settings category', exact: true })
        await category.click()
        await expect(category).toHaveAttribute('aria-expanded', 'true')
        await page.locator('.q-menu').getByRole('button', { name, exact: true }).click()
        await expect(page.locator('.q-menu')).toHaveCount(0)
        await expect(category).toContainText(name)
        await expect(category).toHaveAttribute('aria-expanded', 'false')
      } else await page.getByRole('navigation', { name: 'Settings categories' }).getByRole('link', { name, exact: true }).click()
      await expect(page.getByRole('heading', { name: heading, exact: true })).toBeVisible()
      expect(await page.locator('.settings__body').evaluate(element => element.scrollWidth <= element.clientWidth)).toBe(true)
    }
    await expect(page.getByRole('navigation', { name: 'Settings categories' })).toBeVisible({ visible: width > 760 })
    await page.getByRole('button', { name: 'Back to workspace', exact: true }).click()
    await expect(page).toHaveURL(/\/chats$/)
    expect(errors).toEqual([])
  })
}

for (const width of [1440, 320]) {
  test(`settings search reveals nested controls without losing unsaved values at ${width}px`, async ({ page, context }) => {
    const state = await mockDesign(context)
    await page.setViewportSize({ width, height: 900 })
    await page.goto('/settings/general')
    const agentSet = page.getByLabel('Default agent set', { exact: true })
    await agentSet.click()
    await page.locator('.q-menu').getByRole('button', { name: 'research', exact: true }).click()
    await expect(page.locator('.q-menu')).toHaveCount(0)
    const search = page.getByRole('searchbox', { name: 'Search settings' })
    await search.fill('transcript')
    await expect(page.getByRole('region', { name: 'Settings search results' })).toContainText('History budget')
    await search.press('Escape')
    await expect(agentSet).toContainText('research')
    await page.locator('h1').click()
    await page.keyboard.press('/')
    await expect(search).toBeFocused()
    await search.fill('code font')
    await page.getByRole('link', { name: /Code font/ }).click()
    await expect(page.getByLabel('Code font', { exact: true })).toBeVisible()
    await expect(page.locator('#token-font-mono')).toBeFocused()
    await expect(page).toHaveURL(/\/settings\/appearance#token-font-mono$/)
    await page.reload()
    await expect(page.getByLabel('Code font', { exact: true })).toBeVisible()
    await page.getByRole('searchbox', { name: 'Filter theme tokens' }).fill('font')
    await search.fill('canvas')
    await page.getByRole('link', { name: /Canvas/ }).click()
    await expect(page.getByLabel('Canvas', { exact: true })).toBeVisible()
    await expect(page.getByRole('searchbox', { name: 'Filter theme tokens' })).toHaveValue('')
    await search.fill('no-such-setting-xyz')
    await expect(page.getByText(/No settings match/)).toBeVisible()
    expect(state.writes).toEqual([])
  })
}

test('theme editor validates, resets and keeps each preset customization separate', async ({ page, context }) => {
  const state = await mockDesign(context)
  await page.goto('/settings/appearance#token-accent')
  const accent = page.getByLabel('Accent', { exact: true })
  await expect(accent).toBeVisible()
  await accent.fill('#aa4400')
  await accent.press('Tab')
  await expect(page.locator('html')).toHaveCSS('--accent', '#aa4400')
  await accent.fill('url(https://example.test/tracker)')
  await accent.press('Tab')
  await expect(accent).toHaveAttribute('aria-invalid', 'true')
  await expect(page.locator('html')).toHaveCSS('--accent', '#aa4400')
  await page.getByRole('button', { name: 'Reset Accent', exact: true }).click()
  await expect(accent).toHaveValue('#79dfc5')
  await expect(accent).toHaveAttribute('aria-invalid', 'false')
  await accent.fill('#551166')
  await accent.press('Tab')
  await page.getByRole('button', { name: 'Daylight theme', exact: true }).click()
  await expect(page.locator('html')).toHaveCSS('--accent', '#255acb')
  await page.getByRole('button', { name: 'Orbit theme', exact: true }).click()
  await expect(page.locator('html')).toHaveCSS('--accent', '#551166')
  await page.getByRole('button', { name: 'Reset theme', exact: true }).click()
  await expect(page.locator('html')).toHaveCSS('--accent', '#79dfc5')
  await page.getByRole('button', { name: 'Undo reset', exact: true }).click()
  await expect(page.locator('html')).toHaveCSS('--accent', '#551166')
  expect((await stored(page)).overrides.orbit.accent).toBe('#551166')
  expect(state.writes).toEqual([])
})

test('theme files import atomically, export only styling and support transparent-color warnings', async ({ page, context }) => {
  await mockDesign(context)
  await page.goto('/settings/appearance')
  const file = page.locator('#theme-transfer input[type=file]')
  const imported = { version: 1, theme: 'nebula', overrides: { accent: '#663399', 'chat-font-size': 18, font: 'monospace' } }
  await file.setInputFiles({ name: 'theme.json', mimeType: 'application/json', buffer: Buffer.from(JSON.stringify(imported)) })
  await expect(page.getByText('Theme imported.', { exact: true })).toBeVisible()
  await expect(page.locator('html')).toHaveCSS('--chat-font-size', '18px')
  const before = await stored(page)
  await file.setInputFiles({ name: 'unsafe.json', mimeType: 'application/json', buffer: Buffer.from(JSON.stringify({ ...imported, overrides: { accent: '#111111', font: 'serif; opacity:0' } })) })
  await expect(page.getByRole('alert')).toContainText('Invalid theme token')
  expect(await stored(page)).toEqual(before)
  await file.setInputFiles({ name: 'too-big.json', mimeType: 'application/json', buffer: Buffer.from(' '.repeat(64001)) })
  await expect(page.getByRole('alert')).toContainText('64 KB')
  expect(await stored(page)).toEqual(before)
  const downloaded = page.waitForEvent('download')
  await page.getByRole('button', { name: 'Export theme', exact: true }).click()
  const download = await downloaded
  expect(JSON.parse(await readFile(await download.path(), 'utf8'))).toEqual(imported)
  await file.setInputFiles({ name: 'transparent.json', mimeType: 'application/json', buffer: Buffer.from(JSON.stringify({ version: 1, theme: 'orbit', overrides: { text: '#ffffff00' } })) })
  await expect(page.getByRole('status').filter({ hasText: 'Text contrast is below 4.5:1.' })).toBeVisible()
})

test('workspace controls change layout, composer behavior, code and graph direction', async ({ page, context }) => {
  const state = await mockDesign(context)
  await page.goto('/settings/workspace')
  await page.getByLabel('Density', { exact: true }).selectOption('compact')
  await page.getByLabel('Sidebar width', { exact: true }).fill('400')
  await page.getByLabel('Sidebar width', { exact: true }).press('Tab')
  await page.getByRole('switch', { name: 'Show chat configuration', exact: true }).check()
  await page.getByRole('switch', { name: 'Message timestamps', exact: true }).uncheck()
  await page.getByRole('switch', { name: 'Starter prompts', exact: true }).uncheck()
  await page.getByLabel('Send shortcut', { exact: true }).selectOption('modifier-enter')
  await page.getByLabel('Motion', { exact: true }).selectOption('reduced')
  await page.getByLabel('Flow direction', { exact: true }).selectOption('LR')
  await page.getByRole('switch', { name: 'Wrap source code', exact: true }).check()
  await page.goto('/chats')
  await expect(page.getByRole('button', { name: 'Daily briefing', exact: true })).toHaveCount(0)
  await page.goto('/chats/alpha')
  await expect(page.locator('.shell__side')).toHaveCSS('width', '400px')
  await expect(page.getByRole('button', { name: 'Chat configuration', exact: true })).toHaveAttribute('aria-expanded', 'true')
  await expect(page.getByRole('button', { name: 'Select projects', exact: true })).toBeVisible()
  await expect(page.locator('.msg__time')).toHaveCount(0)
  const input = page.getByRole('textbox', { name: 'Message', exact: true })
  await input.fill('First line')
  await input.press('Enter')
  expect(state.sent).toHaveLength(0)
  await expect(input).toHaveValue('First line\n')
  await input.press('Shift+Enter')
  await expect(input).toHaveValue('First line\n\n')
  await input.dispatchEvent('keydown', { key: 'Enter', ctrlKey: true, isComposing: true })
  expect(state.sent).toHaveLength(0)
  await input.press('Control+Enter')
  await expect.poll(() => state.sent.length).toBe(1)
  expect(state.sent[0].text).toBe('First line')
  const separator = page.getByRole('separator', { name: 'Resize sidebar', exact: true })
  await separator.press('ArrowRight')
  await expect(page.locator('.shell__side')).toHaveCSS('width', '410px')
  await separator.press('Home')
  await expect(page.locator('.shell__side')).toHaveCSS('width', '320px')
  await page.goto('/workflows/digest?tab=Code')
  await expect(page.locator('.viewer__text').first()).toHaveCSS('white-space', 'pre-wrap')
  await page.getByRole('button', { name: 'Flow', exact: true }).click()
  await expect(page.locator('.flow-node')).toHaveCount(3)
  await page.getByRole('button', { name: 'Fit all steps', exact: true }).click()
  const positions = await page.locator('.flow-node').evaluateAll(nodes => nodes.map(node => {
    const { x, y, width, height } = node.getBoundingClientRect()
    return { x: x + width / 2, y: y + height / 2 }
  }))
  expect(positions[1].x).toBeGreaterThan(positions[0].x)
  expect(Math.abs(positions[1].y - positions[0].y)).toBeLessThan(1)
  state.data.active_turn = { id: 'live', steps: [{ kind: 'text', text: 'Working' }] }
  await page.goto('/chats/alpha')
  await expect(page.locator('.composer')).toHaveClass(/composer--running/)
  expect(await page.locator('.composer').evaluate(element => getComputedStyle(element, '::before').animationName)).toBe('none')
  expect(state.writes).toEqual([])
})

test('preferences migrate legacy values and synchronize between tabs', async ({ page, context }) => {
  await mockDesign(context)
  await context.addInitScript(() => {
    if (!localStorage.getItem('nautionette.preferences.v1')) {
      localStorage.setItem('nautionette.sideWidth', '420')
      localStorage.setItem('nautionette.chatGroupBy', 'model')
      localStorage.setItem('nautionette.chatActiveMinutes', '1440')
    }
  })
  await page.goto('/settings/workspace')
  await expect(page.getByLabel('Sidebar width', { exact: true })).toHaveValue('420')
  await expect(page.getByLabel('Group chats by', { exact: true })).toHaveValue('model')
  await expect(page.getByLabel('Activity window', { exact: true })).toHaveValue('1440')
  const second = await context.newPage()
  await second.goto('/settings/appearance')
  await page.goto('/settings/appearance')
  await page.getByRole('button', { name: 'Sand theme', exact: true }).click()
  await expect(second.locator('html')).toHaveAttribute('data-theme', 'sand')
  await second.getByRole('button', { name: 'Nebula theme', exact: true }).click()
  await expect(page.locator('html')).toHaveAttribute('data-theme', 'nebula')
  await page.reload()
  await expect(page.locator('html')).toHaveAttribute('data-theme', 'nebula')
  await second.close()
})

test('unavailable preference storage does not break live customization or claim it was saved', async ({ page, context }) => {
  await mockDesign(context)
  await context.addInitScript(key => {
    const save = Storage.prototype.setItem
    Storage.prototype.setItem = function (name, value) {
      if (name === key) throw new DOMException('Storage unavailable', 'QuotaExceededError')
      return save.call(this, name, value)
    }
  }, PREFERENCES_KEY)
  await page.goto('/settings/appearance')
  await page.getByRole('button', { name: 'Sand theme', exact: true }).click()
  await expect(page.locator('html')).toHaveAttribute('data-theme', 'sand')
  await expect(page.getByRole('alert')).toContainText('this session only')
  await page.reload()
  await expect(page.locator('html')).toHaveAttribute('data-theme', 'orbit')
})

test('automation settings link to the actual workflow controls, including after reload', async ({ page, context }) => {
  await mockDesign(context)
  await page.goto('/settings/automation')
  await page.getByRole('link', { name: /Morning digest/ }).click()
  await expect(page).toHaveURL(/\/workflows\/digest\?tab=Run$/)
  await expect(page.getByRole('button', { name: 'Run now', exact: true })).toBeVisible()
  await expect(page.getByRole('button', { name: 'Save schedule', exact: true })).toBeVisible()
  await page.reload()
  await expect(page.getByRole('button', { name: 'Save schedule', exact: true })).toBeVisible()
})
