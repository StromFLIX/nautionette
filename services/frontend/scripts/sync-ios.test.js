import { test } from 'node:test'
import assert from 'node:assert/strict'
import { execFileSync } from 'node:child_process'
import { configureInfo, configureProject } from './sync-ios.mjs'
import { brand } from './native-brand.mjs'

test('iOS microphone permission and privacy resource survive repeat syncs', () => {
  const template = execFileSync('tar', ['-xOf', 'node_modules/@capacitor/cli/assets/ios-spm-template.tar.gz', 'App/App.xcodeproj/project.pbxproj'], { encoding: 'utf8' })
  const updated = configureProject(template)
  assert.match(updated, /PrivacyInfo.xcprivacy in Resources/)
  assert.equal(configureProject(updated), updated)
  const info = configureInfo('<plist><dict><key>Example</key><string>value</string></dict></plist>')
  assert.match(info, /NSMicrophoneUsageDescription/)
  assert.ok(info.includes(`<key>CFBundleDisplayName</key>\n\t<string>${brand.name}</string>`))
  assert.ok(info.includes(`Record dictation for your ${brand.name} instance.`))
  assert.equal(configureInfo(info), info)
  assert.throws(() => configureProject('changed template'), /template changed/)
  assert.throws(() => configureInfo('invalid'), /dictionary/)
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
