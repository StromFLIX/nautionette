import { test, expect } from '@playwright/test'
import { mockAgents, projectId } from './agent-fixture.js'
import { openChatConfiguration, closeChatConfiguration } from './helpers.js'

const picker = page => page.getByRole('button', { name: 'Select agent', exact: true })
const settings = page => page.getByRole('button', { name: 'Chat configuration', exact: true })

for (const width of [1440, 320]) {
  for (const route of ['/chats', '/chats/alpha']) {
    test(`direct agent switching and resets preserve the draft on ${route} at ${width}px`, async ({ page, context }) => {
      const state = await mockAgents(context)
      const writer = state.addAgent('Writer with a long descriptive name', { tools: [], reasoning_effort: 'high', project_ids: [projectId] })
      const researcher = state.addAgent('Research', { tools: ['mail_search'] })
      state.settings.default_agent_id = researcher.id
      await page.setViewportSize({ width, height: 900 })
      await page.goto(route === '/chats' ? '/chats?welcome=1' : route)
      await expect(picker(page)).toBeEnabled()
      await page.getByLabel('Message', { exact: true }).fill('Keep this unsent draft')
      await page.locator('input[type="file"]').setInputFiles({ name: 'notes.txt', mimeType: 'text/plain', buffer: Buffer.from('Keep this attachment') })
      await expect(page.getByRole('button', { name: 'Remove notes.txt' })).toBeVisible()

      await picker(page).click()
      await page.getByRole('menuitemradio', { name: writer.name, exact: true }).click()
      await expect(picker(page)).toContainText(writer.name)
      await expect(settings(page)).toHaveAttribute('aria-expanded', 'false')
      if (route !== '/chats') await expect.poll(() => state.chats.alpha.chat.agent_id).toBe(writer.id)
      await expect(page.getByLabel('Message', { exact: true })).toHaveValue('Keep this unsent draft')
      await expect(page.getByRole('button', { name: 'Remove notes.txt' })).toBeVisible()
      expect(await page.locator('.composer__bar').evaluate(el => el.scrollWidth <= el.clientWidth)).toBe(true)

      // Settings stay separate; the dropdown can restore overrides without opening them.
      await openChatConfiguration(page)
      await expect(page.getByLabel('Select tools', { exact: true })).toContainText('0/2 tools')
      await page.getByLabel('Reasoning effort', { exact: true }).click()
      await page.getByRole('menuitemradio', { name: 'Low', exact: true }).click()
      await closeChatConfiguration(page)
      await picker(page).click()
      await page.getByRole('button', { name: 'Reapply agent defaults', exact: true }).click()
      await expect(settings(page)).toHaveAttribute('aria-expanded', 'false')
      await openChatConfiguration(page)
      await expect(page.getByLabel('Reasoning effort', { exact: true })).toContainText('High')
      await closeChatConfiguration(page)

      await picker(page).click()
      await page.getByRole('button', { name: 'Reset to default agent', exact: true }).click()
      await expect(picker(page)).toContainText('Research')
      await openChatConfiguration(page)
      await expect(page.getByLabel('Select tools', { exact: true })).toContainText('1/2 tools')
      await closeChatConfiguration(page)

      // Global defaults remain an explicit choice even with a configured default agent.
      await picker(page).click()
      await page.getByRole('menuitemradio', { name: 'Global defaults', exact: true }).click()
      await expect(picker(page)).toContainText('Global defaults')
      await expect(page.getByLabel('Message', { exact: true })).toHaveValue('Keep this unsent draft')
      await expect(page.getByRole('button', { name: 'Remove notes.txt' })).toBeVisible()
    })
  }
}

test('reset falls back to global defaults and failed saves keep the selected agent', async ({ page, context }) => {
  const state = await mockAgents(context)
  const writer = state.addAgent('Writer', { tools: [] })
  Object.assign(state.data.chat, { agent_id: writer.id, agent_name: writer.name, tools: [], agent_customized: false })
  await page.goto('/chats/alpha')
  await expect(picker(page)).toContainText('Writer')
  state.chatPatchFailure = true
  await picker(page).click()
  await page.getByRole('button', { name: 'Reset to default agent', exact: true }).click()
  await expect(page.getByText('Chat settings could not be saved', { exact: true })).toBeVisible()
  await expect(picker(page)).toContainText('Writer')
  state.chatPatchFailure = false
  await picker(page).click()
  await page.getByRole('button', { name: 'Reset to default agent', exact: true }).click()
  await expect(picker(page)).toContainText('Global defaults')
  await expect.poll(() => state.chats.alpha.chat.agent_id).toBe(null)
  expect(state.chats.alpha.chat.tools).toBe(null)
})

test('direct agent selection waits for defaults discovery', async ({ page, context }) => {
  const state = await mockAgents(context)
  let release
  state.catalogGate = new Promise(resolve => { release = resolve })
  await page.goto('/chats')
  await expect(picker(page)).toBeDisabled()
  release()
  await expect(picker(page)).toBeEnabled()
})
