import { test, expect } from '@playwright/test'
import { mockAgents } from './agent-fixture.js'

for (const width of [1440, 390, 320]) {
  for (const entry of ['/dashboards', '/dashboards/nautionette-git-release?agent=removed', '/unknown/retired-view']) {
    test(`retired and unknown links recover to usable chats at ${width}px: ${entry}`, async ({ page, context }) => {
      const state = await mockAgents(context)
      await context.addInitScript(() => {
        localStorage.setItem('nautionette.dashboard-order:old-instance', '["old"]')
        localStorage.setItem('nautionette.dashboard-order:another-instance', '["other"]')
        localStorage.setItem('unrelated-preference', 'keep')
        sessionStorage.setItem('nautionette.dashboard-submission:old-instance:old:action', '{}')
        sessionStorage.setItem('unrelated-session', 'keep')
      })
      const ada = state.addAgent('Ada')
      const errors = []
      const retiredRequests = []
      page.on('pageerror', error => errors.push(error.message))
      page.on('request', request => {
        if (new URL(request.url()).pathname.startsWith('/api/dashboards')) retiredRequests.push(request.url())
      })
      await page.setViewportSize({ width, height: 900 })
      await page.goto(entry)
      await expect(page).toHaveURL(/\/chats$/)
      await expect(page.getByRole('heading', { name: 'Chats', exact: true })).toBeVisible()
      await expect(page.getByRole('link', { name: 'Dashboards', exact: true })).toHaveCount(0)
      await expect(page.locator('.chat-list-item')).toHaveCount(1)
      await page.getByRole('button', { name: 'Chats with Ada', exact: true }).click()
      await expect(page).toHaveURL(new RegExp(`/chats\\?agent=${ada.id}$`))
      await expect(page.getByRole('heading', { name: 'Ada', exact: true })).toBeVisible()
      await page.getByRole('button', { name: 'Show all chats', exact: true }).click()
      await expect(page.locator('.chat-list-item')).toHaveCount(1)
      // The editor can paint before acknowledging the visit has refreshed the
      // list. Finish that round trip before deliberately unloading the page;
      // WebKit's intercepted fetch otherwise races the full reload.
      const readSaved = page.waitForResponse(response => new URL(response.url()).pathname === '/api/chats/alpha/read-state' && response.status() === 200)
      const listRefreshed = page.waitForResponse(response => new URL(response.url()).pathname === '/api/chats' && response.status() === 200)
      await page.locator('.chat-list-item').click()
      await expect(page).toHaveURL(/\/chats\/alpha$/)
      await expect(page.getByRole('textbox', { name: 'Message' })).toBeVisible()
      expect(await (await readSaved).finished()).toBeNull()
      expect(await (await listRefreshed).finished()).toBeNull()
      await page.reload()
      await expect(page.getByRole('textbox', { name: 'Message' })).toBeVisible()
      await page.getByRole('textbox', { name: 'Message' }).fill('Chat still works after removing the old UI.')
      await page.getByRole('button', { name: 'Send message', exact: true }).click()
      await expect.poll(() => state.sent.length).toBe(1)
      expect(state.sent[0].text).toBe('Chat still works after removing the old UI.')
      expect(retiredRequests).toEqual([])
      expect(await page.evaluate(() => Object.keys(localStorage).filter(key => key.startsWith('nautionette.dashboard-order:')))).toEqual([])
      expect(await page.evaluate(() => Object.keys(sessionStorage).filter(key => key.startsWith('nautionette.dashboard-submission:')))).toEqual([])
      expect(await page.evaluate(() => localStorage.getItem('unrelated-preference'))).toBe('keep')
      expect(await page.evaluate(() => sessionStorage.getItem('unrelated-session'))).toBe('keep')
      expect(errors).toEqual([])
      expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
    })
  }
}
