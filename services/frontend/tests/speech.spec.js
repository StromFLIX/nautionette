import { test, expect } from '@playwright/test'
import { mockDesign } from './design-fixture.js'

test.use({ launchOptions: { args: ['--use-fake-device-for-media-stream', '--use-fake-ui-for-media-stream'] } })

async function speechFixture (context, { denied = false, real = false, holdFinal = false, unavailable = false } = {}) {
  const state = await mockDesign(context)
  const frames = [], controls = [], sockets = [], tickets = [], forbidden = []
  if (!real) await context.addInitScript(({ denied }) => {
    window.stoppedTracks = 0
    Object.defineProperty(navigator, 'mediaDevices', { value: {
      async getUserMedia () {
        if (denied) throw new DOMException('Denied', 'NotAllowedError')
        return { getTracks: () => [{ stop () { window.stoppedTracks++ } }] }
      }
    } })
    window.AudioContext = class {
      constructor () { this.sampleRate = 16000; this.destination = {} }
      async resume () {}
      async close () {}
      createGain () { return { gain: { value: 1 }, connect () {}, disconnect () {} } }
      createMediaStreamSource () {
        return { disconnect () {}, connect: node => {
          window.feedVoice = (seconds, amplitude = 0.1) => {
            for (let n = 0; n < seconds * 4; n++) node.onaudioprocess?.({ inputBuffer: { getChannelData: () => new Float32Array(4000).fill(amplitude) } })
          }
          setTimeout(() => window.feedVoice(1), 0)
        } }
      }
      createScriptProcessor () { return { connect () {}, disconnect () {}, onaudioprocess: null } }
    }
  }, { denied })
  await context.route('**/api/speech', route => route.fulfill({ json: { enabled: true, languages: ['en', 'de'] } }))
  await context.route('**/api/speech/stream-ticket', route => {
    tickets.push(route.request().postDataJSON())
    return route.fulfill({ json: { ticket: 'ephemeral-ticket' } })
  })
  await context.route('**/api/speech/cleanup', route => { forbidden.push(route.request().url()); return route.abort() })
  await context.route('**/api/speech/transcribe?*', route => { forbidden.push(route.request().url()); return route.abort() })
  await context.routeWebSocket('**/api/speech/stream', socket => {
    sockets.push(socket)
    let revision = 0, sequence = 0
    const send = (type, text) => socket.send(JSON.stringify({ type, text, revision: ++revision }))
    socket.onMessage(message => {
      if (typeof message === 'string') {
        const event = JSON.parse(message)
        controls.push(event)
        if (event.type === 'auth') socket.send(JSON.stringify({ type: unavailable ? 'error' : 'ready' }))
        if (event.type === 'end') {
          expect(event.sequence).toBe(sequence)
          if (!holdFinal) send('done', sequence ? 'Fix the test.' : '')
        }
      } else {
        expect(message.readUInt32LE(0)).toBe(sequence++)
        frames.push(message)
        send('transcript', 'Fix the test.')
      }
    })
  })
  return { state, frames, controls, sockets, tickets, forbidden }
}
const draft = page => page.getByRole('textbox', { name: 'Message', exact: true })
const mic = page => page.getByRole('button', { name: 'Dictate message', exact: true })
const stop = page => page.getByRole('button', { name: 'Finish recording', exact: true })

for (const width of [320, 390, 1440]) test(`local streaming mic/send fit at ${width}px and Stop never reuploads audio`, async ({ page, context }) => {
  const f = await speechFixture(context)
  await page.setViewportSize({ width, height: 800 })
  await page.goto('/chats/alpha')
  await draft(page).fill('Existing draft')
  await mic(page).click()
  await expect(draft(page)).toHaveValue('Existing draft\nFix the test.')
  await expect(stop(page)).toBeVisible()
  await expect(page.getByRole('button', { name: 'Send message', exact: true })).toBeDisabled()
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
  const count = f.frames.length
  await stop(page).click()
  await expect(mic(page)).toBeEnabled()
  expect(f.frames).toHaveLength(count)
  expect(f.forbidden).toHaveLength(0)
  expect(f.state.sent).toHaveLength(0)
  expect(await page.evaluate(() => stoppedTracks)).toBe(1)
  await draft(page).fill('Reviewed editable transcript')
  await page.getByRole('button', { name: 'Send message', exact: true }).click()
  await expect.poll(() => f.state.sent.length).toBe(1)
})

test('old cleanup preferences cannot invoke a cloud model; language is persisted', async ({ page, context }) => {
  const f = await speechFixture(context)
  await context.addInitScript(() => localStorage.setItem('nautionette.preferences.v1', JSON.stringify({ dictationCleanup: true, dictationLanguage: 'de' })))
  await page.goto('/settings/workspace')
  await expect(page.getByRole('switch', { name: 'Clean up dictation', exact: true })).toHaveCount(0)
  await expect(page.getByLabel('Dictation language', { exact: true })).toHaveValue('de')
  await expect(page.getByLabel('Dictation language', { exact: true }).locator('option')).toHaveCount(2)
  await page.goto('/chats/alpha')
  await mic(page).click()
  await expect(draft(page)).toHaveValue('Fix the test.')
  await stop(page).click()
  await expect(mic(page)).toBeEnabled()
  expect(f.tickets[0]).toEqual({ language: 'de' })
  expect(f.forbidden).toHaveLength(0)
  await expect(page.getByRole('button', { name: 'Use original transcript', exact: true })).toHaveCount(0)
})

test('cancel restores the original draft and releases the microphone', async ({ page, context }) => {
  const f = await speechFixture(context)
  await page.goto('/chats/alpha')
  await draft(page).fill('Keep this')
  await mic(page).click()
  await expect(draft(page)).toHaveValue('Keep this\nFix the test.')
  await page.getByRole('button', { name: 'Cancel dictation', exact: true }).click()
  await expect(draft(page)).toHaveValue('Keep this')
  expect(await page.evaluate(() => stoppedTracks)).toBe(1)
  expect(f.controls.filter(x => x.type === 'end')).toHaveLength(0)
})

test('microphone permission errors preserve the typed draft and allow retry', async ({ page, context }) => {
  await speechFixture(context, { denied: true })
  await page.goto('/chats/alpha')
  await draft(page).fill('Keep this')
  await mic(page).click()
  await expect(page.getByRole('status').filter({ hasText: 'Microphone permission denied' })).toBeVisible()
  await expect(draft(page)).toHaveValue('Keep this')
  await expect(mic(page)).toBeEnabled()
})

test('private service failure happens before microphone capture', async ({ page, context }) => {
  await speechFixture(context, { unavailable: true })
  await page.goto('/chats/alpha')
  await draft(page).fill('Keep this')
  await mic(page).click()
  await expect(page.getByRole('status').filter({ hasText: 'Live dictation unavailable' })).toBeVisible()
  await expect(draft(page)).toHaveValue('Keep this')
  expect(await page.evaluate(() => stoppedTracks)).toBe(0)
  await expect(mic(page)).toBeEnabled()
})

test('disconnect preserves recognized words and never reconnects or replays audio', async ({ page, context }) => {
  const f = await speechFixture(context)
  await page.goto('/chats/alpha')
  await mic(page).click()
  await expect(draft(page)).toHaveValue('Fix the test.')
  f.sockets[0].close()
  await expect(page.getByRole('status').filter({ hasText: 'recognized text was kept' })).toBeVisible()
  await expect(draft(page)).toHaveValue('Fix the test.')
  await expect(mic(page)).toBeEnabled()
  expect(f.sockets).toHaveLength(1)
  expect(await page.evaluate(() => stoppedTracks)).toBe(1)
})

test('late final results after cancel cannot overwrite an editable draft', async ({ page, context }) => {
  const f = await speechFixture(context, { holdFinal: true })
  await page.goto('/chats/alpha')
  await draft(page).fill('Original')
  await mic(page).click()
  await expect(draft(page)).toHaveValue('Original\nFix the test.')
  await stop(page).click()
  await expect(page.getByText('Finalizing…', { exact: true })).toBeVisible()
  await page.getByRole('button', { name: 'Cancel dictation', exact: true }).click()
  await draft(page).fill('User edits')
  f.sockets[0].send(JSON.stringify({ type: 'done', revision: 99, text: 'Late result' }))
  await expect(draft(page)).toHaveValue('User edits')
})

test('navigation closes an active microphone without transcribing into another chat', async ({ page, context }) => {
  await speechFixture(context)
  await page.goto('/chats/alpha')
  await mic(page).click()
  await expect(draft(page)).toHaveValue('Fix the test.')
  await page.getByRole('link', { name: /Workflows/ }).first().click()
  await expect.poll(() => page.evaluate(() => stoppedTracks)).toBe(1)
})

test('real AudioWorklet streams bounded binary frames and flushes on Stop', async ({ page, context }) => {
  const f = await speechFixture(context, { real: true })
  await context.grantPermissions(['microphone'])
  await page.goto('/chats/alpha')
  await mic(page).click()
  await expect(draft(page)).toHaveValue('Fix the test.', { timeout: 15000 })
  await expect(stop(page)).toBeVisible()
  expect(f.frames.length).toBeGreaterThan(0)
  expect(f.frames.every(frame => frame.length <= 65540)).toBe(true)
  expect(f.frames.every(frame => frame.subarray(0, 4).toString() !== 'RIFF')).toBe(true)
  await stop(page).click()
  await expect(mic(page)).toBeEnabled()
  expect(f.controls.filter(x => x.type === 'end')).toHaveLength(1)
  expect(f.state.sent).toHaveLength(0)
})
