import { copyFile, readFile, writeFile } from 'node:fs/promises'

// Keep generated Gradle files out of Git while retaining our small native bridge.
// Run after cap add/sync so the activity replacement cannot be overwritten.
const root = new URL('../', import.meta.url)
const config = JSON.parse(await readFile(new URL('capacitor.config.json', root), 'utf8'))
if (config.appId !== 'dev.nautionette.app') throw new Error('Update the native Java package when changing appId.')
for (const name of ['MainActivity.java', 'NautionetteSystemBarsPlugin.java', 'NautionetteDownloadsPlugin.java']) {
  await copyFile(new URL(`native/android/${name}`, root), new URL(`android/app/src/main/java/dev/nautionette/app/${name}`, root))
}
// BridgeWebChromeClient requests BOTH permissions for AUDIO_CAPTURE and denies
// getUserMedia if either is missing, even when the user allows RECORD_AUDIO.
// MODIFY_AUDIO_SETTINGS is a normal (install-time) permission, not another prompt.
const manifestPath = new URL('android/app/src/main/AndroidManifest.xml', root)
let manifest = await readFile(manifestPath, 'utf8')
for (const permission of ['RECORD_AUDIO', 'MODIFY_AUDIO_SETTINGS']) {
  if (!manifest.includes(`android.permission.${permission}`)) {
    manifest = manifest.replace('</manifest>', `    <uses-permission android:name="android.permission.${permission}" />\n</manifest>`)
  }
}
await writeFile(manifestPath, manifest)
