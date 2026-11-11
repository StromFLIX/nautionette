import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync, mkdtempSync, mkdirSync, copyFileSync, rmSync } from 'node:fs'
import { execFileSync } from 'node:child_process'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { brand, configureAndroidStrings } from './native-brand.mjs'

const frontend = fileURLToPath(new URL('../', import.meta.url))

test('Android changes display resources, never package or URI identifiers', () => {
  const technical = '<string name="package_name">dev.nautionette.app</string><string name="custom_url_scheme">nautionette</string>'
  const strings = configureAndroidStrings(`<resources>${technical}<string name="app_name">Nautionette</string><string name="title_activity_main">Old name</string></resources>`)
  assert.ok(strings.includes(technical))
  assert.ok(strings.includes(`<string name="app_name">${brand.name}</string>`))
  assert.ok(strings.includes(`<string name="title_activity_main">${brand.name}</string>`))
  assert.equal(configureAndroidStrings(strings), strings)
})

test('launcher exports have platform dimensions and Electron uses the larger desktop master', () => {
  for (const [name, size] of [['app-icon.png', 1024], ['desktop-icon.png', 1024], ['ic_launcher.png', 192], ['ic_launcher_foreground.png', 432]]) {
    const png = readFileSync(join(frontend, 'native/icons', name))
    assert.equal(png.subarray(1, 4).toString(), 'PNG')
    assert.equal(png.readUInt32BE(16), size)
    assert.equal(png.readUInt32BE(20), size)
  }
  for (const path of ['build/icon.png', 'desktop/icon.png']) {
    assert.deepEqual(readFileSync(join(frontend, path)), readFileSync(join(frontend, 'native/icons/desktop-icon.png')))
  }
  assert.notDeepEqual(readFileSync(join(frontend, 'desktop/icon.png')), readFileSync(join(frontend, 'native/icons/app-icon.png')))
})

test('iOS sync installs the approved icon into the current Capacitor template', () => {
  const root = mkdtempSync(join(tmpdir(), 'denkzeug-ios-'))
  try {
    const ios = join(root, 'ios')
    mkdirSync(ios)
    execFileSync('tar', ['-xf', join(frontend, 'node_modules/@capacitor/cli/assets/ios-spm-template.tar.gz'), '-C', ios])
    for (const source of ['ios/PrivacyInfo.xcprivacy', 'ios/BridgeViewController.swift', 'icons/app-icon.png']) {
      mkdirSync(join(root, 'native', source.split('/')[0]), { recursive: true })
      copyFileSync(join(frontend, 'native', source), join(root, 'native', source))
    }
    const sync = () => execFileSync(process.execPath, [join(frontend, 'scripts/sync-ios.mjs')], { cwd: root })
    sync()
    assert.deepEqual(readFileSync(join(ios, 'App/App/BridgeViewController.swift')), readFileSync(join(frontend, 'native/ios/BridgeViewController.swift')))
    assert.match(readFileSync(join(ios, 'App/App/Base.lproj/Main.storyboard'), 'utf8'), /customClass="BridgeViewController"/)
    const slot = join(ios, 'App/App/Assets.xcassets/AppIcon.appiconset')
    const contents = JSON.parse(readFileSync(join(slot, 'Contents.json'), 'utf8'))
    assert.equal(contents.images[0].filename, 'AppIcon-512@2x.png')
    assert.deepEqual(readFileSync(join(slot, contents.images[0].filename)), readFileSync(join(frontend, 'native/icons/app-icon.png')))
    const info = readFileSync(join(ios, 'App/App/Info.plist'), 'utf8')
    assert.ok(info.includes(`<string>${brand.name}</string>`))
    sync()
    assert.equal(readFileSync(join(ios, 'App/App/Info.plist'), 'utf8'), info)
  } finally {
    rmSync(root, { recursive: true, force: true })
  }
})
