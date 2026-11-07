import test from 'node:test'
import assert from 'node:assert/strict'
import { mkdtemp, mkdir, copyFile, readFile, writeFile, rm } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { execFileSync } from 'node:child_process'

for (const existing of [[], ['RECORD_AUDIO'], ['MODIFY_AUDIO_SETTINGS'], ['RECORD_AUDIO', 'MODIFY_AUDIO_SETTINGS']]) {
  test(`Android sync supplies both WebView audio permissions with ${existing.join(', ') || 'no existing permissions'}`, async () => {
    const root = await mkdtemp(join(tmpdir(), 'nautionette-android-'))
    try {
      const native = 'android/app/src/main/java/dev/nautionette/app'
      for (const dir of ['scripts', 'native/android', native]) await mkdir(join(root, dir), { recursive: true })
      await copyFile(new URL('./sync-android.mjs', import.meta.url), join(root, 'scripts/sync-android.mjs'))
      await writeFile(join(root, 'capacitor.config.json'), JSON.stringify({ appId: 'dev.nautionette.app' }))
      const names = ['MainActivity.java', 'NautionetteSystemBarsPlugin.java', 'NautionetteDownloadsPlugin.java']
      for (const name of names) await copyFile(new URL(`../native/android/${name}`, import.meta.url), join(root, 'native/android', name))
      const manifestPath = join(root, 'android/app/src/main/AndroidManifest.xml')
      const application = '    <application android:label="Nautionette" />'
      await writeFile(manifestPath, `<manifest xmlns:android="http://schemas.android.com/apk/res/android">\n${application}\n    <uses-permission android:name="android.permission.INTERNET" />\n${existing.map(p => `    <uses-permission android:name="android.permission.${p}" />\n`).join('')}</manifest>\n`)
      const sync = () => execFileSync(process.execPath, [join(root, 'scripts/sync-android.mjs')])
      sync()
      const manifest = await readFile(manifestPath, 'utf8')
      for (const permission of ['RECORD_AUDIO', 'MODIFY_AUDIO_SETTINGS', 'INTERNET']) {
        assert.equal(manifest.split(`android.permission.${permission}`).length - 1, 1)
      }
      assert.ok(manifest.includes(application))
      for (const name of names) assert.equal(await readFile(join(root, native, name), 'utf8'), await readFile(new URL(`../native/android/${name}`, import.meta.url), 'utf8'))
      sync()
      assert.equal(await readFile(manifestPath, 'utf8'), manifest, 'repeated sync must not duplicate permissions')
    } finally {
      await rm(root, { recursive: true, force: true })
    }
  })
}
