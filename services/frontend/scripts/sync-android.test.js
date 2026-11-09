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
      for (const dir of ['scripts', 'desktop', 'native/android', 'native/icons', 'android/app/src/main/res/values', 'android/app/src/main/res/mipmap-mdpi', native]) await mkdir(join(root, dir), { recursive: true })
      await copyFile(new URL('./sync-android.mjs', import.meta.url), join(root, 'scripts/sync-android.mjs'))
      await copyFile(new URL('./native-brand.mjs', import.meta.url), join(root, 'scripts/native-brand.mjs'))
      await copyFile(new URL('../desktop/brand.json', import.meta.url), join(root, 'desktop/brand.json'))
      const brand = JSON.parse(await readFile(join(root, 'desktop/brand.json'), 'utf8'))
      const stringsPath = join(root, 'android/app/src/main/res/values/strings.xml')
      await writeFile(stringsPath, '<resources><string name="app_name">Nautionette</string><string name="title_activity_main">Nautionette</string><string name="custom_url_scheme">dev.nautionette.app</string></resources>')
      await writeFile(join(root, 'capacitor.config.json'), JSON.stringify({ appId: 'dev.nautionette.app' }))
      for (const name of ['ic_launcher.png', 'ic_launcher_foreground.png']) await copyFile(new URL(`../native/icons/${name}`, import.meta.url), join(root, 'native/icons', name))
      await writeFile(join(root, 'android/app/src/main/res/mipmap-mdpi/ic_launcher.png'), 'stock-icon')
      await writeFile(join(root, 'android/app/src/main/res/mipmap-mdpi/custom.png'), 'custom-icon')
      const names = ['MainActivity.java', 'NautionetteSystemBarsPlugin.java', 'NautionetteDownloadsPlugin.java']
      for (const name of names) await copyFile(new URL(`../native/android/${name}`, import.meta.url), join(root, 'native/android', name))
      const manifestPath = join(root, 'android/app/src/main/AndroidManifest.xml')
      const application = '    <application android:label="@string/app_name" />'
      await writeFile(manifestPath, `<manifest xmlns:android="http://schemas.android.com/apk/res/android">\n${application}\n    <uses-permission android:name="android.permission.INTERNET" />\n${existing.map(p => `    <uses-permission android:name="android.permission.${p}" />\n`).join('')}</manifest>\n`)
      const sync = () => execFileSync(process.execPath, [join(root, 'scripts/sync-android.mjs')])
      sync()
      const manifest = await readFile(manifestPath, 'utf8')
      for (const permission of ['RECORD_AUDIO', 'MODIFY_AUDIO_SETTINGS', 'INTERNET']) {
        assert.equal(manifest.split(`android.permission.${permission}`).length - 1, 1)
      }
      assert.ok(manifest.includes(application))
      const strings = await readFile(stringsPath, 'utf8')
      assert.ok(strings.includes(`<string name="app_name">${brand.name}</string>`))
      assert.ok(strings.includes(`<string name="title_activity_main">${brand.name}</string>`))
      assert.ok(strings.includes('<string name="custom_url_scheme">dev.nautionette.app</string>'))
      await assert.rejects(readFile(join(root, 'android/app/src/main/res/mipmap-mdpi/ic_launcher.png')), { code: 'ENOENT' })
      assert.equal(await readFile(join(root, 'android/app/src/main/res/mipmap-mdpi/custom.png'), 'utf8'), 'custom-icon')
      for (const name of ['ic_launcher', 'ic_launcher_round', 'ic_launcher_foreground']) {
        const source = name === 'ic_launcher_foreground' ? 'ic_launcher_foreground.png' : 'ic_launcher.png'
        assert.deepEqual(await readFile(join(root, `android/app/src/main/res/mipmap-xxxhdpi/${name}.png`)), await readFile(join(root, `native/icons/${source}`)))
      }
      for (const name of names) assert.equal(await readFile(join(root, native, name), 'utf8'), await readFile(new URL(`../native/android/${name}`, import.meta.url), 'utf8'))
      sync()
      assert.equal(await readFile(manifestPath, 'utf8'), manifest, 'repeated sync must not duplicate permissions')
      assert.equal(await readFile(stringsPath, 'utf8'), strings)
    } finally {
      await rm(root, { recursive: true, force: true })
    }
  })
}
