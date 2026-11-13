import { test, expect } from '@playwright/test'
import { brand } from '../src/brand.js'
import { mockDesign } from './design-fixture.js'
import { clientBuildInfo } from '../scripts/client-build-info.js'
import { formatBuildInfo } from '../src/build-info.js'

const clientMetadata = clientBuildInfo('serve')
const client = formatBuildInfo(clientMetadata)
const commit = '68c4590f45388cc3d9270c459a5cd30985aba4e4'

for (const width of [1440, 320]) {
  for (const [name, metadata, label, linked] of [
    ['revision', { version: commit, environment: 'production' }, '68c4590', true],
    ['release', { version: commit, build_commit: commit, release_tag: 'v1.2.3' }, 'v1.2.3', false],
    ['legacy semver', { version: 'v2.3.4' }, 'v2.3.4', false],
    ['dev', { version: 'dev' }, 'dev', false],
    ['untrusted value', { version: 'javascript:'.repeat(100) }, 'Unknown', false]
  ]) {
    test(`${name} pill and details at ${width}px`, async ({ page, context }) => {
      await mockDesign(context)
      await context.route('**/api/system', route => route.fulfill({ json: { ...metadata, components: [], agent_sets: [] } }))
      await page.setViewportSize({ width, height: 900 })
      await page.goto('/settings/system')
      const pill = page.getByRole('button', { name: 'Build versions', exact: true })
      const different = (client.commit || client.label) !== (formatBuildInfo(metadata).commit || label)
      await expect(pill.locator('.version-pill__segment')).toHaveText(different ? [client.label, label] : [label])
      await expect(pill).not.toContainText(/Client|Server/)
      await expect(pill.locator('.version-pill__segment--client')).toHaveCount(different ? 1 : 0)
      await expect(page.getByRole('dialog', { name: 'Build version details' })).not.toBeVisible()
      await expect(page.locator('.system-health__config .build-version')).toHaveText([client.label, label])
      await expect(page.locator('.system-health__config dt')).toContainText(['Client version', 'Server version', 'Authentication', 'Default model', 'Model credentials'])
      const surface = page.locator('.system-health__config > div:nth-child(2) .build-version')
      if (linked) await expect(surface).toHaveAttribute('href', `${brand.repository}/commit/${commit}`)
      else await expect(surface).not.toHaveAttribute('href')

      // Native keyboard activation, dismissal and focus restoration.
      await pill.focus()
      await page.keyboard.press('Enter')
      const details = page.getByRole('dialog', { name: 'Build version details' })
      await expect(details).toBeVisible()
      await expect(pill).toHaveAttribute('aria-expanded', 'true')
      await expect(details.locator('dt')).toHaveText(['Client', 'Server'])
      await expect(details.locator('dd > span')).toHaveText([client.label, label])
      const release = details.getByRole('link', { name: 'Release notes for Server', exact: true })
      if (label.startsWith('v')) await expect(release).toHaveAttribute('href', `${brand.repository}/releases/tag/${encodeURIComponent(label)}`)
      else await expect(release).toHaveCount(0)
      if (metadata.version === commit) {
        await expect(details.getByRole('link', { name: 'Commit 68c4590 for Server' })).toHaveAttribute('href', `${brand.repository}/commit/${commit}`)
      }
      for (const link of await details.getByRole('link').all()) {
        await expect(link).toHaveAttribute('rel', 'noopener noreferrer')
        await expect(link).toHaveAttribute('target', '_blank')
        await expect(link).toHaveAttribute('href', new RegExp(`^${brand.repository}/`))
      }
      expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(width)
      const bounds = await details.boundingBox()
      expect(bounds.x).toBeGreaterThanOrEqual(0)
      expect(bounds.x + bounds.width).toBeLessThanOrEqual(width)
      await page.keyboard.press('Escape')
      await expect(details).not.toBeVisible()
      await expect(pill).toBeFocused()
      await expect(pill).toHaveAttribute('aria-expanded', 'false')
      await pill.click()
      await expect(details).toBeVisible()
      await page.getByRole('heading', { name: 'Settings', exact: true }).click()
      await expect(details).not.toBeVisible()
    })
  }
}

test('matching versions collapse to the server segment and refresh reactively', async ({ page, context }) => {
  await mockDesign(context)
  let payload = { ...clientMetadata, components: [], agent_sets: [] }
  await context.route('**/api/system', route => route.fulfill({ json: payload }))
  await page.goto('/settings/system')
  const pill = page.getByRole('button', { name: 'Build versions', exact: true })
  await expect(pill.locator('.version-pill__segment')).toHaveText([client.label])
  payload = { ...payload, version: commit, release_tag: 'v3.2.1' }
  await page.getByRole('button', { name: 'Refresh system health' }).click()
  await expect(pill.locator('.version-pill__segment')).toHaveText([client.label, 'v3.2.1'])
  await expect(page.locator('.system-health__config .build-version')).toHaveText([client.label, 'v3.2.1'])
  await pill.click()
  const details = page.getByRole('dialog', { name: 'Build version details' })
  await expect(details.getByRole('link', { name: 'Release notes for Server' })).toHaveAttribute('href', `${brand.repository}/releases/tag/v3.2.1`)
  payload = { ...clientMetadata, components: [], agent_sets: [] }
  await page.keyboard.press('Escape')
  await page.getByRole('button', { name: 'Refresh system health' }).click()
  await expect(pill.locator('.version-pill__segment')).toHaveText([client.label])
})
