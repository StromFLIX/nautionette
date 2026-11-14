// Display names must not silently move Chromium credentials or preferences.
const { existsSync, readdirSync } = require('node:fs')
const { basename, join } = require('node:path')
const brand = require('./brand.json')
const LEGACY_NAMES = ['nautionette-frontend', 'Nautionette']

function existingDataPath (appData, current, exists = existsSync) {
  // Never move an existing/custom profile: credentials and preferences live here.
  // Only genuinely new installations use the canonical brand directory.
  const hasData = candidate => {
    if (!exists(candidate)) return false
    // Electron's getPath('userData') can create an empty default directory.
    // That is not an existing profile. On inspection errors, preserve it.
    try { return readdirSync(candidate).length > 0 } catch { return true }
  }
  if (!LEGACY_NAMES.includes(basename(current)) && basename(current) !== brand.name) return current
  if (LEGACY_NAMES.includes(basename(current)) && hasData(current)) return current
  for (const name of LEGACY_NAMES) {
    const candidate = join(appData, name)
    if (hasData(candidate)) return candidate
  }
  return join(appData, brand.name)
}
module.exports = { existingDataPath }
