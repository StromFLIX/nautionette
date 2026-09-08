<template>
  <h2 class="settings__title">Appearance</h2>
  <p class="settings__intro">Four starting points. No fixed rules.</p>

  <section id="themes" class="theme-grid" aria-label="Color theme">
    <button v-for="theme in THEMES" :key="theme.id" class="theme-card" :aria-label="`${theme.name} theme`"
      :aria-pressed="preferences.theme === theme.id" :class="{ 'theme-card--selected': preferences.theme === theme.id }"
      @click="chooseTheme(theme.id)">
      <div class="theme-card__preview" :style="previewStyle(theme)" aria-hidden="true">
        <div class="theme-card__rail"><i /><b /><b /></div>
        <div class="theme-card__sidebar"><i /><b /><b /><b /></div>
        <div class="theme-card__main"><span /><i /><b /><b /><em /></div>
      </div>
      <span class="theme-card__caption">
        <strong>{{ theme.name }}</strong>
        <span v-if="preferences.theme === theme.id" class="material-icons" aria-hidden="true">check_circle</span>
        <span v-else class="theme-card__mode">{{ theme.mode }}</span>
      </span>
      <span class="theme-card__description">{{ theme.description }}<span v-if="Object.keys(preferences.overrides[theme.id] || {}).length"> · custom</span></span>
    </button>
  </section>

  <div class="appearance__quick">
    <div class="appearance__accent">
      <label for="quick-theme-accent" class="setting__label">Accent color</label>
      <div class="row">
        <input id="quick-theme-accent" class="color-swatch color-swatch--large" type="color" :value="currentTokens.accent.slice(0, 7)" @input="changeColor('accent', $event.target.value)" />
        <code class="grow muted">{{ currentTokens.accent.toUpperCase() }}</code>
        <button class="btn btn--sm" :disabled="!hasOverride('accent')" @click="clearToken('accent')">Reset accent</button>
      </div>
    </div>
    <div class="appearance__sample" aria-label="Live theme preview">
      <div class="row"><BrandMark /><span class="grow">A little more you.</span><span class="chip chip--success">Ready</span></div>
      <div class="appearance__sample-message">Your workspace, reimagined.</div>
      <div class="row"><code class="grow"><span class="appearance__sample-keyword">const</span> theme = <span class="appearance__sample-string">'{{ preferences.theme }}'</span></code><span class="appearance__sample-action" aria-hidden="true"><span class="material-icons">arrow_upward</span></span></div>
    </div>
  </div>

  <details id="theme-editor" class="settings-disclosure appearance__editor">
    <summary><span class="material-icons" aria-hidden="true">tune</span><span class="grow">Customize every detail</span><span class="caption dim">{{ THEME_TOKENS.length }} tokens</span></summary>
    <div class="appearance__editor-body">
      <p class="caption muted">Hex colors support transparency. Fonts use locally installed families.</p>
      <div class="appearance__token-search">
        <input v-model="tokenQuery" class="field" type="search" placeholder="Filter tokens…" aria-label="Filter theme tokens" />
        <label class="caption muted row"><input v-model="modifiedOnly" type="checkbox" />Modified only</label>
      </div>
      <p v-if="!visibleTokens.length" class="caption dim" role="status">No matching tokens.</p>
      <details v-for="group in visibleGroups" :key="group" class="settings-disclosure token-group" :open="Boolean(tokenQuery) || modifiedOnly">
        <summary><span class="grow">{{ group }}</span><span class="caption dim">{{ tokensIn(group).length }}</span></summary>
        <div class="token-grid" :key="preferences.theme">
          <div v-for="token in tokensIn(group)" :id="`token-${token.key}`" :key="token.key" class="token-field" :class="{ 'token-field--modified': hasOverride(token.key) }">
            <label :for="`theme-${token.key}`">{{ token.label }}<span v-if="hasOverride(token.key)" class="token-field__modified" title="Customized" aria-label="Customized" /></label>
            <div class="row">
              <input v-if="token.type === 'color'" class="color-swatch" type="color" :value="currentTokens[token.key].slice(0, 7)"
                :aria-label="`${token.label} color picker`" @input="changeColor(token.key, $event.target.value)" />
              <input :id="`theme-${token.key}`" class="field" :class="{ 'field--bad': errors[token.key] }"
                :type="token.type === 'number' ? 'number' : 'text'" :value="drafts[token.key] ?? currentTokens[token.key]"
                :min="token.min" :max="token.max" :step="token.step" :spellcheck="false"
                :aria-invalid="Boolean(errors[token.key])" :aria-describedby="errors[token.key] ? `error-${token.key}` : undefined"
                @change="changeToken(token, $event.target.value)" />
              <button class="btn btn--icon btn--sm" :aria-label="`Reset ${token.label}`" :disabled="!hasOverride(token.key) && !errors[token.key]" @click="clearToken(token.key)">
                <span class="material-icons" aria-hidden="true">restart_alt</span>
              </button>
            </div>
            <span class="token-field__key mono">--{{ token.key }}<span v-if="token.unit"> · {{ token.unit }}</span></span>
            <p v-if="errors[token.key]" :id="`error-${token.key}`" class="caption field-hint--bad" role="alert">{{ errors[token.key] }}</p>
          </div>
        </div>
      </details>
    </div>
  </details>

  <section id="theme-transfer" class="appearance__transfer">
    <div class="row">
      <input ref="fileInput" type="file" accept=".json,application/json" hidden @change="upload" />
      <button class="btn btn--outline btn--sm" @click="fileInput?.click()"><span class="material-icons" aria-hidden="true">upload</span>Import theme</button>
      <button class="btn btn--outline btn--sm" @click="download"><span class="material-icons" aria-hidden="true">download</span>Export theme</button>
      <span class="grow" />
      <button v-if="undo" class="btn btn--sm" @click="undoReset">Undo reset</button>
      <button v-else class="btn btn--sm" :disabled="!Object.keys(currentOverrides).length" @click="reset">Reset theme</button>
    </div>
    <p v-if="transferError" class="caption field-hint--bad" role="alert">{{ transferError }}</p>
    <p v-if="notice" class="caption muted" role="status">{{ notice }}</p>
  </section>
  <footer class="settings__footer">
    <span class="caption muted" :role="preferenceError ? 'alert' : 'status'">{{ preferenceError || 'Saved automatically on this device' }}</span>
    <span v-if="contrastWarning" class="caption appearance__contrast" role="status"><span class="material-icons" aria-hidden="true">contrast</span>{{ contrastWarning }}</span>
  </footer>
</template>

<script setup>
import { computed, reactive, ref, watch } from 'vue'
import BrandMark from '../BrandMark.vue'
import { THEMES, THEME_TOKENS, TOKEN_GROUPS, contrastRatio, exportTheme, importTheme, resolveTheme } from '../../themes'
import { applyImportedTheme, currentOverrides, currentTokens, preferenceError, preferences, resetTheme, resetToken, setToken } from '../../preferences'

const fileInput = ref(null)
const tokenQuery = ref('')
const modifiedOnly = ref(false)
const errors = reactive({})
const drafts = reactive({})
const transferError = ref('')
const notice = ref('')
const undo = ref(null)
const hasOverride = key => Object.hasOwn(currentOverrides.value, key)
const visibleTokens = computed(() => THEME_TOKENS.filter(token =>
  (!modifiedOnly.value || hasOverride(token.key)) && `${token.label} ${token.group} ${token.key}`.toLowerCase().includes(tokenQuery.value.trim().toLowerCase())))
const tokensIn = group => visibleTokens.value.filter(token => token.group === group)
const visibleGroups = computed(() => TOKEN_GROUPS.filter(group => tokensIn(group).length))
const contrastWarning = computed(() => {
  const tokens = currentTokens.value
  if (contrastRatio(tokens.text, tokens['surface-app']) < 4.5) return 'Text contrast is below 4.5:1.'
  if (contrastRatio(tokens['accent-text'], tokens.accent, tokens['surface-app']) < 4.5) return 'Button text contrast is below 4.5:1.'
  return ''
})
function previewStyle (theme) {
  const tokens = resolveTheme(theme.id, preferences.overrides[theme.id])
  return {
    '--preview-canvas': tokens['surface-app'], '--preview-panel': tokens['surface-panel'],
    '--preview-text': tokens.text, '--preview-accent': tokens.accent, '--preview-border': tokens.border
  }
}
function clearErrors () {
  for (const key of Object.keys(errors)) delete errors[key]
  for (const key of Object.keys(drafts)) delete drafts[key]
}
function chooseTheme (id) { preferences.theme = id; undo.value = null; clearErrors(); notice.value = ''; transferError.value = '' }
function changeColor (key, value) { setToken(key, value); delete errors[key]; delete drafts[key]; undo.value = null }
function clearToken (key) { resetToken(key); delete errors[key]; delete drafts[key] }
function changeToken (token, raw) {
  drafts[token.key] = raw
  const value = token.type === 'number' ? (raw === '' ? NaN : Number(raw)) : raw.trim()
  if (setToken(token.key, value)) { delete errors[token.key]; delete drafts[token.key]; undo.value = null; return }
  errors[token.key] = token.type === 'color' ? 'Use #RRGGBB or #RRGGBBAA.'
    : token.type === 'number' ? `Choose a value from ${token.min} to ${token.max}.`
      : 'Use font family names, separated by commas. No CSS declarations.'
}
function reset () {
  undo.value = { theme: preferences.theme, overrides: { ...currentOverrides.value } }
  resetTheme()
  clearErrors()
}
function undoReset () { applyImportedTheme(undo.value); undo.value = null }
async function upload (event) {
  const file = event.target.files?.[0]
  event.target.value = ''
  if (!file) return
  transferError.value = ''; notice.value = ''
  try {
    if (file.size > 64000) throw new Error('Theme files must be smaller than 64 KB.')
    const imported = importTheme(await file.text())
    applyImportedTheme(imported)
    undo.value = null
    clearErrors()
    notice.value = 'Theme imported.'
  } catch (error) { transferError.value = error.message }
}
watch(() => preferences.theme, () => { clearErrors(); undo.value = null })
defineExpose({ revealSetting: id => {
  if (id.startsWith('token-')) { tokenQuery.value = ''; modifiedOnly.value = false }
} })
function download () {
  const blob = new Blob([exportTheme(preferences.theme, currentOverrides.value)], { type: 'application/json' })
  const url = URL.createObjectURL(blob)
  const link = document.createElement('a')
  link.href = url
  link.download = `nautionette-${preferences.theme}.json`
  link.click()
  setTimeout(() => URL.revokeObjectURL(url), 1000)
}
</script>

<style scoped>
.theme-grid { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 12px; margin: 24px 0; }
.theme-card { display: block; min-width: 0; border: 1px solid var(--border-strong); border-radius: var(--radius-md); padding: 6px 6px 12px; background: var(--surface-panel); color: var(--text); text-align: left; font: inherit; cursor: pointer; transition: border-color var(--transition), box-shadow var(--transition); }
.theme-card:hover { border-color: var(--text-dim); }
.theme-card--selected { border-color: var(--accent); box-shadow: 0 0 0 2px var(--accent-soft); }
.theme-card__preview { display: grid; grid-template-columns: 12% 25% minmax(0, 1fr); aspect-ratio: 1.7; overflow: hidden; border-radius: var(--radius-sm); background: var(--preview-canvas); border: 1px solid var(--preview-border); }
.theme-card__rail { display: flex; flex-direction: column; align-items: center; gap: 12%; padding-top: 28%; border-right: 1px solid var(--preview-border); }
.theme-card__rail i { display: block; width: 56%; aspect-ratio: 1; clip-path: var(--octagon); background: var(--preview-accent); }
.theme-card__rail b { width: 30%; height: 5px; border-radius: 2px; background: var(--preview-text); opacity: 0.25; }
.theme-card__sidebar { display: flex; flex-direction: column; gap: 10%; padding: 20% 12%; background: var(--preview-panel); border-right: 1px solid var(--preview-border); }
.theme-card__sidebar i, .theme-card__sidebar b { height: 4px; width: 70%; background: var(--preview-text); opacity: 0.16; border-radius: 2px; }
.theme-card__sidebar i { width: 90%; background: var(--preview-accent); opacity: 0.65; }
.theme-card__main { display: flex; flex-direction: column; gap: 7%; padding: 14% 12% 10%; }
.theme-card__main span { display: block; width: 28%; aspect-ratio: 1; clip-path: var(--octagon); background: var(--preview-accent); opacity: 0.65; margin: 0 auto 5%; }
.theme-card__main i { height: 7px; width: 70%; margin-left: auto; background: var(--preview-accent); opacity: 0.25; border-radius: 3px; }
.theme-card__main b { height: 3px; width: 85%; background: var(--preview-text); opacity: 0.2; border-radius: 2px; }
.theme-card__main b + b { width: 58%; }
.theme-card__main em { height: 15%; border: 1px solid var(--preview-border); border-radius: 3px; margin-top: auto; }
.theme-card__caption { display: flex; align-items: center; justify-content: space-between; gap: 6px; padding: 10px 6px 2px; font-size: 13px; }
.theme-card__caption .material-icons { font-size: 16px; color: var(--accent); }
.theme-card__mode { font-size: 10px; color: var(--text-dim); text-transform: capitalize; }
.theme-card__description { display: block; padding: 0 6px; color: var(--text-muted); font-size: 10px; line-height: 1.6; }
.appearance__quick { display: grid; grid-template-columns: 1fr 1.2fr; gap: 28px; align-items: center; margin: 28px 0; }
.appearance__accent .setting__label { display: block; margin-bottom: 12px; }
.color-swatch { flex: none; appearance: none; width: 32px; height: 34px; padding: 3px; border: 1px solid var(--border-strong); border-radius: var(--radius-sm); background: var(--surface-input); cursor: pointer; }
.color-swatch::-webkit-color-swatch-wrapper { padding: 0; }
.color-swatch::-webkit-color-swatch { border: 0; border-radius: var(--radius-xs); }
.color-swatch::-moz-color-swatch { border: 0; border-radius: var(--radius-xs); }
.color-swatch--large { width: 40px; height: 40px; }
.appearance__sample { border: 1px solid var(--border); border-radius: var(--radius-md); padding: 16px; background: var(--surface-panel); font-size: 12px; }
.appearance__sample .brand-mark { width: 26px; height: 26px; }
.appearance__sample-message { width: fit-content; margin: 16px 0 16px auto; padding: 8px 12px; border-radius: var(--radius-md); background: var(--bubble-out); color: var(--bubble-text); }
.appearance__sample-keyword { color: var(--syntax-keyword); }
.appearance__sample-string { color: var(--syntax-string); }
.appearance__sample code { font-size: 11px; overflow-wrap: anywhere; }
.appearance__sample-action { display: grid; place-items: center; width: 26px; height: 26px; background: var(--accent); color: var(--accent-text); clip-path: var(--octagon); }
.appearance__sample-action .material-icons { font-size: 16px; }
.appearance__editor-body { padding: 6px 16px 16px; }
.appearance__token-search { display: flex; align-items: center; gap: 20px; margin: 16px 0; }
.appearance__token-search > input { flex: 1; }
.appearance__token-search label { flex: none; white-space: nowrap; }
.token-group { margin-top: 8px; border: 0; border-radius: 0; border-top: 1px solid var(--border); }
.token-group > summary { padding-inline: 0; }
.token-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 24px; padding: 12px 0 20px; }
.token-field { min-width: 0; }
.token-field > label { display: flex; align-items: center; gap: 6px; margin-bottom: 8px; font-size: 12px; }
.token-field .field { flex: 1; width: 100%; font: 11px var(--font-mono); }
.token-field .btn { flex: none; }
.token-field__key { display: block; margin-top: 5px; font-size: 10px; color: var(--text-dim); }
.token-field__modified { display: inline-block; width: 5px; height: 5px; background: var(--accent); border-radius: 50%; }
.appearance__transfer { margin-top: 24px; }
.appearance__transfer > .row { flex-wrap: wrap; }
.appearance__transfer .material-icons { font-size: 16px; }
.appearance__transfer p { margin: 12px 0 0; }
.appearance__contrast { display: flex; align-items: center; gap: 6px; color: var(--warning); }
.appearance__contrast .material-icons { font-size: 15px; }
@media (max-width: 1200px) { .theme-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); } .appearance__quick { grid-template-columns: minmax(0, 1fr); gap: 20px; } }
@media (max-width: 600px) {
  .theme-grid { gap: 10px; }
  .token-grid { grid-template-columns: minmax(0, 1fr); gap: 20px; }
  .appearance__token-search { flex-wrap: wrap; gap: 10px; }
  .appearance__token-search > input { flex-basis: 100%; }
  .appearance__editor > summary > .caption { display: none; }
  .appearance__editor-body { padding-inline: 12px; }
}
</style>
