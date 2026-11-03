<template>
  <h2 id="creative-apps" class="settings__title">Creative apps</h2>
  <p class="settings__intro">Server-side tools for creative files. Apps start on demand; saved results appear in Outputs.</p>
  <section class="craft-updates" aria-label="App updates">
    <div class="craft-updates__head">
      <div class="grow">
        <h3>{{ loading ? 'Loading app status…' : !apps.length ? 'App status unavailable' : outdated.length ? `${outdated.length} app update${outdated.length === 1 ? '' : 's'} available` : updates.checking ? 'Checking for updates…' : unchecked ? 'Release status not yet known' : 'Your apps are up to date' }}</h3>
        <p class="caption dim">{{ summary }} <span v-if="updates.checked_at">Last checked {{ checkedAt }}.</span></p>
      </div>
      <button type="button" class="btn btn--sm" :disabled="loading || action || updates.checking" @click="checkUpdates">{{ updates.checking ? 'Checking…' : 'Check for updates' }}</button>
      <button type="button" class="btn btn--sm btn--primary" :disabled="loading || action || !outdated.some(item => item.status === 'ready')" @click="updateAll">Update all{{ outdated.length ? ` (${outdated.length})` : '' }}</button>
    </div>
    <label class="craft-auto"><input type="checkbox" :checked="updates.automatic" :disabled="loading || action" @change="setAutomatic($event)" /><span><strong>Automatically update all apps</strong><span class="caption dim">Hourly stable updates, even with Settings closed. Includes disabled apps and replaces version pins.</span></span></label>
    <details class="craft-policy"><summary class="caption">How updates work</summary><p class="caption dim">Downloads are checksum-verified and tested before activation. Failed updates keep the working version; running turns retain their exact versions. Already queued updates continue if automatic updates are switched off. Apps are prepared in read-only tool volumes, not inside the Pi image.</p></details>
    <p v-if="queued" class="caption" role="status">{{ queued }} Updates run two at a time. You can leave this page.</p>
  </section>
  <p v-if="error" class="caption field-hint--bad" role="alert">{{ error }} <button type="button" class="btn btn--sm" @click="load">Retry loading</button></p>
  <p v-if="loading" class="caption dim" role="status">Loading creative apps…</p>
  <div v-if="!loading" class="craft-filters">
    <label class="caption" for="craft-filter">Show</label>
    <select id="craft-filter" v-model="filter" class="field"><option value="all">All apps ({{ apps.length }})</option><option value="updates">Updates available ({{ outdated.length }})</option><option value="attention">Needs attention ({{ attention }})</option></select>
  </div>
  <p v-if="!loading && !visibleApps.length" class="caption dim" role="status">No apps match this filter.</p>
  <article v-for="item in visibleApps" :key="item.id" class="craft-app" :class="{ 'craft-app--outdated': item.update_available }" :aria-label="item.name">
    <div class="craft-app__head">
      <div class="grow"><h3>{{ item.name }} <span class="craft-badge" :class="{ 'craft-badge--update': item.update_available }">{{ badge(item) }}</span></h3><p class="caption dim">{{ item.description }}</p></div>
      <label class="craft-app__enabled caption"><input type="checkbox" :checked="item.enabled" :disabled="busy[item.id] || item.status !== 'ready'" :aria-label="`Enable ${item.name}`" @change="configure(item, { enabled: $event.target.checked })" /> Enabled</label>
    </div>
    <div class="craft-app__current">
      <p class="caption">Active: <strong>{{ item.version }}</strong><span v-if="item.latest_version" class="dim">{{ item.update_available ? ' → ' : ' · ' }}Latest stable: <strong>{{ item.latest_version }}</strong></span><span class="dim"> · {{ item.bundled ? 'Deployment default' : 'Verified update' }}{{ item.enabled ? '' : ' · Disabled for new turns' }}</span></p>
      <button v-if="item.update_available && item.status === 'ready'" type="button" class="btn btn--sm btn--primary" :disabled="busy[item.id] || action" @click="configure(item, { version: item.latest_version })">Update to {{ item.latest_version }}</button>
    </div>
    <p v-if="item.status !== 'ready'" class="caption" role="status">{{ item.status === 'queued' ? 'Queued' : 'Installing and verifying' }} {{ item.pending_version }}… The active version stays available.</p>
    <p v-if="item.error || errors[item.id]" class="caption field-hint--bad" role="alert">{{ errors[item.id] || item.error }}</p>
    <p v-if="item.update_error" class="caption field-hint--bad" role="alert">{{ item.update_error }} <button type="button" class="btn btn--sm" :disabled="action || updates.checking" @click="checkUpdates">Retry release check</button></p>
    <details class="craft-advanced">
      <summary class="caption">Version options &amp; documentation</summary>
      <div class="craft-app__version">
        <label class="caption" :for="`craft-version-${item.id}`">Exact version</label>
        <input :id="`craft-version-${item.id}`" v-model="draft[item.id]" class="field" :list="`craft-releases-${item.id}`" placeholder="0.8.0" :disabled="busy[item.id] || item.status !== 'ready'" autocapitalize="none" autocomplete="off" spellcheck="false" />
        <datalist :id="`craft-releases-${item.id}`"><option v-for="version in releases[item.id] || []" :key="version" :value="version" /></datalist>
        <button type="button" class="btn btn--sm" :disabled="busy[item.id] || item.status !== 'ready'" @click="checkReleases(item)">List versions</button>
        <button type="button" class="btn btn--sm" :disabled="busy[item.id] || item.status !== 'ready' || !draft[item.id]?.trim() || draft[item.id].trim() === item.version" @click="configure(item, { version: draft[item.id].trim() })">Apply version</button>
        <button v-if="!item.bundled" type="button" class="btn btn--sm" :disabled="busy[item.id] || item.status !== 'ready'" @click="configure(item, { version: 'bundled' })">Restore default {{ item.bundled_version }}</button>
      </div>
      <p v-if="releases[item.id]" class="caption dim">{{ releases[item.id].length ? `Available: ${releases[item.id].join(', ')}.` : 'No verified stable releases found for this architecture.' }}</p>
      <a :href="item.repository" class="caption dim" target="_blank" rel="noopener noreferrer">Upstream releases &amp; documentation ↗</a>
    </details>
  </article>
  <p class="caption dim">Updates run trusted upstream native code. These are early releases; desktop-only commands are unavailable. Disabling hides app tools, but is not a security boundary against an agent with shell access.</p>
</template>
<script setup>
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { api } from '../../api'
import { onLiveEvent } from '../../store'
const apps = ref([]), updates = ref({ automatic: false, revision: 0, checking: false }), draft = ref({}), releases = ref({}), busy = ref({}), errors = ref({}), loading = ref(true), error = ref(''), action = ref(false), filter = ref('all'), queued = ref('')
let timer, disposed = false
const unsubscribe = onLiveEvent(event => {
  if (['artcraft.changed', 'connected', 'client.reconnect'].includes(event.kind) && !disposed) {
    clearTimeout(timer)
    timer = setTimeout(load, 150)
  }
})
const outdated = computed(() => apps.value.filter(item => item.update_available))
const attention = computed(() => apps.value.filter(item => item.error || item.update_error).length)
const unchecked = computed(() => apps.value.some(item => !item.latest_version || item.update_error))
const summary = computed(() => `${apps.value.length} apps · ${apps.value.filter(item => item.status !== 'ready').length} queued or installing.${attention.value ? ` ${attention.value} need attention; their release status may be unknown.` : ''}`)
const checkedAt = computed(() => updates.value.checked_at ? new Date(updates.value.checked_at * 1000).toLocaleString() : '')
const visibleApps = computed(() => apps.value.filter(item => filter.value === 'all' || (filter.value === 'updates' ? item.update_available : item.error || item.update_error)).slice().sort((a, b) => Number(b.update_available) - Number(a.update_available)))
function badge (item) {
  if (item.status === 'queued') return 'Queued'
  if (item.status === 'installing') return 'Updating'
  if (item.error) return 'Update failed'
  if (item.update_error) return 'Check failed'
  if (item.update_available) return 'Update available'
  if (!item.latest_version) return updates.value.checking ? 'Checking…' : 'Not checked'
  return item.version === item.latest_version ? 'Up to date' : 'Newer version selected'
}
function accept (data) {
  if (disposed) return
  for (const item of data.apps) {
    const previous = apps.value.find(old => old.id === item.id)
    if (!(item.id in draft.value) || (previous?.status !== 'ready' && item.status === 'ready' && !item.error)) draft.value[item.id] = item.version
  }
  apps.value = data.apps
  updates.value = data.updates
  clearTimeout(timer)
  if (updates.value.checking || data.apps.some(item => item.status !== 'ready')) timer = setTimeout(load, 1500)
}
async function load () {
  clearTimeout(timer)
  try { accept(await api.creativeApps()); if (!disposed) error.value = '' }
  catch (failure) { if (!disposed) { error.value = failure.message; timer = setTimeout(load, 5000) } }
  finally { if (!disposed) loading.value = false }
}
async function perform (call) {
  if (action.value) return
  action.value = true; error.value = ''
  try { const data = await call(); accept(data); return data }
  catch (failure) { if (!disposed) error.value = failure.message }
  finally { action.value = false }
}
async function checkUpdates () { await perform(() => api.checkCreativeAppUpdates()) }
async function updateAll () {
  const data = await perform(() => api.updateAllCreativeApps())
  if (data && !disposed) queued.value = data.queued.length ? `${data.queued.length} app updates queued.` : 'No additional updates to queue.'
}
async function setAutomatic (event) {
  const automatic = event.target.checked
  const data = await perform(() => api.configureCreativeAppUpdates({ automatic, expected_revision: updates.value.revision }))
  const failure = error.value
  await load()
  if (!disposed) {
    event.target.checked = updates.value.automatic
    if (!data && failure) error.value = failure
  }
}
async function configure (item, payload) {
  if (busy.value[item.id]) return
  busy.value[item.id] = true; errors.value[item.id] = ''
  try {
    const updated = await api.configureCreativeApp(item.id, { ...payload, expected_revision: item.revision })
    if (disposed) return
    apps.value = apps.value.map(old => old.id === updated.id ? updated : old)
    if (updated.status === 'ready') draft.value[item.id] = updated.version
    await load()
  } catch (failure) { if (!disposed) { errors.value[item.id] = failure.message; await load() } }
  finally { busy.value[item.id] = false }
}
async function checkReleases (item) {
  if (busy.value[item.id]) return
  busy.value[item.id] = true; errors.value[item.id] = ''
  try { const data = await api.creativeAppReleases(item.id); if (!disposed) releases.value[item.id] = data.versions }
  catch (failure) { if (!disposed) errors.value[item.id] = failure.message }
  finally { busy.value[item.id] = false }
}
onMounted(async () => {
  await load()
  if (!disposed && !error.value && !updates.value.checking && (!updates.value.checked_at || Date.now() / 1000 - updates.value.checked_at >= 3600)) await checkUpdates()
})
onUnmounted(() => { disposed = true; clearTimeout(timer); unsubscribe() })
</script>
<style scoped>
.craft-updates { margin: 20px 0; padding: 18px; border: 1px solid var(--border); border-radius: var(--radius-md); background: var(--surface-panel); }
.craft-updates__head, .craft-app__head, .craft-app__current { display: flex; align-items: center; flex-wrap: wrap; gap: 12px; }
.craft-updates h3, .craft-app h3 { margin: 0; font-size: 0.9375rem; }
.craft-updates p { line-height: 1.6; margin: 8px 0; }
.craft-auto { display: flex; align-items: start; gap: 10px; padding-top: 16px; margin-top: 12px; border-top: 1px solid var(--border); }
.craft-auto input { margin-top: 4px; flex-shrink: 0; }
.craft-auto strong { font-size: 0.875rem; }
.craft-auto .caption { display: block; margin-top: 5px; line-height: 1.6; }
.craft-policy { padding-top: 12px; }
.craft-policy summary { cursor: pointer; color: var(--text-muted); }
.craft-filters { display: flex; align-items: center; gap: 10px; margin: 20px 0 12px; }
.craft-filters select { width: auto; max-width: 100%; }
.craft-app { margin: 12px 0; padding: 18px; border: 1px solid var(--border); border-radius: var(--radius-md); background: var(--surface-panel); overflow-wrap: anywhere; }
.craft-app--outdated { border-left: 3px solid var(--accent); }
.craft-app__head .grow, .craft-updates__head .grow { min-width: 0; flex-basis: 210px; }
.craft-app h3 { display: flex; flex-wrap: wrap; align-items: center; gap: 8px; }
.craft-app p { margin: 10px 0; line-height: 1.6; }
.craft-app__head p { margin-bottom: 0; }
.craft-app__enabled { display: flex; align-items: center; gap: 6px; }
.craft-app__current { justify-content: space-between; margin: 8px 0; }
.craft-badge { font-size: 0.6875rem; padding: 3px 7px; border-radius: var(--radius-sm); background: var(--surface-hover); color: var(--text-muted); font-weight: 500; }
.craft-badge--update { background: var(--accent-soft); color: var(--accent); }
input[type=checkbox] { accent-color: var(--accent); }
.craft-advanced { padding-top: 10px; border-top: 1px solid var(--border); }
.craft-advanced summary { cursor: pointer; color: var(--text-muted); }
.craft-app__version { display: flex; align-items: center; flex-wrap: wrap; gap: 8px; margin: 12px 0; }
.craft-app__version input { width: 120px; min-width: 0; }
@media (max-width: 640px) { .craft-app, .craft-updates { padding: 14px; } .craft-app__version > label { width: 100%; } .craft-updates__head > button { flex-grow: 1; } }
</style>
