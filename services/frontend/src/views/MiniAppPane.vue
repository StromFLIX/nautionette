<template>
  <div v-if="!route.params.id" class="app-empty stack grow">
    <span class="material-icons" aria-hidden="true">apps</span>
    <h1>Mini-apps</h1>
    <p>Ask an agent to build an app. Custom interfaces, durable SQLite state and scoped tools—without a workflow for every button.</p>
    <p class="caption muted">Open an app from the list, or ask your agent to read the mini-app publishing contract.</p>
  </div>
  <section v-else class="miniapp stack grow" aria-label="Mini-app">
    <header class="miniapp__head row">
      <button class="btn btn--icon miniapp__back" aria-label="Back to apps" @click="backTo('/apps')"><span class="material-icons">arrow_back</span></button>
      <div class="grow"><h1 class="truncate">{{ item?.title || 'Mini-app' }}</h1><p class="caption muted">{{ active?.manifest.description || 'Isolated application' }}</p></div>
      <button v-if="item" class="btn btn--icon" :aria-label="item.pinned ? 'Unpin app' : 'Pin app'" @click="edit({ pinned: !item.pinned })"><span class="material-icons">{{ item.pinned ? 'push_pin' : 'bookmark_border' }}</span></button>
      <button class="btn btn--icon" aria-label="Reload app" @click="load"><span class="material-icons">refresh</span></button>
      <button class="btn btn--icon" aria-label="App details" :aria-expanded="showDetails" @click="showDetails = !showDetails; if (showDetails) resources()"><span class="material-icons">info_outline</span></button>
    </header>
    <p v-if="error" class="miniapp__notice caption" role="alert">{{ error }}</p>
    <div v-if="showDetails && item" class="miniapp__details scroll-y">
      <p class="caption">{{ item.id }} · Release {{ item.active_revision || 'not ready' }} · {{ active?.manifest.backend?.mode || 'frontend-only' }}</p>
      <p class="caption muted">This app cannot read Nautionette’s token, browser storage or other applications. Network/tool access is enforced by its published grants.</p>
      <div class="row">
        <button class="btn" :disabled="busy" @click="edit({ archived: !item.archived })">{{ item.archived ? 'Unarchive' : 'Archive' }}</button>
        <button v-if="active?.manifest.backend && !item.archived" class="btn" :disabled="busy" @click="restart">Restart backend</button>
        <button v-if="item.archived" class="btn" :disabled="busy" @click="remove">Permanently delete app and data</button>
      </div>
      <p v-if="active?.manifest.backend" class="caption">Published grants</p>
      <pre v-if="active?.manifest.backend?.permissions" class="caption miniapp__metrics">{{ JSON.stringify(active.manifest.backend.permissions, null, 2) }}</pre>
      <pre v-if="runtime" class="caption miniapp__metrics">{{ JSON.stringify(runtime, null, 2) }}</pre>
      <h2 class="caption">Releases</h2>
      <div v-for="version in item.releases" :key="version.revision" class="row caption">
        <span class="grow">{{ version.revision }} · {{ version.state }} {{ version.error }}</span>
        <button v-if="version.state === 'ready' && !item.archived" class="btn" :disabled="busy" @click="restore(version.revision)">{{ version.revision === item.active_revision ? 'Rebuild code' : 'Restore code' }}</button>
      </div>
      <p class="caption muted">Restoring code keeps current data. Migrations must remain backward compatible.</p>
    </div>
    <p v-if="loading" class="miniapp__notice caption" role="status">Loading app…</p>
    <p v-else-if="item?.archived" class="miniapp__notice caption">This app is archived. Its backend and capabilities are suspended; its data is preserved.</p>
    <iframe v-else-if="active" ref="frame" :key="generation" class="miniapp__frame grow" :title="item.title" sandbox="allow-scripts" referrerpolicy="no-referrer" :srcdoc="document" @load="connect" />
    <p v-else-if="item" class="miniapp__notice caption">No healthy release yet. {{ item.releases[0]?.error || 'Check publication status with your agent.' }}</p>
  </section>
</template>

<script setup>
import { computed, onUnmounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { api } from '../api'
import { actions, onLiveEvent } from '../store'
import { currentTokens, preferences } from '../preferences'
import { backTo } from '../router'
import { connectMiniapp, miniappDocument } from '../miniapp-bridge'
const route = useRoute(), router = useRouter()
const item = ref(null), frame = ref(null), runtime = ref(null)
const error = ref(''), loading = ref(false), busy = ref(false), showDetails = ref(false), generation = ref(0)
const active = computed(() => item.value?.active)
const document = computed(() => miniappDocument(active.value?.manifest.frontend || {}))
const theme = computed(() => ({ tokens: Object.fromEntries(Object.entries(currentTokens.value).map(([key, value]) => [`--${key}`, value])), dark: globalThis.document.documentElement.dataset.colorScheme === 'dark', fontSize: `${preferences.interfaceSize}%` }))
let bridge, request, sequence = 0
function close () { bridge?.close(); bridge = null; request?.abort() }
async function load () {
  const id = route.params.id, run = ++sequence
  close(); error.value = ''; runtime.value = null; item.value = null
  if (!id) return
  loading.value = true
  request = new AbortController()
  try { const data = await api.miniapp(id, request.signal); if (run === sequence) { item.value = data; generation.value++ } }
  catch (e) { if (run === sequence && e.name !== 'AbortError') error.value = e.message }
  finally { if (run === sequence) loading.value = false }
}
function connect () {
  bridge?.close()
  if (!frame.value || !item.value?.active || item.value.archived) return
  const id = item.value.id, revision = active.value.revision
  bridge = connectMiniapp(frame.value, (payload, signal) => api.miniappRequest(id, { revision, ...payload }, signal), theme.value)
}
watch(theme, value => bridge?.setTheme(value), { deep: true, flush: 'post' })
watch(() => route.params.id, load, { immediate: true })
const unsubscribe = onLiveEvent(event => {
  if (event.kind === 'client.reconnect' || (event.kind === 'miniapp.changed' && event.data?.app_id === route.params.id)) load()
})
onUnmounted(() => { sequence++; close(); unsubscribe() })
async function resources () {
  try { runtime.value = (await api.miniappStatus(route.params.id)).runtime }
  catch (e) { error.value = e.message }
}
async function operate (fn) {
  if (busy.value) return
  busy.value = true; error.value = ''
  try { await fn(); await actions.loadMiniapps(); await load() }
  catch (e) { error.value = e.message }
  finally { busy.value = false }
}
function edit (patch) { return operate(() => api.editMiniapp(item.value.id, { expected_revision: item.value.revision, ...patch })) }
function restore (revision) { return operate(() => api.restoreMiniapp(item.value.id, { expected_revision: item.value.revision, revision })) }
function restart () { return operate(() => api.controlMiniapp(item.value.id, 'restart')) }
function remove () {
  // Destructive data removal is deliberately distinct from safe archiving.
  if (!window.confirm('Permanently delete this app, retained releases and SQLite data?')) return
  return operate(async () => { await api.deleteMiniapp(item.value.id, item.value.revision); await router.push('/apps') })
}
</script>

<style scoped>
.miniapp { min-width: 0; min-height: 0; height: 100%; }
.miniapp__head { flex: none; gap: 8px; padding: 14px 18px; border-bottom: 1px solid var(--border); min-width: 0; }
.miniapp__head .grow { min-width: 0; }
.miniapp__head h1 { margin: 0; font-size: 1rem; }
.miniapp__head p { margin: 4px 0 0; overflow-wrap: anywhere; }
.miniapp__frame { border: 0; width: 100%; min-height: 0; background: var(--surface-app); }
.miniapp__notice { padding: 12px 18px; margin: 0; }
.miniapp__details { flex: none; max-height: 55%; padding: 12px 18px; background: var(--surface-panel); border-bottom: 1px solid var(--border); }
.miniapp__metrics { overflow-wrap: anywhere; white-space: pre-wrap; }
.miniapp__back { display: none; }
.app-empty { min-height: 100%; align-items: center; justify-content: center; padding: 32px; text-align: center; }
.app-empty > .material-icons { font-size: 3rem; color: var(--accent); }
.app-empty p { max-width: 440px; }
@media (max-width: 900px) { .miniapp__back { display: inline-flex; } .miniapp__head { padding: 8px; gap: 2px; } }
</style>
