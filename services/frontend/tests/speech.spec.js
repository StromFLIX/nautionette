import { test, expect } from '@playwright/test'
import { mockDesign } from './design-fixture.js'
import { openChatConfiguration, closeChatConfiguration } from './helpers.js'

test.use({ launchOptions: { args: ['--use-fake-device-for-media-stream', '--use-fake-ui-for-media-stream'] } })

async function speechFixture (context, { denied = false, cleanup = false } = {}) {
  const state = await mockDesign(context)
  const uploads = [], cleanups = []
  await context.addInitScript(({ denied, cleanup }) => {
    if (cleanup && !localStorage.getItem('nautionette.preferences.v1')) localStorage.setItem('nautionette.preferences.v1', JSON.stringify({ dictationCleanup: true }))
    window.stoppedTracks = 0
    Object.defineProperty(navigator, 'mediaDevices', { value: {
      async getUserMedia () {
        if (denied) throw new DOMException('Denied', 'NotAllowedError')
        return { getTracks: () => [{ stop () { window.stoppedTracks++ } }] }
      }
    } })
    window.AudioContext = class {
      constructor () { this.sampleRate = 16000; this.destination = {}; this.node = null }
      async resume () {}
      async close () {}
      createGain () { return { gain: { value: 1 }, connect () {}, disconnect () {} } }
      createMediaStreamSource () {
        return { disconnect () {}, connect: node => {
          window.feedVoice = seconds => { for (let n = 0; n < seconds * 4; n++) node.onaudioprocess?.({ inputBuffer: { getChannelData: () => new Float32Array(4000).fill(0.1) } }) }
          setTimeout(() => window.feedVoice(1), 0)
        } }
      }
      createScriptProcessor () { return { connect () {}, disconnect () {}, onaudioprocess: null } }
    }
  }, { denied, cleanup })
  await context.route('**/api/speech', route => route.fulfill({ json: { enabled: true } }))
  await context.route('**/api/speech/transcribe?*', async route => {
    uploads.push({ query: Object.fromEntries(new URL(route.request().url()).searchParams), bytes: route.request().postDataBuffer() })
    const text = cleanup ? 'um fix the test' : 'Fix the test.'
    const events = [{ type: 'transcript', text }, { type: 'delta', text }, { type: 'done', text }]
    return route.fulfill({ contentType: 'text/event-stream', body: events.map(event => `data: ${JSON.stringify(event)}\n\n`).join('') })
  })
  await context.route('**/api/speech/cleanup', route => {
    cleanups.push(route.request().postDataJSON())
    const events = [{ type: 'delta', text: 'Fix ' }, { type: 'delta', text: 'the test.' }, { type: 'done', text: 'Fix the test.' }]
    return route.fulfill({ contentType: 'text/event-stream', body: events.map(event => `data: ${JSON.stringify(event)}\n\n`).join('') })
  })
  return { state, uploads, cleanups }
}

for (const width of [320, 390, 1440]) test(`joined microphone/send and header context fit at ${width}px`, async ({ page, context }, testInfo) => {
  const { state, uploads } = await speechFixture(context)
  await page.setViewportSize({ width, height: 800 })
  await page.goto('/chats/alpha')
  const draft = page.getByRole('textbox', { name: 'Message', exact: true })
  await draft.fill('Existing draft')
  await expect(page.getByRole('button', { name: 'Select model', exact: true })).toHaveCount(0)
  await expect(page.locator('.composer__bar > button').first()).toHaveText('add')
  await expect(page.locator('.pane-head .context-ring')).toBeVisible()
  await page.getByRole('button', { name: 'Dictate message', exact: true }).click()
  await expect(page.getByRole('button', { name: 'Finish recording', exact: true })).toBeVisible()
  await expect(page.locator('.voice-control__wave')).toBeVisible()
  await expect(page.getByRole('button', { name: 'Send message', exact: true })).toBeDisabled()
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
  await page.screenshot({ path: testInfo.outputPath(`dictation-recording-${width}.png`) })
  await page.getByRole('button', { name: 'Finish recording', exact: true }).click()
  await expect(draft).toHaveValue('Existing draft\nFix the test.')
  expect(uploads).toHaveLength(1)
  expect(uploads[0].query.cleanup).toBe('false')
  expect(state.sent).toHaveLength(0)
  expect(await page.evaluate(() => stoppedTracks)).toBe(1)
  await page.screenshot({ path: testInfo.outputPath(`dictation-draft-${width}.png`) })
  await page.getByRole('button', { name: 'Send message', exact: true }).click()
  await expect.poll(() => state.sent.length).toBe(1)
})

test('live text appears before stop and the final window replaces rather than duplicates it', async ({ page, context }) => {
  const { state, uploads } = await speechFixture(context)
  await page.goto('/chats/alpha')
  const draft = page.getByRole('textbox', { name: 'Message', exact: true })
  await draft.fill('Existing draft')
  await page.getByRole('button', { name: 'Dictate message', exact: true }).click()
  await page.evaluate(() => feedVoice(3))
  await expect(draft).toHaveValue('Existing draft\nFix the test.')
  await expect(page.getByRole('button', { name: 'Finish recording', exact: true })).toBeVisible()
  expect(uploads.length).toBeGreaterThan(0)
  expect(state.sent).toHaveLength(0)
  await page.getByRole('button', { name: 'Finish recording', exact: true }).click()
  await expect(page.getByRole('button', { name: 'Dictate message', exact: true })).toBeEnabled()
  await expect(draft).toHaveValue('Existing draft\nFix the test.')
})

test('cleanup uses the chat model and original transcript can be recovered', async ({ page, context }) => {
  const { cleanups } = await speechFixture(context, { cleanup: true })
  await page.goto('/chats/alpha')
  await openChatConfiguration(page)
  await expect(page.getByRole('group', { name: 'Dictation', exact: true })).toHaveCount(0)
  await expect(page.getByLabel("Clean up with this chat's model")).toHaveCount(0)
  await closeChatConfiguration(page)
  await page.getByRole('button', { name: 'Dictate message', exact: true }).click()
  await page.getByRole('button', { name: 'Finish recording', exact: true }).click()
  await expect(page.getByRole('textbox', { name: 'Message', exact: true })).toHaveValue('Fix the test.')
  expect(cleanups[0]).toMatchObject({ text: 'um fix the test', model: 'test/model' })
  await page.getByRole('button', { name: 'Use original transcript', exact: true }).click()
  await expect(page.getByRole('textbox', { name: 'Message', exact: true })).toHaveValue('um fix the test')
})

test('dictation preferences live only in Settings and still configure chat recording', async ({ page, context }) => {
  const { uploads, cleanups } = await speechFixture(context, { cleanup: true })
  await page.goto('/settings/workspace')
  const cleanup = page.getByRole('switch', { name: 'Clean up dictation', exact: true })
  const language = page.getByLabel('Dictation language', { exact: true })
  await expect(cleanup).toBeChecked()
  await language.selectOption('de')
  await page.reload()
  await expect(cleanup).toBeChecked()
  await expect(language).toHaveValue('de')
  await page.goto('/chats/alpha')
  await openChatConfiguration(page)
  await expect(page.getByRole('group', { name: 'Dictation', exact: true })).toHaveCount(0)
  await expect(page.getByLabel('Dictation language', { exact: true })).toHaveCount(0)
  await closeChatConfiguration(page)
  await page.getByRole('button', { name: 'Dictate message', exact: true }).click()
  await page.getByRole('button', { name: 'Finish recording', exact: true }).click()
  await expect(page.getByRole('textbox', { name: 'Message', exact: true })).toHaveValue('Fix the test.')
  expect(uploads[0].query.language).toBe('de')
  expect(cleanups).toHaveLength(1)
})

test('cancel discards recording, releases the microphone and preserves the original typed text', async ({ page, context }) => {
  const { uploads } = await speechFixture(context)
  await page.goto('/chats/alpha')
  const draft = page.getByRole('textbox', { name: 'Message', exact: true })
  await draft.fill('Keep this')
  await page.getByRole('button', { name: 'Dictate message', exact: true }).click()
  await page.getByRole('button', { name: 'Cancel dictation', exact: true }).click()
  await expect(draft).toHaveValue('Keep this')
  expect(uploads).toHaveLength(0)
  expect(await page.evaluate(() => stoppedTracks)).toBe(1)
})

test('permission errors do not modify or send the draft', async ({ page, context }) => {
  const { state, uploads } = await speechFixture(context, { denied: true })
  await page.goto('/chats/alpha')
  await page.getByRole('textbox', { name: 'Message', exact: true }).fill('Keep this')
  await page.getByRole('button', { name: 'Dictate message', exact: true }).click()
  await expect(page.getByRole('status').filter({ hasText: 'Microphone permission denied' })).toBeVisible()
  await expect(page.getByRole('textbox', { name: 'Message', exact: true })).toHaveValue('Keep this')
  expect(state.sent).toHaveLength(0)
  expect(uploads).toHaveLength(0)
})

test('transcription failure retains the typed draft and permits a retry', async ({ page, context }) => {
  await speechFixture(context)
  await context.route('**/api/speech/transcribe?*', route => route.fulfill({ status: 502, json: { detail: 'Transcription service unavailable.' } }))
  await page.goto('/chats/alpha')
  const draft = page.getByRole('textbox', { name: 'Message', exact: true })
  await draft.fill('Keep this draft')
  await page.getByRole('button', { name: 'Dictate message', exact: true }).click()
  await page.getByRole('button', { name: 'Finish recording', exact: true }).click()
  await expect(page.getByRole('status').filter({ hasText: 'Transcription service unavailable' })).toBeVisible()
  await expect(draft).toHaveValue('Keep this draft')
  await expect(page.getByRole('button', { name: 'Dictate message', exact: true })).toBeEnabled()
})

test('cancel during transcription ignores a late result and preserves the draft', async ({ page, context }) => {
  await speechFixture(context)
  let release
  const held = new Promise(resolve => { release = resolve })
  await context.route('**/api/speech/transcribe?*', async route => {
    await held
    await route.fulfill({ contentType: 'text/event-stream', body: 'data: {"type":"delta","text":"Late"}\n\ndata: {"type":"done","text":"Late"}\n\n' }).catch(() => {})
  })
  await page.goto('/chats/alpha')
  const draft = page.getByRole('textbox', { name: 'Message', exact: true })
  await draft.fill('Keep this draft')
  await page.getByRole('button', { name: 'Dictate message', exact: true }).click()
  await page.getByRole('button', { name: 'Finish recording', exact: true }).click()
  await expect(page.getByText('Finalizing…', { exact: true })).toBeVisible()
  await page.getByRole('button', { name: 'Cancel dictation', exact: true }).click()
  release()
  await expect(draft).toHaveValue('Keep this draft')
  await expect(page.getByRole('button', { name: 'Dictate message', exact: true })).toBeEnabled()
})

test.describe('real browser audio pipeline', () => {
  test('AudioWorklet sends valid audio and shows text while the microphone is still active', async ({ page, context, browserName }) => {
    test.skip(browserName !== 'chromium', 'Chromium fake microphone device')
    const state = await mockDesign(context)
    await context.grantPermissions(['microphone'])
    let uploads = 0
    await context.route('**/api/speech', route => route.fulfill({ json: { enabled: true } }))
    await context.route('**/api/speech/transcribe?*', route => {
      const audio = route.request().postDataBuffer()
      expect(audio.subarray(0, 4).toString()).toBe('RIFF')
      expect(audio.subarray(8, 12).toString()).toBe('WAVE')
      uploads++
      return route.fulfill({ contentType: 'text/event-stream', body: 'data: {"type":"done","text":"Live microphone text."}\n\n' })
    })
    await page.goto('/chats/alpha')
    await page.getByRole('button', { name: 'Dictate message', exact: true }).click()
    await expect(page.getByRole('textbox', { name: 'Message', exact: true })).toHaveValue('Live microphone text.', { timeout: 15000 })
    await expect(page.getByRole('button', { name: 'Finish recording', exact: true })).toBeVisible()
    expect(uploads).toBeGreaterThan(0)
    expect(state.sent).toHaveLength(0)
    await page.getByRole('button', { name: 'Finish recording', exact: true }).click()
    await expect(page.getByRole('button', { name: 'Dictate message', exact: true })).toBeEnabled()
  })
})

test('navigation releases an active microphone without transcribing into another chat', async ({ page, context }) => {
  const { uploads } = await speechFixture(context)
  await page.goto('/chats/alpha')
  await page.getByRole('button', { name: 'Dictate message', exact: true }).click()
  await page.getByRole('link', { name: /Workflows/ }).first().click()
  await expect.poll(() => page.evaluate(() => stoppedTracks)).toBe(1)
  expect(uploads).toHaveLength(0)
})
