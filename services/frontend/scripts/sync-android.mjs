import { copyFile, readFile, writeFile } from 'node:fs/promises'

// Keep generated Gradle files out of Git while retaining our small native bridge.
// Run after cap add/sync so the activity replacement cannot be overwritten.
const root = new URL('../', import.meta.url)
const config = JSON.parse(await readFile(new URL('capacitor.config.json', root), 'utf8'))
if (config.appId !== 'dev.nautionette.app') throw new Error('Update the native Java package when changing appId.')
for (const name of ['MainActivity.java', 'NautionetteSystemBarsPlugin.java', 'NautionetteDownloadsPlugin.java']) {
  await copyFile(new URL(`native/android/${name}`, root), new URL(`android/app/src/main/java/dev/nautionette/app/${name}`, root))
}
// Capacitor's WebChromeClient requests runtime microphone permission for getUserMedia.
const manifestPath = new URL('android/app/src/main/AndroidManifest.xml', root)
const manifest = await readFile(manifestPath, 'utf8')
if (!manifest.includes('android.permission.RECORD_AUDIO')) {
  await writeFile(manifestPath, manifest.replace('</manifest>', '    <uses-permission android:name="android.permission.RECORD_AUDIO" />\n</manifest>'))
}
