import { computed, reactive, ref, watch } from 'vue'
import { Dark, setCssVar } from 'quasar'
import { cssTokens, resolveTheme, themeById, validToken } from './themes'
import { PREFERENCES_KEY, preferenceDefaults, sanitizePreferences, validPreference, workspaceDefaults } from './preferences-schema'
import { compileSkinCss, installSkin } from './skins'

export const preferenceError = ref('')
function load () {
  try {
    const saved = localStorage.getItem(PREFERENCES_KEY)
    if (saved) return sanitizePreferences(JSON.parse(saved))
    // Carry existing list preferences forward without removing the legacy keys.
    const legacy = {}
    for (const [key, oldKey] of [['sideWidth', 'sideWidth'], ['chatGroupBy', 'chatGroupBy'], ['chatActiveMinutes', 'chatActiveMinutes']]) {
      const raw = localStorage.getItem(`nautionette.${oldKey}`)
      if (raw !== null) legacy[key] = key === 'chatGroupBy' ? raw : Number(raw)
    }
    return sanitizePreferences(legacy)
  } catch {
    preferenceError.value = 'Preferences could not be loaded. Defaults are in use.'
    return preferenceDefaults()
  }
}

export const preferences = reactive(load())
export const reducedMotion = () => preferences.motion === 'reduced' || window.matchMedia('(prefers-reduced-motion: reduce)').matches
export const currentSkin = computed(() => preferences.skins.find(skin => skin.id === preferences.skin) || null)
export const currentDesignKey = computed(() => currentSkin.value ? `skin:${currentSkin.value.id}` : preferences.theme)
export const currentOverrides = computed(() => preferences.overrides[currentDesignKey.value] || {})
const effectiveOverrides = computed(() => ({ ...currentSkin.value?.tokens, ...currentOverrides.value }))
export const currentTokens = computed(() => resolveTheme(currentSkin.value?.base || preferences.theme, effectiveOverrides.value))

export function setToken (key, value) {
  if (!validToken(key, value)) return false
  preferences.overrides[currentDesignKey.value] = { ...currentOverrides.value, [key]: value }
  return true
}
export function resetToken (key) {
  const next = { ...currentOverrides.value }
  delete next[key]
  preferences.overrides[currentDesignKey.value] = next
}
export function resetTheme () { preferences.overrides[currentDesignKey.value] = {} }
export function resetWorkspace () { Object.assign(preferences, workspaceDefaults()) }
export function setPreference (key, value) {
  if (validPreference(key, value)) preferences[key] = value
}
export function applyImportedTheme ({ theme, overrides }) {
  // A single patch, already validated by importTheme, so an invalid file is never partially applied.
  Object.assign(preferences, { theme, skin: '', overrides: { ...preferences.overrides, [theme]: overrides } })
}
export function applyImportedSkin (skin) {
  const skins = installSkin(preferences.skins, skin)
  const overrides = { ...preferences.overrides }
  delete overrides[`skin:${skin.id}`]
  Object.assign(preferences, { skins, skin: skin.id, theme: skin.base, overrides })
}
export function chooseSkin (skin) {
  Object.assign(preferences, { skin: skin.id, theme: skin.base })
}
export function removeSkin (id) {
  const overrides = { ...preferences.overrides }
  delete overrides[`skin:${id}`]
  Object.assign(preferences, { skins: preferences.skins.filter(skin => skin.id !== id), overrides,
    skin: preferences.skin === id ? '' : preferences.skin })
}
export function recoverAppearance () {
  Object.assign(preferences, { skin: '', theme: 'orbit', overrides: { ...preferences.overrides, orbit: {} } })
  // Immediate removal, even before Vue flushes or if storage is unavailable.
  apply()
}
let skinStyle
let appliedCss = ''
function apply () {
  const root = document.documentElement
  const theme = themeById(currentSkin.value?.base || preferences.theme)
  for (const [key, value] of Object.entries(cssTokens(theme.id, effectiveOverrides.value))) root.style.setProperty(key, value)
  root.dataset.skin = currentSkin.value?.id || ''
  const css = activeSkinCss.value
  if (css !== appliedCss) {
    skinStyle?.remove()
    skinStyle = null
    if (css) {
      skinStyle = document.createElement('style')
      skinStyle.id = 'nautionette-skin'
      skinStyle.textContent = css
      document.head.append(skinStyle)
    }
    appliedCss = css
  }
  root.style.fontSize = `${preferences.interfaceSize}%`
  root.style.colorScheme = theme.mode
  root.dataset.theme = theme.id
  root.dataset.colorScheme = theme.mode
  root.dataset.density = preferences.density
  root.dataset.motion = preferences.motion
  for (const key of ['chatListAnimation', 'messageWindowAnimation', 'toolIndicatorAnimation']) {
    root.dataset[key] = String(preferences[key])
  }
  root.dataset.codeWrap = String(preferences.codeWrap)
  Dark.set(theme.mode === 'dark')
  const tokens = currentTokens.value
  for (const [key, token] of Object.entries({ primary: 'accent', secondary: 'flow-activity', positive: 'success', warning: 'warning', negative: 'danger', dark: 'surface-panel' })) {
    setCssVar(key, tokens[token])
  }
  document.querySelector('meta[name="theme-color"]')?.setAttribute('content', tokens['surface-app'])
}

const systemReducedMotion = ref(window.matchMedia('(prefers-reduced-motion: reduce)').matches)
const activeSkinCss = computed(() => currentSkin.value
  ? compileSkinCss(currentSkin.value, preferences.motion === 'reduced' || systemReducedMotion.value) : '')

/** Called after Quasar is installed, before the first component is mounted. */
export function startPreferences () {
  // Works even when a pack hides the entire interface; keep the library intact.
  const safe = new URL(window.location.href).searchParams.get('safe-appearance') === '1'
  if (safe) recoverAppearance()
  apply()
  if (safe) {
    try { localStorage.setItem(PREFERENCES_KEY, JSON.stringify(preferences)) } catch { preferenceError.value = 'Storage is unavailable. Recovery applies for this session only.' }
  }
  const motion = window.matchMedia('(prefers-reduced-motion: reduce)')
  const motionChanged = event => { systemReducedMotion.value = event.matches; apply() }
  motion.addEventListener('change', motionChanged)
  const recover = event => {
    if ((event.ctrlKey || event.metaKey) && event.altKey && event.code === 'Digit0') {
      event.preventDefault()
      recoverAppearance()
    }
  }
  window.addEventListener('keydown', recover, true)
  let synchronized = JSON.stringify(preferences)
  const stop = watch(preferences, () => {
    apply()
    const current = JSON.stringify(preferences)
    // Remote updates are already persisted. Echoing them can roll another tab
    // back to an older snapshot while its storage events are still queued.
    if (current === synchronized) return
    try {
      const previous = JSON.parse(synchronized)
      const saved = localStorage.getItem(PREFERENCES_KEY)
      let merged = { ...previous }
      try { if (saved) merged = sanitizePreferences(JSON.parse(saved)) }
      catch { /* Allow a local edit to replace malformed stored preferences. */ }
      // Only save fields edited here; a background tab may not yet have received
      // another tab's newer grouping/filter when it changes an unrelated setting.
      const local = JSON.parse(current)
      for (const [key, value] of Object.entries(local)) {
        if (JSON.stringify(value) !== JSON.stringify(previous[key])) merged[key] = value
      }
      // Base theme and active pack are one selection, not independent settings.
      if (local.theme !== previous.theme || local.skin !== previous.skin) {
        merged.theme = local.theme
        merged.skin = local.skin
      }
      const validated = sanitizePreferences(merged)
      const serialized = JSON.stringify(validated)
      localStorage.setItem(PREFERENCES_KEY, serialized)
      synchronized = serialized
      Object.assign(preferences, validated)
      preferenceError.value = ''
    } catch {
      preferenceError.value = 'Storage is unavailable. These changes apply for this session only.'
    }
  }, { deep: true, flush: 'post' })
  const sync = event => {
    if (event.storageArea !== localStorage || (event.key !== PREFERENCES_KEY && event.key !== null)) return
    try {
      // event.newValue can be obsolete by the time a suspended tab handles it.
      const saved = localStorage.getItem(PREFERENCES_KEY)
      const next = saved ? sanitizePreferences(JSON.parse(saved)) : preferenceDefaults()
      synchronized = JSON.stringify(next)
      Object.assign(preferences, next)
    } catch { /* Ignore malformed cross-tab data; keep the current, usable theme. */ }
  }
  window.addEventListener('storage', sync)
  return () => {
    stop()
    window.removeEventListener('storage', sync)
    window.removeEventListener('keydown', recover, true)
    motion.removeEventListener('change', motionChanged)
    skinStyle?.remove()
    skinStyle = null
    appliedCss = ''
  }
}
