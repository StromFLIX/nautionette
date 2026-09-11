<template>
  <section id="skin-library" class="skin-library" aria-labelledby="skin-library-title">
    <div class="row"><h3 id="skin-library-title" class="grow">Skin packs</h3><span class="caption dim">{{ preferences.skins.length }} installed</span></div>
    <p class="caption muted">Go beyond colors: custom layouts, typography, textures and controls. Try an example or import a pack below.</p>
    <div class="skin-library__grid">
      <article v-for="skin in designs" :key="skin.id" class="skin-card" :class="{ 'skin-card--selected': preferences.skin === skin.id }">
        <SkinPreview :skin="skin" />
        <div class="skin-card__body">
          <div class="row"><strong class="grow">{{ skin.name }}</strong><span v-if="preferences.skin === skin.id" class="material-icons" aria-label="Active skin">check_circle</span></div>
          <p class="caption muted">{{ skin.description }}</p>
          <p class="caption dim">{{ skin.author || 'Custom skin' }} · {{ installed(skin.id) ? 'Installed' : 'Example pack' }}</p>
          <div class="skin-card__actions">
            <button class="btn btn--outline btn--sm" :aria-label="`Use ${skin.name} skin`" :aria-pressed="preferences.skin === skin.id" @click="use(skin)">{{ preferences.skin === skin.id ? 'Active' : 'Use skin' }}</button>
            <button class="btn btn--sm" :aria-label="`Download ${skin.name} skin`" @click="download(skin)">Download</button>
            <button v-if="installed(skin.id)" class="btn btn--sm" :aria-label="`Remove ${skin.name} skin`" @click="removeSkin(skin.id)">Remove</button>
          </div>
        </div>
      </article>
    </div>
    <p v-if="error" class="caption field-hint--bad" role="alert">{{ error }}</p>
    <p id="skin-recovery" class="caption muted">Only use packs you trust: CSS can hide or move controls. To recover, press <kbd>Ctrl / Cmd + Alt + 0</kbd> or <a href="?safe-appearance=1">open appearance safe mode</a>. Your packs stay in the library.</p>
    <button v-if="currentSkin" class="btn btn--sm" @click="recoverAppearance">Restore default appearance</button>
  </section>
</template>

<script setup>
import { computed, ref } from 'vue'
import SkinPreview from './SkinPreview.vue'
import winamp from '../../skin-examples/winamp-classic.skin.json'
import xp from '../../skin-examples/windows-xp.skin.json'
import { exportSkin } from '../../skins'
import { downloadDesign } from '../../skin-download'
import { applyImportedSkin, chooseSkin, currentSkin, preferences, recoverAppearance, removeSkin } from '../../preferences'

const examples = [winamp, xp]
const error = ref('')
const installed = id => preferences.skins.some(skin => skin.id === id)
const designs = computed(() => [...preferences.skins, ...examples.filter(skin => !installed(skin.id))])
function use (skin) {
  error.value = ''
  try {
    if (installed(skin.id)) chooseSkin(skin)
    else applyImportedSkin(skin)
  } catch (cause) { error.value = cause.message }
}
function download (skin) {
  error.value = ''
  try { downloadDesign(exportSkin(skin, preferences.overrides[`skin:${skin.id}`]), `${skin.id}.skin.json`) }
  catch (cause) { error.value = cause.message }
}
</script>

<style scoped>
.skin-library { margin: 24px 0 32px; }
.skin-library h3 { font-weight: 600; }
.skin-library > p { margin: 12px 0; line-height: 1.6; }
.skin-library a { color: var(--accent-hover); }
.skin-library__grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 16px; margin: 20px 0; }
.skin-card { min-width: 0; padding: 6px; border: 1px solid var(--border-strong); border-radius: var(--radius-md); background: var(--surface-panel); }
.skin-card--selected { border-color: var(--accent); box-shadow: 0 0 0 2px var(--accent-soft); }
.skin-card__body { padding: 12px 8px 8px; overflow-wrap: anywhere; }
.skin-card__body .material-icons { color: var(--accent); font-size: 1rem; }
.skin-card p { margin: 8px 0; }
.skin-card__actions { display: flex; flex-wrap: wrap; gap: 6px; margin-top: 12px; }
@media (max-width: 600px) { .skin-library__grid { grid-template-columns: minmax(0, 1fr); } }
</style>
