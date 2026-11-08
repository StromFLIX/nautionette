import { _electron as electron, expect } from '@playwright/test'
import { resolve } from 'node:path'

// CI exercises the packaged Linux application, not just the browser bundle.
const application = await electron.launch({
  executablePath: process.env.ELECTRON_EXECUTABLE || resolve('out/desktop/linux-unpacked/nautionette-frontend'),
  // Test containers may not provide Chromium's OS sandbox. This affects this
  // smoke launcher only, never the packaged app's security defaults.
  args: [...(process.getuid?.() === 0 || process.env.ELECTRON_TEST_NO_SANDBOX === '1' ? ['--no-sandbox'] : []), ...(process.env.ELECTRON_EXECUTABLE ? ['.'] : [])],
  timeout: 30000
})
try {
  const page = await application.firstWindow()
  const errors = []
  page.on('pageerror', error => errors.push(error.message))
  // Xvfb has no desktop window manager. Focus the actual app and keep its
  // startup dialog/animation from being background-throttled by Chromium.
  await application.evaluate(({ BrowserWindow }) => {
    const window = BrowserWindow.getAllWindows()[0]
    window.webContents.setBackgroundThrottling(false)
    window.show()
    window.focus()
  })
  await expect(page.getByPlaceholder('https://nautionette.example.com')).toBeVisible({ timeout: 20000 })
  expect(await page.evaluate(() => ({
    desktop: globalThis.nautionetteDesktop?.oauth?.version,
    node: typeof globalThis.require,
    origin: location.origin
  }))).toEqual({ desktop: 1, node: 'undefined', origin: 'capacitor://localhost' })
  // Exercise the actual isolated preload -> IPC -> loopback socket path.
  // Suppress the external browser; Microsoft remains a manual acceptance test.
  await application.evaluate(({ shell }) => { shell.openExternal = async () => {} })
  const receiver = await page.evaluate(() => globalThis.nautionetteDesktop.oauth.prepare('http://127.0.0.1:0/callback'))
  const state = 's'.repeat(43)
  const authorization = 'https://login.example.test/authorize?' + new URLSearchParams({
    state, redirect_uri: receiver.redirect_uri, response_type: 'code',
    code_challenge: 'x'.repeat(43), code_challenge_method: 'S256'
  })
  const callback = page.evaluate(({ receiver, state, authorization }) => globalThis.nautionetteDesktop.oauth.authorize({
    id: receiver.id, state, authorization_url: authorization
  }), { receiver, state, authorization })
  // A validated callback should bring this exact app window back, not leave
  // the user staring at a localhost page behind a hidden desktop client.
  await application.evaluate(({ BrowserWindow }) => { BrowserWindow.getAllWindows()[0].hide() })
  const forged = await fetch(receiver.redirect_uri + '?' + new URLSearchParams({ state: 'wrong', code: 'synthetic-code' }))
  expect(forged.status).toBe(400)
  expect(await application.evaluate(({ BrowserWindow }) => BrowserWindow.getAllWindows()[0].isVisible())).toBe(false)
  let received = false
  for (let attempt = 0; attempt < 50; attempt++) {
    const response = await fetch(receiver.redirect_uri + '?' + new URLSearchParams({ state, code: 'synthetic-code' }))
    if (response.ok) {
      const body = await response.text()
      expect(body).toContain('Callback received')
      expect(body).not.toContain('synthetic-code')
      expect(body).not.toContain(state)
      received = true; break
    }
    await new Promise(resolve => setTimeout(resolve, 20))
  }
  expect(received).toBe(true)
  expect(await callback).toEqual({ method: 'GET', fields: { state, code: 'synthetic-code' } })
  expect(await application.evaluate(({ BrowserWindow }) => BrowserWindow.getAllWindows()[0].isVisible())).toBe(true)
  console.log(`Desktop OAuth: actual loopback listener answered on port ${receiver.port}; callback completed and owning app window returned`)
  const fixed = await page.evaluate(() => globalThis.nautionetteDesktop.oauth.prepare('http://localhost:12798'))
  expect(fixed.port).toBe(12798)
  const fixedResponse = await fetch('http://127.0.0.1:12798/')
  expect(fixedResponse.status).toBe(400) // No state installed: arbitrary local requests rejected.
  await page.evaluate(() => globalThis.nautionetteDesktop.oauth.cancel())
  console.log('Desktop OAuth: fixed port 12798 bound, rejected unsolicited request, then closed')
  const formReceiver = await page.evaluate(() => globalThis.nautionetteDesktop.oauth.prepare('http://localhost:0/callback'))
  const formUrl = 'https://login.example.test/authorize?' + new URLSearchParams({
    state, redirect_uri: formReceiver.redirect_uri, response_type: 'code', response_mode: 'form_post',
    code_challenge: 'x'.repeat(43), code_challenge_method: 'S256'
  })
  const formCallback = page.evaluate(({ receiver, state, url }) => globalThis.nautionetteDesktop.oauth.authorize({
    id: receiver.id, state, authorization_url: url, response_mode: 'form_post'
  }), { receiver: formReceiver, state, url: formUrl })
  let formReceived = false
  for (let attempt = 0; attempt < 50; attempt++) {
    const response = await fetch(formReceiver.redirect_uri, { method: 'POST',
      headers: { 'Content-Type': 'application/x-www-form-urlencoded', Origin: 'https://login.example.test' },
      body: new URLSearchParams({ state, code: 'synthetic-form-code', session_state: 'synthetic-session' }).toString() })
    if (response.ok) { formReceived = true; break }
    await new Promise(resolve => setTimeout(resolve, 20))
  }
  expect(formReceived).toBe(true)
  expect(await formCallback).toEqual({ method: 'POST', fields: { state, code: 'synthetic-form-code', session_state: 'synthetic-session' } })
  console.log(`Desktop OAuth: form_post callback received on actual localhost port ${formReceiver.port}`)
  await page.reload()
  await expect(page.getByPlaceholder('https://nautionette.example.com')).toBeVisible({ timeout: 20000 })
  expect(errors).toEqual([])
  console.log('Packaged desktop: local assets, instance setup, isolated renderer and route reload OK')
} finally {
  await application.close()
}
