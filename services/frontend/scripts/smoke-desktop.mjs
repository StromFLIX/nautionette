import { _electron as electron, expect } from '@playwright/test'
import { resolve } from 'node:path'

// CI exercises the packaged Linux application, not just the browser bundle.
const application = await electron.launch({
  executablePath: resolve('out/desktop/linux-unpacked/nautionette-frontend'),
  args: process.getuid?.() === 0 ? ['--no-sandbox'] : [],
  timeout: 30000
})
try {
  const page = await application.firstWindow()
  const errors = []
  page.on('pageerror', error => errors.push(error.message))
  await expect(page.getByPlaceholder('https://nautionette.example.com')).toBeVisible()
  expect(await page.evaluate(() => ({
    desktop: globalThis.nautionetteDesktop,
    node: typeof globalThis.require,
    origin: location.origin
  }))).toEqual({ desktop: true, node: 'undefined', origin: 'capacitor://localhost' })
  await page.reload()
  await expect(page.getByPlaceholder('https://nautionette.example.com')).toBeVisible()
  expect(errors).toEqual([])
  console.log('Packaged desktop: local assets, instance setup, isolated renderer and route reload OK')
} finally {
  await application.close()
}
