import { test, expect } from '@playwright/test'
import { brand } from '../src/brand.js'
import { mockDesign } from './design-fixture.js'

const commit = '68c4590f45388cc3d9270c459a5cd30985aba4e4'

for (const width of [1440, 320]) {
  for (const [name, metadata, label, linked] of [
    ['revision', { version: commit, environment: 'production' }, '68c4590', true],
    ['release', { version: commit, build_commit: commit, release_tag: 'v1.2.3' }, 'v1.2.3', false],
    ['legacy semver', { version: 'v2.3.4' }, 'v2.3.4', false],
    ['dev', { version: 'dev' }, 'dev', false],
    ['untrusted value', { version: 'javascript:'.repeat(100) }, 'Unknown', false]
  ]) {
    test(`${name} is consistent in Settings and System at ${width}px`, async ({ page, context }) => {
      await mockDesign(context)
      await context.route('**/api/system', route => route.fulfill({ json: { ...metadata, components: [], agent_sets: [] } }))
      await page.setViewportSize({ width, height: 900 })
      await page.goto('/settings/system')
      const surfaces = page.locator('.build-version')
      await expect(surfaces).toHaveCount(2)
      for (const surface of await surfaces.all()) {
        await expect(surface).toBeVisible()
        await expect(surface).toHaveText(label)
        if (linked) {
          await expect(surface).toHaveAttribute('href', `${brand.repository}/commit/${commit}`)
          await expect(surface).toHaveAttribute('rel', 'noopener noreferrer')
          await expect(surface).toHaveAttribute('target', '_blank')
          await expect(surface).toHaveAccessibleName(`View commit ${commit}`)
        } else {
          await expect(surface).not.toHaveAttribute('href')
        }
      }
      expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(width)
      // Revision links are keyboard reachable, not click handlers on plain text.
      if (linked) {
        await surfaces.first().focus()
        await expect(surfaces.first()).toBeFocused()
      }
    })
  }
}

test('refreshed release metadata updates both surfaces without reloading', async ({ page, context }) => {
  await mockDesign(context)
  let payload = { version: commit, components: [], agent_sets: [] }
  await context.route('**/api/system', route => route.fulfill({ json: payload }))
  await page.goto('/settings/system')
  await expect(page.locator('.build-version').first()).toHaveText('68c4590')
  payload = { ...payload, release_tag: 'v3.2.1' }
  await page.getByRole('button', { name: 'Refresh system health' }).click()
  await expect(page.locator('.build-version')).toHaveText(['v3.2.1', 'v3.2.1'])
  await expect(page.locator('a.build-version')).toHaveCount(0)
})
