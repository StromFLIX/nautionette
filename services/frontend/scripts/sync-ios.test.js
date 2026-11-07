import { test } from 'node:test'
import assert from 'node:assert/strict'
import { execFileSync } from 'node:child_process'
import { configureInfo, configureProject } from './sync-ios.mjs'

test('iOS microphone permission and privacy resource survive repeat syncs', () => {
  const template = execFileSync('tar', ['-xOf', 'node_modules/@capacitor/cli/assets/ios-spm-template.tar.gz', 'App/App.xcodeproj/project.pbxproj'], { encoding: 'utf8' })
  const updated = configureProject(template)
  assert.match(updated, /PrivacyInfo.xcprivacy in Resources/)
  assert.equal(configureProject(updated), updated)
  const info = configureInfo('<plist><dict><key>Example</key><string>value</string></dict></plist>')
  assert.match(info, /NSMicrophoneUsageDescription/)
  assert.equal(configureInfo(info), info)
  assert.throws(() => configureProject('changed template'), /template changed/)
  assert.throws(() => configureInfo('invalid'), /dictionary/)
})
