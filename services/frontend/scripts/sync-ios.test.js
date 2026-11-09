import { test } from 'node:test'
import assert from 'node:assert/strict'
import { execFileSync } from 'node:child_process'
import { readFileSync } from 'node:fs'
import { configureInfo, configureProject, configureStoryboard } from './sync-ios.mjs'
import { brand } from './native-brand.mjs'

test('iOS microphone permission and privacy resource survive repeat syncs', () => {
  const template = execFileSync('tar', ['-xOf', 'node_modules/@capacitor/cli/assets/ios-spm-template.tar.gz', 'App/App.xcodeproj/project.pbxproj'], { encoding: 'utf8' })
  const updated = configureProject(template)
  assert.match(updated, /PrivacyInfo.xcprivacy in Resources/)
  assert.match(updated, /BridgeViewController.swift in Sources/)
  assert.match(updated, /lastKnownFileType = sourcecode.swift; path = BridgeViewController.swift;/)
  assert.equal(configureProject(updated), updated)
  const info = configureInfo('<plist><dict><key>Example</key><string>value</string></dict></plist>')
  assert.match(info, /NSMicrophoneUsageDescription/)
  assert.ok(info.includes(`<key>CFBundleDisplayName</key>\n\t<string>${brand.name}</string>`))
  assert.ok(info.includes(`Record dictation for your ${brand.name} instance.`))
  assert.equal(configureInfo(info), info)
  assert.throws(() => configureProject('changed template'), /template changed/)
  assert.throws(() => configureInfo('invalid'), /dictionary/)
})

test('iOS uses the native interactive back/forward history gesture after every sync', () => {
  const template = execFileSync('tar', ['-xOf', 'node_modules/@capacitor/cli/assets/ios-spm-template.tar.gz', 'App/App/Base.lproj/Main.storyboard'], { encoding: 'utf8' })
  const updated = configureStoryboard(template)
  assert.match(updated, /customClass="BridgeViewController" customModule="App" customModuleProvider="target"/)
  assert.equal(configureStoryboard(updated), updated)
  assert.throws(() => configureStoryboard('changed template'), /template changed/)
  const controller = readFileSync(new URL('../native/ios/BridgeViewController.swift', import.meta.url), 'utf8')
  assert.match(controller, /override func capacitorDidLoad\(\)/)
  assert.match(controller, /allowsBackForwardNavigationGestures = true/)
})

test('Android installs the official Capacitor app plugin for system back gestures', () => {
  const pkg = JSON.parse(readFileSync(new URL('../package.json', import.meta.url), 'utf8'))
  assert.ok(pkg.dependencies['@capacitor/app'])
})

test('iOS existing display copy updates without changing bundle IDs or URL schemes', () => {
  const ids = '<key>CFBundleIdentifier</key><string>dev.nautionette.app</string><key>CFBundleURLSchemes</key><array><string>nautionette</string></array>'
  const result = configureInfo(`<plist><dict>${ids}<key>CFBundleDisplayName</key><string>Nautionette</string><key>NSMicrophoneUsageDescription</key><string>Record dictation for your Nautionette instance.</string></dict></plist>`)
  assert.ok(result.includes(ids))
  assert.ok(result.includes(`<string>${brand.name}</string>`))
  assert.ok(result.includes(`Record dictation for your ${brand.name} instance.`))
  assert.ok(!result.includes('Nautionette'))
  assert.equal(configureInfo(result), result)
})
