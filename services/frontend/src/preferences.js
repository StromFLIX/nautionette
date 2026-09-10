import { computed, reactive, ref, watch } from 'vue'
import { Dark, setCssVar } from 'quasar'
import { cssTokens, resolveTheme, themeById, validToken } from './themes'
import { PREFERENCES_KEY, preferenceDefaults, sanitizePreferences, validPreference, workspaceDefaults } from './preferences-schema'

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
export const currentOverrides = computed(() => preferences.overrides[preferences.theme] || {})
export const currentTokens = computed(() => resolveTheme(preferences.theme, currentOverrides.value))

export function setToken (key, value) {
  if (!validToken(key, value)) return false
  preferences.overrides[preferences.theme] = { ...currentOverrides.value, [key]: value }
  return true
}
export function resetToken (key) {
  const next = { ...currentOverrides.value }
  delete next[key]
  preferences.overrides[preferences.theme] = next
}
export function resetTheme () { preferences.overrides[preferences.theme] = {} }
export function resetWorkspace () { Object.assign(preferences, workspaceDefaults()) }
export function setPreference (key, value) {
  if (validPreference(key, value)) preferences[key] = value
}
export function applyImportedTheme ({ theme, overrides }) {
  // A single patch, already validated by importTheme, so an invalid file is never partially applied.
  Object.assign(preferences, { theme, overrides: { ...preferences.overrides, [theme]: overrides } })
}

function apply () {
  const root = document.documentElement
  const theme = themeById(preferences.theme)
  for (const [key, value] of Object.entries(cssTokens(theme.id, currentOverrides.value))) root.style.setProperty(key, value)
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

/** Called after Quasar is installed, before the first component is mounted. */
export function startPreferences () {
  apply()
  const stop = watch(preferences, () => {
    apply()
    try {
      localStorage.setItem(PREFERENCES_KEY, JSON.stringify(preferences))
      preferenceError.value = ''
    } catch {
      preferenceError.value = 'Storage is unavailable. These changes apply for this session only.'
    }
  }, { deep: true, flush: 'post' })
  const sync = event => {
    if (event.key !== PREFERENCES_KEY && event.key !== null) return
    try {
      Object.assign(preferences, event.newValue ? sanitizePreferences(JSON.parse(event.newValue)) : preferenceDefaults())
    } catch { /* Ignore malformed cross-tab data; keep the current, usable theme. */ }
  }
  window.addEventListener('storage', sync)
  return () => { stop(); window.removeEventListener('storage', sync) }
}
