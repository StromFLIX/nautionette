// Display names must not silently move Chromium credentials or preferences.
const { existsSync } = require('node:fs')
const { basename, join } = require('node:path')
const brand = require('./brand.json')
const LEGACY_NAMES = ['nautionette-frontend', 'Nautionette']

function existingDataPath (appData, current, exists = existsSync) {
  // Electron/package variants have used either spelling. Preserve its current
  // legacy choice first, then an existing installation, never a new brand folder.
  if (LEGACY_NAMES.includes(basename(current)) || basename(current) !== brand.name) return current
  for (const name of LEGACY_NAMES) {
    const candidate = join(appData, name)
    if (exists(candidate)) return candidate
  }
  return join(appData, LEGACY_NAMES[0])
}
module.exports = { existingDataPath }
