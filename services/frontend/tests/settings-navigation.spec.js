import { test, expect } from '@playwright/test'

async function mockApi (context, health) {
  await context.route('**/api/**', route => {
    const path = new URL(route.request().url()).pathname
    if (path === '/api/system') return route.fulfill({ json: {
      components: health === 'unknown' ? [] : [{ name: 'temporal', status: health }]
    } })
    if (path === '/api/events') return route.fulfill({ contentType: 'text/event-stream', body: ': connected\n\n' })
    const key = path.split('/').pop()
    return route.fulfill({ json: key === 'catalog' ? { models: [], tools: [], agent_sets: [] } : { [key]: [] } })
  })
}

for (const width of [1440, 901, 900, 390]) {
  for (const health of ['ok', 'degraded', 'unknown']) {
    test(`settings navigation at ${width}px with ${health} health`, async ({ page, context }) => {
      await mockApi(context, health)
      await page.setViewportSize({ width, height: 900 })
      await page.goto('/chats')

      const desktop = width > 900
      const settings = page.getByRole('link', { name: 'Settings', exact: true })
      await expect(settings).toHaveCount(1)
      await expect(settings).toBeVisible()
      await expect(page.locator(desktop ? '.side__cog' : '.rail__settings')).toBeHidden()
      await expect(settings).toHaveCSS('text-decoration-line', 'none')
      await settings.hover()
      await expect(settings).toHaveCSS('text-decoration-line', 'none')
      await settings.focus()
      await expect(settings).toHaveCSS('text-decoration-line', 'none')
      await expect(settings).toHaveAttribute('href', `/settings/${health === 'degraded' ? 'system' : 'general'}`)
      await expect(settings.locator('.dot')).toHaveCount(health === 'ok' ? 0 : 1)
      if (health === 'degraded') await expect(settings.locator('.dot')).toHaveClass(/dot--bad/)

      const box = await settings.boundingBox()
      if (desktop) {
        const rail = await page.locator('.rail').boundingBox()
        expect(box.x).toBeGreaterThanOrEqual(rail.x)
        expect(box.x + box.width).toBeLessThanOrEqual(rail.x + rail.width)
        expect(rail.y + rail.height - box.y - box.height).toBeLessThan(20)
      } else {
        const header = await page.locator('.side__head').boundingBox()
        expect(box.y).toBeGreaterThanOrEqual(header.y)
        expect(box.y + box.height).toBeLessThanOrEqual(header.y + header.height)
        expect(box.x).toBeGreaterThan(width - 70)
      }

      await settings.press('Enter')
      await expect(page).toHaveURL(`/settings/${health === 'degraded' ? 'system' : 'general'}`)
    })
  }
}

test('settings moves between header and rail when resizing', async ({ page, context }) => {
  await mockApi(context, 'ok')
  await page.goto('/chats')
  for (const width of [1440, 390, 1440]) {
    await page.setViewportSize({ width, height: 900 })
    await expect(page.getByRole('link', { name: 'Settings', exact: true })).toHaveCount(1)
    await expect(page.locator(width > 900 ? '.rail__settings' : '.side__cog')).toBeVisible()
    await expect(page.locator(width > 900 ? '.side__cog' : '.rail__settings')).toBeHidden()
  }
})
