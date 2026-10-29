import { test, expect } from '@playwright/test'
import { mockAgents } from './agent-fixture.js'
import { openChatConfiguration, closeChatConfiguration } from './helpers.js'
import { PREFERENCES_KEY } from '../src/preferences-schema.js'

for (const path of ['/chats', '/chats/alpha']) {
  test(`preferences replace the composer and preserve its draft at ${path}`, async ({ page, context }) => {
    await mockAgents(context)
    await page.goto(path)
    const input = page.getByRole('textbox', { name: 'Message', exact: true })
    const draft = 'Keep this draft\nand its height'
    await input.fill(draft)
    await page.locator('input[type=file]').setInputFiles({ name: 'notes.txt', mimeType: 'text/plain', buffer: Buffer.from('Keep this attachment') })
    const height = (await input.boundingBox()).height
    const composerBox = await page.locator('.composer').boundingBox()
    await openChatConfiguration(page)
    const panel = page.getByRole('region', { name: 'Chat preferences' })
    await expect(input).toBeHidden()
    await expect(page.getByRole('button', { name: 'Attach files', exact: true })).toBeHidden()
    await expect(page.getByRole('button', { name: 'Send message', exact: true })).toBeHidden()
    for (const name of ['Select agent', 'Select model', 'Select agent set', 'Reasoning effort', 'Select tools', 'Select projects', 'Select extensions']) {
      await expect(panel.getByRole('button', { name, exact: true })).toBeVisible()
    }
    await expect(page.locator('.composer')).toBeVisible()
    await expect(page.locator('.composer > .composer__configuration')).toBeVisible()
    await expect(page.getByRole('dialog')).toHaveCount(0)
    await expect(page.locator('.q-dialog__backdrop')).toHaveCount(0)
    const settingsBox = await page.locator('.composer').boundingBox()
    expect(settingsBox.x).toBeCloseTo(composerBox.x, 1)
    expect(settingsBox.width).toBeCloseTo(composerBox.width, 1)
    if (path === '/chats') expect(settingsBox.y).toBeCloseTo(composerBox.y, 1)
    else expect(settingsBox.y + settingsBox.height).toBeCloseTo(composerBox.y + composerBox.height, 1)
    const close = panel.getByRole('button', { name: 'Close chat preferences' })
    await expect(close).toBeFocused()
    const header = await page.locator('.composer__configuration-head').boundingBox()
    const closeBox = await close.boundingBox()
    expect(closeBox.x).toBeGreaterThan(header.x + header.width / 2)
    if (path === '/chats/alpha') {
      // The rest of the chat remains usable: this is not a modal or a focus trap.
      const options = page.getByRole('button', { name: 'Chat options', exact: true })
      await options.click()
      await expect(page.getByRole('button', { name: /Rename$/ })).toBeVisible()
      await page.keyboard.press('Escape')
      await expect(page.locator('.q-menu')).toHaveCount(0)
      // Quasar restores anchor focus after the menu's exit transition.
      await expect(options).toBeFocused()
      await expect(panel).toBeVisible()
    }
    await page.screenshot({ path: `/tmp/chat-preferences-${path === '/chats' ? 'welcome' : 'chat'}.png` })
    await closeChatConfiguration(page)
    await expect(input).toHaveValue(draft)
    expect((await input.boundingBox()).height).toBe(height)
    await expect(page.locator('.composer__attachments')).toContainText('notes.txt')
    await expect(page.getByRole('button', { name: 'Chat configuration', exact: true })).toBeFocused()
    await openChatConfiguration(page)
    // Opening transfers focus on Vue's next tick; Escape belongs to the panel.
    await expect(close).toBeFocused()
    await page.keyboard.press('Escape')
    await expect(panel).toBeHidden()
    await expect(input).toHaveValue(draft)
  })
}

test('model and reasoning changes stay together in the in-place panel and persist', async ({ page, context }) => {
  const state = await mockAgents(context)
  await page.goto('/chats/alpha')
  await openChatConfiguration(page)
  await page.getByRole('button', { name: 'Reasoning effort', exact: true }).click()
  await page.getByRole('menuitemradio', { name: 'High', exact: true }).click()
  await expect.poll(() => state.data.chat.reasoning_effort).toBe('high')
  await page.getByRole('button', { name: 'Select model', exact: true }).click()
  await page.getByPlaceholder('Search models').fill('Plain')
  await page.getByPlaceholder('Search models').press('Enter')
  await expect.poll(() => state.data.chat.model).toBe('test/plain')
  // Escape in a picker closes only that picker, not the in-place preferences.
  await page.keyboard.press('Escape')
  await expect(page.getByRole('region', { name: 'Chat preferences' })).toBeVisible()
  await expect(page.getByRole('button', { name: 'Reasoning effort', exact: true })).toBeDisabled()
  await expect(page.getByRole('button', { name: 'Reasoning effort', exact: true })).toContainText('Provider default')
  await closeChatConfiguration(page)
  await page.reload()
  await openChatConfiguration(page)
  await expect(page.getByRole('button', { name: 'Select model', exact: true })).toContainText('Plain')
  expect(state.data.chat.reasoning_effort).toBe(null)
})

for (const width of [320, 390, 1440]) {
  test(`in-place preferences scroll without losing their close button at ${width}px`, async ({ page, context }) => {
    await mockAgents(context)
    await context.addInitScript(key => localStorage.setItem(key, JSON.stringify({ interfaceSize: 150 })), PREFERENCES_KEY)
    await page.setViewportSize({ width, height: 320 })
    await page.goto('/chats/alpha')
    await openChatConfiguration(page)
    const close = page.getByRole('button', { name: 'Close chat preferences', exact: true })
    for (const name of ['Select model', 'Select agent set', 'Reasoning effort', 'Select tools', 'Select projects', 'Select extensions']) {
      const button = page.getByRole('button', { name, exact: true })
      await button.scrollIntoViewIfNeeded()
      await expect(button).toBeInViewport({ ratio: 1 })
      await expect(close).toBeInViewport({ ratio: 1 })
    }
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
    await page.screenshot({ path: `/tmp/chat-preferences-small-${width}.png` })
    await closeChatConfiguration(page)
    await expect(page.getByRole('textbox', { name: 'Message', exact: true })).toBeVisible()
  })
}
