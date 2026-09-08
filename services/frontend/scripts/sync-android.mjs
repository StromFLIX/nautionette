import { copyFile, readFile } from 'node:fs/promises'

// Keep generated Gradle files out of Git while retaining our small native bridge.
// Run after cap add/sync so the activity replacement cannot be overwritten.
const root = new URL('../', import.meta.url)
const config = JSON.parse(await readFile(new URL('capacitor.config.json', root), 'utf8'))
if (config.appId !== 'dev.nautionette.app') throw new Error('Update the native Java package when changing appId.')
for (const name of ['MainActivity.java', 'NautionetteSystemBarsPlugin.java']) {
  await copyFile(new URL(`native/android/${name}`, root), new URL(`android/app/src/main/java/dev/nautionette/app/${name}`, root))
}
