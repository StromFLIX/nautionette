import { test } from 'node:test'
import assert from 'node:assert/strict'
import { resolve } from 'node:path'
import security from '../desktop/security.cjs'
import { windowChrome, OVERLAY_HEIGHT } from '../desktop/window-chrome.cjs'
import { readFileSync } from 'node:fs'
import vm from 'node:vm'

const { trustedRenderer, externalURL, assetPath } = security

test('Windows and Linux retain native controls without title/icon chrome; macOS is unchanged', () => {
  for (const platform of ['win32', 'linux']) {
    const options = windowChrome(platform)
    assert.equal(options.titleBarStyle, 'hidden')
    assert.equal(options.titleBarOverlay.height, 32)
    assert.notEqual(options.frame, false) // Never replace native caption buttons with web buttons.
  }
  assert.deepEqual(windowChrome('darwin'), {})
  const app = readFileSync(new URL('../src/App.vue', import.meta.url), 'utf8')
  assert.ok(app.includes(`padding-top: ${OVERLAY_HEIGHT}px`))
  assert.ok(app.includes(`height: ${OVERLAY_HEIGHT}px`))
})

test('sandboxed preload exposes only read-only chrome state and handles fullscreen', () => {
  const source = readFileSync(new URL('../desktop/preload.cjs', import.meta.url), 'utf8')
  for (const platform of ['win32', 'linux', 'darwin']) {
    let bridge, listener, fullscreenClass
    vm.runInNewContext(source, {
      process: { platform },
      require: name => {
        assert.equal(name, 'electron')
        return {
          contextBridge: { exposeInMainWorld: (_name, value) => { bridge = value } },
          ipcRenderer: { on: (channel, callback) => {
            assert.equal(channel, 'window:fullscreen'); listener = callback
          } }
        }
      },
      document: { documentElement: { classList: { toggle: (name, value) => {
        assert.equal(name, 'desktop-fullscreen'); fullscreenClass = value
      } } } }
    })
    assert.equal(bridge.windowControlsOverlay, platform !== 'darwin')
    assert.deepEqual(Object.keys(bridge).sort(), ['oauth', 'windowControlsOverlay'])
    listener({}, true)
    assert.equal(fullscreenClass, true)
    listener({}, false)
    assert.equal(fullscreenClass, false)
    listener({}, 'true')
    assert.equal(fullscreenClass, false)
  }
})

test('desktop only trusts its bundled origin and opens web URLs externally', () => {
  assert.equal(trustedRenderer('capacitor://localhost/'), true)
  for (const url of ['https://localhost/', 'capacitor://evil/', 'capacitor://localhost.evil/', 'file:///etc/passwd', 'garbage']) {
    assert.equal(trustedRenderer(url), false)
  }
  assert.equal(externalURL('https://example.com'), true)
  for (const url of ['javascript:alert(1)', 'file:///etc/passwd', 'data:text/html,test', 'ms-settings:', 'garbage']) {
    assert.equal(externalURL(url), false)
  }
})

test('desktop asset handler never reads outside dist', () => {
  const root = resolve('/app/dist')
  assert.equal(assetPath(root, 'capacitor://localhost/'), resolve(root, 'index.html'))
  assert.equal(assetPath(root, 'capacitor://localhost/assets/app.js'), resolve(root, 'assets/app.js'))
  for (const url of ['capacitor://localhost/..%2fsecret', 'capacitor://localhost/%00', 'capacitor://localhost/..%5csecret', 'https://example.com/']) {
    assert.equal(assetPath(root, url), null)
  }
})
