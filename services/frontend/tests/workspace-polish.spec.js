import { test, expect } from '@playwright/test'
import { THEMES, contrastRatio, resolveTheme } from '../src/themes.js'
import { PREFERENCES_KEY } from '../src/preferences-schema.js'
import { mockAgents } from './agent-fixture.js'
import { mockDesign } from './design-fixture.js'

const rgb = hex => `rgb(${[1, 3, 5].map(index => parseInt(hex.slice(index, index + 2), 16)).join(', ')})`

for (const theme of THEMES) {
  for (const width of [1440, 320]) {
    test(`${theme.name} keeps chat search distinct without a repeated brand header at ${width}px`, async ({ page, context }) => {
      await context.addInitScript(({ key, theme }) => localStorage.setItem(key, JSON.stringify({ theme })), { key: PREFERENCES_KEY, theme: theme.id })
      await mockDesign(context)
      await page.setViewportSize({ width, height: 900 })
      await page.goto('/chats')
      await expect(page.locator('.side__title')).toHaveText('Chats')
      await expect(page.locator('.side__identity')).toHaveCount(0)
      await expect(page.locator('.side__head .brand-mark')).toHaveCount(0)
      const tokens = resolveTheme(theme.id)
      const field = page.locator('.side__search')
      await expect(field).toHaveCSS('background-color', rgb(tokens['surface-raised']))
      await expect(field).toHaveCSS('border-top-style', 'solid')
      expect(tokens['surface-raised']).not.toBe(tokens['surface-panel'])
      expect(contrastRatio(tokens['text-muted'], tokens['surface-raised'])).toBeGreaterThanOrEqual(4.5)
      const query = page.getByRole('textbox', { name: 'Search chats', exact: true })
      await query.fill('Design')
      await expect(page.locator('.side__list')).toContainText('Design review')
      await expect(field).toHaveCSS('border-top-color', rgb(tokens.accent))
      expect(await page.locator('.side__list').evaluate(element => element.scrollWidth <= element.clientWidth)).toBe(true)
    })
  }
}

for (const width of [1440, 320]) {
  test(`subagent choices have full-size label targets and preserve the allowlist at ${width}px`, async ({ page, context }) => {
    await context.addInitScript(key => localStorage.setItem(key, JSON.stringify({ interfaceSize: 150 })), PREFERENCES_KEY)
    const state = await mockAgents(context)
    const lead = state.addAgent('Lead')
    const target = state.addAgent('Research for international product and policy teams')
    state.addAgent('Coding')
    await page.setViewportSize({ width, height: 900 })
    await page.goto(`/settings/agents?edit=${lead.id}#agent-profiles`)
    const group = page.getByRole('group', { name: 'Allowed subagents', exact: true })
    await expect(group).toBeVisible()
    await expect(group).toHaveCSS('border-top-width', '0px')
    const check = group.getByRole('checkbox', { name: target.name, exact: true })
    await expect(check).not.toBeChecked()
    const row = check.locator('..')
    expect((await row.boundingBox()).height).toBeGreaterThanOrEqual(44)
    await row.click()
    await expect(check).toBeChecked()
    await expect(row).toHaveClass(/profile-subagents__option--selected/)
    await check.focus()
    await check.press('Space')
    await expect(check).not.toBeChecked()
    await check.press('Space')
    await page.getByRole('button', { name: 'Save agent', exact: true }).click()
    await expect.poll(() => lead.allowed_subagents).toEqual([target.id])
    expect(state.agentWrites.at(-1).allowed_subagents).toEqual([target.id])
    expect(await page.locator('.settings__body').evaluate(element => element.scrollWidth <= element.clientWidth)).toBe(true)
  })
}
