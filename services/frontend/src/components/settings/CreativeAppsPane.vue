<template>
  <h2 id="creative-apps" class="settings__title">Creative apps</h2>
  <p class="settings__intro">Native ArtCraft tools run server-side in the agent sandbox. Ask for a drawing, document, presentation, spreadsheet, image, video or audio result; the agent can publish its files and preview to Outputs.</p>
  <p class="caption dim">All 13 CLI applications are bundled with pinned versions. Nothing starts until an agent uses it. Version changes apply to new turns, not work already running. These are early upstream releases; desktop-only commands are unavailable.</p>
  <p class="caption apps-notice">Updates install trusted upstream native code, verify its SHA-256 checksum and test its headless tool interface before switching. A failed update keeps the working version. Disabling hides app tools; it is not a security boundary against an agent with shell access.</p>
  <p v-if="error" class="caption field-hint--bad" role="alert">{{ error }} <button type="button" class="btn btn--sm" @click="load">Retry loading</button></p>
  <p v-if="loading" class="caption dim" role="status">Loading creative apps…</p>
  <article v-for="item in apps" :key="item.id" class="craft-app" :aria-label="item.name">
    <div class="craft-app__head">
      <div class="grow"><h3>{{ item.name }}</h3><p class="caption dim">{{ item.description }}</p></div>
      <label class="craft-app__enabled caption"><input type="checkbox" :checked="item.enabled" :disabled="busy[item.id] || item.status === 'installing'" :aria-label="`Enable ${item.name}`" @change="configure(item, { enabled: $event.target.checked })" /> Enabled</label>
    </div>
    <p class="caption">Active: <strong>{{ item.version }}</strong> <span class="dim">· {{ item.bundled ? 'Bundled' : 'Verified update' }}{{ item.enabled ? '' : ' · Disabled for new turns' }}</span></p>
    <div class="craft-app__version">
      <label class="caption" :for="`craft-version-${item.id}`">Pinned version</label>
      <input :id="`craft-version-${item.id}`" v-model="draft[item.id]" class="field" :list="`craft-releases-${item.id}`" placeholder="0.8.0" :disabled="busy[item.id] || item.status === 'installing'" autocapitalize="none" autocomplete="off" spellcheck="false" />
      <datalist :id="`craft-releases-${item.id}`"><option v-for="version in releases[item.id] || []" :key="version" :value="version" /></datalist>
      <button type="button" class="btn btn--sm" :disabled="busy[item.id] || item.status === 'installing'" @click="checkReleases(item)">Check releases</button>
      <button type="button" class="btn btn--sm btn--primary" :disabled="busy[item.id] || item.status === 'installing' || !draft[item.id]?.trim() || draft[item.id].trim() === item.version" @click="configure(item, { version: draft[item.id].trim() })">Apply version</button>
      <button v-if="!item.bundled" type="button" class="btn btn--sm" :disabled="busy[item.id] || item.status === 'installing'" @click="configure(item, { version: 'bundled' })">Use bundled {{ item.bundled_version }}</button>
    </div>
    <p v-if="releases[item.id]" class="caption dim">{{ releases[item.id].length ? `Available: ${releases[item.id].join(', ')}. Choose a version above.` : 'No verified stable releases found for this architecture.' }}</p>
    <p v-if="item.status === 'installing'" class="caption" role="status">Installing and verifying {{ item.pending_version }}… The active version stays available. You can leave this page.</p>
    <p v-if="item.error || errors[item.id]" class="caption field-hint--bad" role="alert">{{ errors[item.id] || item.error }}</p>
    <a :href="item.repository" class="caption dim" target="_blank" rel="noopener noreferrer">Upstream releases &amp; documentation ↗</a>
  </article>
</template>
<script setup>
import { onMounted, onUnmounted, ref } from 'vue'
import { api } from '../../api'
const apps = ref([]), draft = ref({}), releases = ref({}), busy = ref({}), errors = ref({}), loading = ref(true), error = ref('')
let timer, disposed = false
async function load () {
  clearTimeout(timer)
  try {
    const data = await api.creativeApps()
    if (disposed) return
    for (const item of data.apps) {
      const previous = apps.value.find(old => old.id === item.id)
      if (!(item.id in draft.value) || (previous?.status === 'installing' && item.status !== 'installing' && !item.error)) draft.value[item.id] = item.version
    }
    apps.value = data.apps
    error.value = ''
    if (data.apps.some(item => item.status === 'installing')) timer = setTimeout(load, 1500)
  } catch (failure) { if (!disposed) error.value = failure.message }
  finally { if (!disposed) loading.value = false }
}
async function configure (item, payload) {
  if (busy.value[item.id]) return
  busy.value[item.id] = true; errors.value[item.id] = ''
  try {
    const updated = await api.configureCreativeApp(item.id, { ...payload, expected_revision: item.revision })
    if (disposed) return
    apps.value = apps.value.map(old => old.id === updated.id ? updated : old)
    if (updated.status !== 'installing') draft.value[item.id] = updated.version
    await load()
  } catch (failure) { if (!disposed) { errors.value[item.id] = failure.message; await load() } }
  finally { busy.value[item.id] = false }
}
async function checkReleases (item) {
  if (busy.value[item.id]) return
  busy.value[item.id] = true; errors.value[item.id] = ''
  try {
    const data = await api.creativeAppReleases(item.id)
    if (!disposed) releases.value[item.id] = data.versions
  } catch (failure) { if (!disposed) errors.value[item.id] = failure.message }
  finally { busy.value[item.id] = false }
}
onMounted(load)
onUnmounted(() => { disposed = true; clearTimeout(timer) })
</script>
<style scoped>
.apps-notice { margin: 20px 0; padding: 12px 14px; background: var(--warning-soft); color: var(--text-muted); border-radius: var(--radius-sm); line-height: 1.7; }
.craft-app { margin: 16px 0; padding: 18px; border: 1px solid var(--border); border-radius: var(--radius-md); background: var(--surface-panel); overflow-wrap: anywhere; }
.craft-app__head { display: flex; align-items: center; flex-wrap: wrap; gap: 12px; }
.craft-app__head .grow { min-width: 180px; }
.craft-app h3 { margin: 0; font-size: 0.9375rem; }
.craft-app p { margin: 10px 0; line-height: 1.6; }
.craft-app__head p { margin-bottom: 0; }
.craft-app__enabled { display: flex; align-items: center; gap: 6px; }
.craft-app input[type=checkbox] { accent-color: var(--accent); }
.craft-app__version { display: flex; align-items: center; flex-wrap: wrap; gap: 8px; margin: 12px 0; }
.craft-app__version input { width: 120px; min-width: 0; }
@media (max-width: 640px) { .craft-app { padding: 14px; } .craft-app__version > label { width: 100%; } }
</style>
