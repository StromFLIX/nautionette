import { test, expect } from '@playwright/test'
import { mockDesign } from './design-fixture.js'
import { SETTINGS_GROUPS, SETTINGS_SECTIONS } from '../src/settings-registry.js'
import { THEMES } from '../src/themes.js'

const groups = [
  ['Your experience', ['general', 'appearance', 'workspace']],
  ['Build & connect', ['agents', 'packages', 'creative-apps', 'mcp', 'projects', 'git']],
  ['Run & manage', ['automation', 'app-runtimes', 'storage', 'system', 'activity']]
]

test('settings registry preserves category routes in the agreed information architecture', () => {
  expect(SETTINGS_GROUPS).toEqual(groups.map(([label]) => label))
  for (const [label, keys] of groups) {
    expect(SETTINGS_SECTIONS.filter(section => section.group === label).map(section => section.key)).toEqual(keys)
  }
})

for (const width of [1440, 320]) {
  test(`settings groups and scope remain legible at ${width}px`, async ({ page, context }, testInfo) => {
    await mockDesign(context)
    await page.setViewportSize({ width, height: width === 320 ? 700 : 1000 })
    await page.goto('/settings/general')
    await expect(page.getByRole('heading', { name: 'General', exact: true })).toBeVisible()
    const scope = page.locator('.settings__scope-line')
    await expect(scope).toContainText('Instance')
    await expect(scope).toContainText('Connection credentials below stay on this device')
    if (width > 760) {
      const nav = page.getByRole('navigation', { name: 'Settings categories' })
      for (const [label, keys] of groups) {
        const group = nav.locator('section').filter({ has: page.getByRole('heading', { name: label, exact: true }) })
        await expect(group.getByRole('heading')).toHaveCSS('text-transform', 'none')
        await expect(group.getByRole('link')).toHaveCount(keys.length)
        for (const key of keys) await expect(group.locator(`a[href="/settings/${key}"]`)).toHaveCount(1)
      }
      await nav.getByRole('link', { name: 'Workspace', exact: true }).click()
    } else {
      await page.getByRole('button', { name: 'Settings category', exact: true }).click()
      const menu = page.locator('.q-menu')
      for (const [label] of groups) await expect(menu.getByText(label, { exact: true })).toBeVisible()
      await menu.getByRole('button', { name: 'Workspace', exact: true }).click()
      await expect(menu).toHaveCount(0)
    }
    await expect(page).toHaveURL('/settings/workspace')
    await expect(scope).toContainText('This device')
    await expect(scope).toContainText('Layout and accessibility preferences apply only to this browser or app')
    expect(await page.locator('.settings__body').evaluate(element => element.scrollWidth <= element.clientWidth)).toBe(true)
    await page.screenshot({ path: testInfo.outputPath(`settings-workspace-${width}.png`) })
  })

  test(`search preserves drafts and reveals connection and technical details at ${width}px`, async ({ page, context }) => {
    const state = await mockDesign(context)
    await page.setViewportSize({ width, height: 900 })
    await page.goto('/settings/general')
    const depth = page.getByLabel('Maximum depth', { exact: true })
    await expect(depth).toBeEnabled()
    await depth.fill('7')
    const search = page.getByRole('searchbox', { name: 'Search settings' })
    await search.fill('access token')
    await expect(page.getByRole('region', { name: 'Settings search results' })).toContainText('This device')
    await search.press('Escape')
    await expect(depth).toHaveValue('7')
    await search.fill('access token')
    await page.getByRole('link', { name: /Access token/ }).click()
    await expect(page).toHaveURL('/settings/general#access-token')
    await expect(page.locator('#access-token')).toBeFocused()
    await expect(page.getByLabel('Access token', { exact: true })).toBeVisible()
    await expect(depth).toHaveValue('7')
    await search.fill('conversation memory')
    await page.getByRole('link', { name: /Conversation memory/ }).click()
    await expect(page.locator('#history')).toHaveAttribute('open', '')
    await expect(page.locator('#history')).toBeFocused()
    expect(state.writes).toEqual([])
  })
}

for (const mode of ['light', 'dark']) {
  test(`settings shell uses semantic tokens in ${mode} themes`, async ({ page, context }, testInfo) => {
    await mockDesign(context)
    const theme = THEMES.find(theme => theme.mode === mode)
    await page.goto('/settings/appearance')
    await page.getByRole('button', { name: `${theme.name} theme`, exact: true }).click()
    await page.goto('/settings/general')
    await expect(page.locator('html')).toHaveCSS('color-scheme', mode)
    const colors = await page.locator('.settings__tree').evaluate(element => ({
      actual: getComputedStyle(element).backgroundColor,
      expected: getComputedStyle(document.documentElement).getPropertyValue('--surface-app').trim()
    }))
    const expected = await page.evaluate(color => {
      const probe = document.createElement('span')
      probe.style.color = color
      document.body.append(probe)
      const result = getComputedStyle(probe).color
      probe.remove()
      return result
    }, colors.expected)
    expect(colors.actual).toBe(expected)
    await expect(page.getByRole('heading', { name: 'General', exact: true })).toBeVisible()
    await page.screenshot({ path: testInfo.outputPath(`settings-general-${mode}.png`) })
  })
}

test.describe('touch settings', () => {
  test.use({ hasTouch: true, isMobile: true, viewport: { width: 390, height: 844 } })
  test('search and connection fields avoid input zoom and category control has a touch target', async ({ page, context }) => {
    await mockDesign(context)
    await page.goto('/settings/general#access-token')
    for (const field of [page.getByRole('searchbox', { name: 'Search settings' }), page.getByLabel('Access token', { exact: true })]) {
      await expect(field).toBeVisible()
      expect(await field.evaluate(element => parseFloat(getComputedStyle(element).fontSize))).toBeGreaterThanOrEqual(16)
    }
    const category = await page.getByRole('button', { name: 'Settings category', exact: true }).boundingBox()
    expect(category.height).toBeGreaterThanOrEqual(44)
    expect(await page.locator('.settings__body').evaluate(element => element.scrollWidth <= element.clientWidth)).toBe(true)
  })
})
