import { test, expect } from '@playwright/test'
import { mockAgents } from './agent-fixture.js'
import { mockDesign } from './design-fixture.js'

// Native interactive gestures traverse this same browser history. Test the
// resulting popstate navigation rather than inventing a second touch router.
test.beforeEach(async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 })
})

for (const reload of [false, true]) {
  test(`back from a chat restores its agent list; forward reopens it (reload=${reload})`, async ({ page, context }) => {
    const state = await mockAgents(context)
    const agent = state.addAgent('Ada')
    state.data.chat.agent_id = agent.id
    await page.goto(`/chats?agent=${agent.id}`)
    await page.locator('.chat-list-item').click()
    await expect(page).toHaveURL(new RegExp(`/chats/alpha\\?agent=${agent.id}$`))
    await expect(page.getByRole('textbox', { name: 'Message', exact: true })).toBeVisible()
    if (reload) {
      await page.reload()
      await expect(page.getByRole('textbox', { name: 'Message', exact: true })).toBeVisible()
    }
    await page.goBack()
    await expect(page).toHaveURL(new RegExp(`/chats\\?agent=${agent.id}$`))
    await expect(page.locator('#shell-sidebar')).toBeVisible()
    await expect(page.getByRole('heading', { name: 'Ada', exact: true })).toBeVisible()
    await page.goForward()
    await expect(page).toHaveURL(new RegExp(`/chats/alpha\\?agent=${agent.id}$`))
    await expect(page.getByRole('textbox', { name: 'Message', exact: true })).toBeVisible()
  })
}

test('launch redirects seed the chat list underneath the focused welcome composer', async ({ page, context }) => {
  await mockAgents(context)
  await page.goto('/')
  await expect(page.getByRole('textbox', { name: 'Message', exact: true })).toBeVisible()
  await expect(page).toHaveURL(/\/chats\?welcome=1$/)
  await page.goBack()
  await expect(page).toHaveURL(/\/chats$/)
  await expect(page.locator('#shell-sidebar')).toBeVisible()
  await page.goForward()
  await expect(page).toHaveURL(/\/chats\?welcome=1$/)
  await expect(page.getByRole('textbox', { name: 'Message', exact: true })).toBeVisible()
})

test('settings reload retains the originating chat rather than replacing it with the list', async ({ page, context }) => {
  await mockDesign(context)
  await page.goto('/chats/alpha')
  await expect(page.getByRole('textbox', { name: 'Message', exact: true })).toBeVisible()
  await page.keyboard.press('Control+,')
  await expect(page).toHaveURL(/\/settings\/general$/)
  await page.reload()
  await expect(page.locator('.settings')).toBeVisible()
  await page.goBack()
  await expect(page).toHaveURL(/\/chats\/alpha$/)
  await expect(page.getByRole('textbox', { name: 'Message', exact: true })).toBeVisible()
  await page.goBack()
  await expect(page).toHaveURL(/\/chats$/)
  await expect(page.locator('#shell-sidebar')).toBeVisible()
})

for (const [detail, list] of [
  ['/chats?welcome=1', '/chats'],
  ['/chats/alpha', '/chats'],
  ['/workflows/digest?tab=Code', '/workflows'],
  ['/runs/example', '/runs'],
  ['/apps/example?agent=agent-1', '/apps?agent=agent-1'],
  ['/settings/general', '/chats']
]) {
  test(`a deep link has exactly one parent even after reload: ${detail}`, async ({ page, context }) => {
    const state = await mockAgents(context)
    state.addAgent('Ada')
    await context.route('**/api/miniapps/example', route => route.fulfill({ json: { id: 'example', title: 'Example', agent_id: 'agent-1', revision: 1, releases: [] } }))
    await context.route('**/api/runs/example', route => route.fulfill({ json: { run: { workflow: 'digest', status: 'completed' }, history_status: 'expired', note: 'Saved run' } }))
    const errors = []
    page.on('pageerror', error => errors.push(error.message))
    await page.goto(detail)
    await expect(page.locator('.shell')).toBeVisible()
    const position = await page.evaluate(() => history.state.position)
    await page.reload()
    await expect(page.locator('.shell')).toBeVisible()
    expect(await page.evaluate(() => history.state.position)).toBe(position)
    await page.goBack()
    await expect(page).toHaveURL(new URL(list, 'http://127.0.0.1:9012').href)
    await expect(page.locator('#shell-sidebar')).toBeVisible()
    await page.goForward()
    await expect(page).toHaveURL(new URL(detail, 'http://127.0.0.1:9012').href)
    expect(errors).toEqual([])
  })
}

test('app and scrolling views permit horizontal browser navigation while containing vertical bounce', async ({ page, context }) => {
  await mockDesign(context)
  for (const path of ['/chats/alpha', '/workflows/digest?tab=Code', '/settings/general']) {
    await page.goto(path)
    await expect(page.locator('.shell')).toBeVisible()
    const styles = await page.evaluate(() => [...document.querySelectorAll('html, body, #app, .scroll-y, .settings__tree, .settings__body')].map(element => {
      const style = getComputedStyle(element)
      return { x: style.overscrollBehaviorX, y: style.overscrollBehaviorY }
    }))
    for (const style of styles) {
      expect(style.x).toBe('auto')
      expect(['contain', 'none']).toContain(style.y)
    }
  }
})
