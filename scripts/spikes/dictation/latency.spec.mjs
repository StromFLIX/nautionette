/** Diagnostic spike, not a promise to preserve the current latency bugs.
 * Runs the real composer, recorder and request queue; STT responses are synthetic.
 */
import { createRequire } from 'node:module'
import { performance } from 'node:perf_hooks'
import { mockDesign } from '../../../services/frontend/tests/design-fixture.js'

const require = createRequire(new URL('../../../services/frontend/package.json', import.meta.url))
const { test, expect } = require('@playwright/test')
const sleep = ms => new Promise(resolve => setTimeout(resolve, ms))
const reply = (route, text) => route.fulfill({
  contentType: 'text/event-stream',
  body: `data: ${JSON.stringify({ type: 'done', text })}\n\n`
})

async function fixture (context, handler) {
  await mockDesign(context)
  await context.addInitScript(() => {
    Object.defineProperty(navigator, 'mediaDevices', { value: {
      async getUserMedia () { return { getTracks: () => [{ stop () {} }] } }
    } })
    window.AudioContext = class {
      constructor () { this.sampleRate = 16000; this.destination = {} }
      async resume () {}
      async close () {}
      createGain () { return { gain: { value: 1 }, connect () {}, disconnect () {} } }
      createMediaStreamSource () {
        return { disconnect () {}, connect: node => {
          window.feedVoice = (seconds, amplitude = 0.1) => {
            for (let n = 0; n < seconds * 4; n++) {
              node.onaudioprocess?.({ inputBuffer: { getChannelData: () => new Float32Array(4000).fill(amplitude) } })
            }
          }
        } }
      }
      createScriptProcessor () { return { connect () {}, disconnect () {}, onaudioprocess: null } }
    }
  })
  await context.route('**/api/speech', route => route.fulfill({ json: { enabled: true } }))
  const uploads = []
  await context.route('**/api/speech/transcribe?*', async route => {
    const bytes = route.request().postDataBuffer()
    const audioSeconds = bytes.readUInt32LE(40) / (bytes.readUInt32LE(24) * 2)
    uploads.push({ bytes, audioSeconds })
    await handler(route, uploads.length)
  })
  return uploads
}

async function begin (page) {
  await page.goto('/chats/alpha')
  await page.getByRole('button', { name: 'Dictate message', exact: true }).click()
  await expect(page.getByRole('button', { name: 'Finish recording', exact: true })).toBeVisible()
}
const draft = page => page.getByRole('textbox', { name: 'Message', exact: true })
const ready = page => page.getByRole('button', { name: 'Dictate message', exact: true })
const stop = page => page.getByRole('button', { name: 'Finish recording', exact: true }).click()

function report (scenario, uploads, extra = {}) {
  console.log(JSON.stringify({ scenario, uploaded_audio_seconds: uploads.map(x => x.audioSeconds), ...extra }))
}

test('Stop retranscribes an already completed, byte-identical partial', async ({ page, context }) => {
  const uploads = await fixture(context, route => reply(route, 'Already recognized.'))
  await begin(page)
  await page.evaluate(() => feedVoice(2))
  await expect(draft(page)).toHaveValue('Already recognized.')
  await stop(page)
  await expect(ready(page)).toBeEnabled()
  expect(uploads).toHaveLength(2)
  expect(uploads[0].bytes.equals(uploads[1].bytes)).toBe(true)
  report('identical-final-upload', uploads)
})

test('Stop after a finalized pause still submits a new silence-only window', async ({ page, context }) => {
  const uploads = await fixture(context, (route, n) => reply(route, n === 3 ? '' : 'Completed phrase.'))
  await begin(page)
  await page.evaluate(() => feedVoice(2))
  await expect(draft(page)).toHaveValue('Completed phrase.')
  await page.evaluate(() => feedVoice(0.75, 0))
  await expect.poll(() => uploads.length).toBe(2)
  // Wait for the actual queue to publish the finalized phrase before the next input.
  await expect.poll(() => page.evaluate(() => {
    const component = document.querySelector('.voice-control').__vueParentComponent
    return component?.setupState?.committed
  })).toBe('Completed phrase.')
  await page.evaluate(() => feedVoice(0.25, 0))
  await stop(page)
  await expect(ready(page)).toBeEnabled()
  expect(uploads).toHaveLength(3)
  expect(uploads[2].audioSeconds).toBe(0.25)
  expect(uploads[2].bytes.subarray(44).every(byte => byte === 0)).toBe(true)
  await expect(draft(page)).toHaveValue('Completed phrase.')
  report('silence-tail-after-pause', uploads)
})

test('Stop waits for both an in-flight partial and a queued final decode', async ({ page, context }) => {
  let release
  const held = new Promise(resolve => { release = resolve })
  const uploads = await fixture(context, async (route, n) => {
    if (n === 1) await held
    else await sleep(300)
    await reply(route, n === 1 ? 'First words.' : 'First words and last word.')
  })
  await begin(page)
  await page.evaluate(() => feedVoice(2))
  await expect.poll(() => uploads.length).toBe(1)
  await page.evaluate(() => feedVoice(0.25))
  await stop(page)
  const started = performance.now()
  await expect(ready(page)).toBeDisabled()
  expect(uploads).toHaveLength(1)
  // Inject known residual STT/network delay; do not claim a model benchmark.
  await sleep(300)
  release()
  await expect(ready(page)).toBeEnabled()
  const stopWaitMs = Math.round(performance.now() - started)
  await expect(draft(page)).toHaveValue('First words and last word.')
  expect(uploads).toHaveLength(2)
  expect(stopWaitMs).toBeGreaterThanOrEqual(600)
  report('in-flight-plus-final', uploads, { injected_delay_ms: 600, observed_wait_ms: stopWaitMs })
})

test('a continuous 11.75-second window repeatedly decodes its entire prefix', async ({ page, context }) => {
  const uploads = await fixture(context, route => reply(route, 'Continuous speech.'))
  await begin(page)
  for (let n = 0; n < 5; n++) {
    await page.evaluate(() => feedVoice(2))
    await expect.poll(() => uploads.length).toBe(n + 1)
    // The next chunk must follow the completion of the previous request, not be coalesced.
    await page.waitForFunction(() => !document.querySelector('.voice-control').__vueParentComponent.setupState.working)
  }
  await page.evaluate(() => feedVoice(1.75))
  await stop(page)
  await expect(ready(page)).toBeEnabled()
  expect(uploads.map(x => x.audioSeconds)).toEqual([2, 4, 6, 8, 10, 11.75])
  report('growing-window-redecode', uploads, {
    unique_audio_seconds: 11.75,
    total_uploaded_audio_seconds: uploads.reduce((sum, x) => sum + x.audioSeconds, 0)
  })
})
