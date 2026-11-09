<template>
  <EmptyState v-if="!route.params.id" icon="apps" title="An interface for your task"
    :description="`Apps are interfaces your agent builds in ${brand.name}. Open one from the list, or ask your agent to build one in chat.`"
    to="/chats" action="Open chats">
    <RouterLink class="miniapp__runtime-link" to="/settings/app-runtimes">Manage app runtimes</RouterLink>
  </EmptyState>
  <section v-else class="miniapp stack grow" aria-label="Mini-app">
    <header class="miniapp__head row">
      <button class="btn btn--icon miniapp__back" aria-label="Back to apps" @click="backTo(appList)"><span class="material-icons">arrow_back</span></button>
      <AppAvatar v-if="item" :app="item" />
      <div class="grow miniapp__identity"><h1 class="truncate">{{ item?.title || 'Mini-app' }}</h1><p class="caption dim truncate">{{ owner }}<template v-if="active"> · v{{ active.revision }}</template></p></div>
      <button v-if="item" class="btn btn--icon" :class="{ 'miniapp__selected': item.pinned }" :disabled="busy || !active || !!item.archived" :aria-label="item.pinned ? 'Unpin app' : 'Pin app'" :aria-pressed="!!item.pinned" :title="item.pinned ? `Unpin v${item.pinned_revision || item.active_revision}` : `Pin v${item.active_revision} above chats`" @click="pin"><span class="material-icons">push_pin</span></button>
      <button class="btn btn--icon" :disabled="busy || loading" aria-label="Reload app" title="Reload app" @click="load(true)"><span class="material-icons">refresh</span></button>
      <button class="btn btn--icon" :class="{ 'miniapp__selected': showDetails }" aria-label="App details" title="App details" :aria-expanded="showDetails" aria-controls="miniapp-info" @click="toggleDetails"><span class="material-icons">{{ showDetails ? 'close' : 'info_outline' }}</span></button>
    </header>
    <p v-if="error" class="miniapp__notice caption" role="alert">{{ error }}</p>
    <div v-if="showDetails && item" id="miniapp-info" class="miniapp__details scroll-y grow">
      <div class="miniapp__info-inner">
        <div class="miniapp__section-head"><div><h2>Overview</h2><p v-if="active?.manifest.description" class="caption muted">{{ active.manifest.description }}</p></div><span class="chip" :class="{ 'chip--success': runtime?.status === 'running' && !item.archived }">{{ appState(runtime, item.archived) }}</span></div>
        <div class="miniapp__actions">
          <button v-if="!item.archived" class="btn btn--primary" @click="toggleDetails"><span class="material-icons" aria-hidden="true">open_in_new</span>Open app</button>
          <button v-if="active?.manifest.backend && !item.archived" class="btn btn--outline" :disabled="busy" @click="restart"><span class="material-icons" aria-hidden="true">restart_alt</span>Restart backend</button>
          <button class="btn btn--outline" :disabled="busy" @click="edit({ archived: !item.archived })"><span class="material-icons" aria-hidden="true">{{ item.archived ? 'unarchive' : 'archive' }}</span>{{ item.archived ? 'Unarchive' : 'Archive' }}</button>
          <RouterLink class="btn btn--outline" to="/settings/app-runtimes">All runtimes</RouterLink>
        </div>
        <section class="miniapp__section" aria-label="Resources">
          <div class="miniapp__section-head"><h2>Resources</h2><button class="btn btn--sm btn--outline" :disabled="sampling" @click="resources">Refresh usage</button></div>
          <AppResources :runtime="runtime" />
          <p v-if="resourceError" class="caption" role="alert">{{ resourceError }}</p>
          <p v-if="runtime?.oom_killed" class="caption" role="alert">Backend exceeded its memory limit.</p>
          <p class="caption dim miniapp__meta">{{ active?.manifest.backend?.mode || 'frontend-only' }}<template v-if="runtime?.started_at"> · Started {{ new Date(runtime.started_at).toLocaleString() }}</template></p>
        </section>
        <section class="miniapp__section" aria-label="App releases">
          <div class="miniapp__section-head"><h2>Releases</h2><span class="caption dim">Restore keeps current data</span></div>
          <div v-for="version in item.releases" :key="version.revision" class="miniapp__release">
            <div class="miniapp__release-row"><strong>v{{ version.revision }}</strong><span v-if="version.revision === item.active_revision" class="chip chip--success">Active</span><span v-else class="chip" :class="{ 'chip--danger': version.state === 'failed' }">{{ version.state }}</span><span v-if="item.pinned && (item.pinned_revision || item.active_revision) === version.revision" class="miniapp__pin-label"><span class="material-icons" aria-hidden="true">push_pin</span>Pinned</span><time v-if="version.created_at" class="caption dim">{{ new Date(version.created_at * 1000).toLocaleDateString() }}</time></div>
            <pre v-if="version.error" class="miniapp__error caption">{{ version.error }}</pre>
            <div v-if="version.state === 'ready' && !item.archived" class="miniapp__release-actions">
              <template v-if="version.revision === item.active_revision"><button class="btn btn--sm btn--outline" :disabled="busy" @click="restore(version.revision)">Rebuild code</button><button class="btn btn--sm" :class="item.pinned ? 'btn--outline' : 'btn--primary'" :disabled="busy" @click="pin">{{ item.pinned ? 'Unpin release' : `Pin v${version.revision}` }}</button></template>
              <template v-else><button class="btn btn--sm btn--outline" :disabled="busy" @click="restore(version.revision)">Restore code</button><button class="btn btn--sm btn--primary" :disabled="busy" @click="restore(version.revision, true)">Use &amp; pin</button></template>
            </div>
          </div>
          <p class="caption dim">The five most recently pinned apps appear above chats. New releases must be pinned explicitly.</p>
        </section>
        <details class="miniapp__section miniapp__disclosure"><summary>Permissions <span class="caption dim">{{ grants.length ? `${grants.length} grants` : 'No external access' }}</span></summary><ul class="miniapp__grants"><li v-for="(grant, index) in grants" :key="index"><strong>{{ grant.label }}</strong><pre v-if="grant.constraints" class="caption">{{ grant.constraints }}</pre></li></ul></details>
        <details :key="item.id" class="miniapp__section miniapp__disclosure" @toggle="diagnosticsToggle"><summary>Diagnostics <span class="caption dim">Builds &amp; recent requests</span></summary>
          <p v-if="diagnosticError" class="caption" role="alert">{{ diagnosticError }}</p>
          <p v-if="diagnosticsLoading" class="caption dim" role="status">Reading diagnostics…</p>
          <template v-if="diagnostics"><button class="btn btn--sm btn--outline" :disabled="diagnosticsLoading" @click="readDiagnostics">Refresh diagnostics</button><p v-if="!diagnostics.operations.length" class="caption dim">No recorded requests.</p><div v-for="(op, index) in diagnostics.operations" :key="index" class="miniapp__operation"><code>{{ op.operation }}</code><span class="caption" :class="{ 'miniapp__failed': op.outcome !== 'complete' }">{{ op.outcome }}</span><span class="caption dim">{{ op.duration_ms }} ms · v{{ op.revision }}</span></div><p v-if="uncertain" class="caption" role="alert">{{ uncertain }} uncertain mutations. Ask your agent to inspect state before retrying.</p></template>
        </details>
        <footer class="miniapp__footer"><code class="caption dim">{{ item.id }}</code><button v-if="item.archived" class="btn btn--outline btn--danger btn--sm" :disabled="busy" @click="remove">Delete app &amp; data</button></footer>
      </div>
    </div>
    <p v-if="loading && !item" class="miniapp__notice caption" role="status">Loading app…</p>
    <p v-else-if="item?.archived && !showDetails" class="miniapp__notice caption">This app is archived. <button class="btn btn--outline btn--sm" @click="toggleDetails">Manage app</button></p>
    <iframe v-if="active && !item.archived" v-show="!showDetails" ref="frame" :key="generation" class="miniapp__frame grow" :title="item.title" sandbox="allow-scripts" referrerpolicy="no-referrer" :srcdoc="document" @load="connect" />
    <p v-else-if="item && !showDetails && !item.archived" class="miniapp__notice caption">No healthy release yet. {{ item.releases[0]?.error }}</p>
  </section>
</template>

<script setup>
import { computed, onUnmounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { api } from '../api'
import { actions, onLiveEvent, store } from '../store'
import { currentTokens, preferences } from '../preferences'
import { backTo } from '../router'
import { connectMiniapp, miniappDocument } from '../miniapp-bridge'
import { appOwner, appState } from '../miniapps'
import AppAvatar from '../components/AppAvatar.vue'
import AppResources from '../components/AppResources.vue'
import EmptyState from '../components/EmptyState.vue'
import { brand } from '../brand'
const route = useRoute(), router = useRouter()
const item = ref(null), frame = ref(null), runtime = ref(null), diagnostics = ref(null)
const error = ref(''), resourceError = ref(''), diagnosticError = ref('')
const loading = ref(false), busy = ref(false), sampling = ref(false), diagnosticsLoading = ref(false), showDetails = ref(false), generation = ref(0)
const appList = computed(() => ({ path: '/apps', query: route.query.agent ? { agent: route.query.agent } : {} }))
const active = computed(() => item.value?.active)
const owner = computed(() => item.value ? appOwner(item.value, store.catalog.agents) : '')
const document = computed(() => miniappDocument(active.value?.manifest.frontend || {}))
const theme = computed(() => ({ tokens: Object.fromEntries(Object.entries(currentTokens.value).map(([key, value]) => [`--${key}`, value])), dark: globalThis.document.documentElement.dataset.colorScheme === 'dark', fontSize: `${preferences.interfaceSize}%` }))
const uncertain = computed(() => diagnostics.value?.mutations?.filter(op => op.state !== 'complete').length || 0)
const grants = computed(() => {
  const permissions = active.value?.manifest.backend?.permissions
  return [...(permissions?.mcp || []).map(grant => ({ label: grant.tool, constraints: Object.keys(grant.arguments || {}).length ? JSON.stringify(grant.arguments, null, 2) : 'All resources for this tool' })), ...(permissions?.http || []).map(grant => ({ label: `${grant.methods.join(', ')} ${grant.origin}${grant.path_prefix}`, constraints: '' }))]
})
let bridge, request, resourceRequest, diagnosticRequest, sequence = 0
function close () { bridge?.close(); bridge = null; request?.abort(); resourceRequest?.abort(); diagnosticRequest?.abort() }
async function load (reset = false) {
  const id = route.params.id, run = ++sequence
  request?.abort(); error.value = ''
  if (reset) { close(); item.value = null; runtime.value = null; diagnostics.value = null; generation.value++ }
  if (!id) { loading.value = false; return }
  loading.value = true; request = new AbortController()
  try {
    const data = await api.miniapp(id, request.signal)
    if (run !== sequence) return
    if (data.active_revision !== item.value?.active_revision || data.archived) { bridge?.close(); bridge = null; generation.value++ }
    item.value = data
    if (showDetails.value) resources()
  } catch (e) { if (run === sequence && e.name !== 'AbortError') error.value = e.message }
  finally { if (run === sequence) loading.value = false }
}
function connect () {
  bridge?.close()
  if (!frame.value || !item.value?.active || item.value.archived) return
  const id = item.value.id, revision = active.value.revision
  bridge = connectMiniapp(frame.value, (payload, signal) => api.miniappRequest(id, { revision, ...payload }, signal), theme.value)
}
watch(theme, value => bridge?.setTheme(value), { deep: true, flush: 'post' })
watch(() => route.params.id, () => { showDetails.value = route.hash === '#info'; resourceError.value = ''; diagnosticError.value = ''; load(true) }, { immediate: true })
watch(() => route.hash, hash => { showDetails.value = hash === '#info'; if (showDetails.value && item.value) resources() })
const unsubscribe = onLiveEvent(event => {
  if (event.kind === 'client.reconnect' || (event.kind === 'miniapp.changed' && event.data?.app_id === route.params.id)) load()
})
onUnmounted(() => { sequence++; close(); unsubscribe() })
function toggleDetails () { router.replace({ path: route.path, query: route.query, hash: showDetails.value ? '' : '#info' }) }
async function resources () {
  resourceRequest?.abort(); const controller = resourceRequest = new AbortController(), id = route.params.id
  sampling.value = true; resourceError.value = ''
  try { const data = await api.miniappStatus(id, controller.signal); if (id === route.params.id && resourceRequest === controller) runtime.value = data.runtime }
  catch (e) { if (e.name !== 'AbortError' && id === route.params.id && resourceRequest === controller) resourceError.value = e.message }
  finally { if (resourceRequest === controller) sampling.value = false }
}
function diagnosticsToggle (event) { if (event.target.open) readDiagnostics() }
async function readDiagnostics () {
  diagnosticRequest?.abort(); const controller = diagnosticRequest = new AbortController(), id = route.params.id
  diagnosticsLoading.value = true; diagnosticError.value = ''
  try { const data = await api.miniappDiagnostics(id, controller.signal); if (id === route.params.id && diagnosticRequest === controller) diagnostics.value = data }
  catch (e) { if (e.name !== 'AbortError' && id === route.params.id && diagnosticRequest === controller) diagnosticError.value = e.message }
  finally { if (diagnosticRequest === controller) diagnosticsLoading.value = false }
}
async function operate (fn) {
  if (busy.value || !item.value) return
  const current = item.value
  busy.value = true; error.value = ''
  try { await fn(current); await actions.loadMiniapps(); if (route.params.id === current.id) await load() }
  catch (e) { if (route.params.id === current.id) error.value = e.message }
  finally { busy.value = false }
}
function edit (patch) { return operate(current => api.editMiniapp(current.id, { expected_revision: current.revision, ...patch })) }
function pin () { return edit({ pinned: !item.value.pinned, pin_revision: item.value.active_revision }) }
function restore (revision, pinned = false) { return operate(current => api.restoreMiniapp(current.id, { expected_revision: current.revision, revision, pinned })) }
function restart () { return operate(async current => { await api.controlMiniapp(current.id, 'restart'); if (route.params.id === current.id) { bridge?.close(); bridge = null; generation.value++ } }) }
function remove () {
  if (!window.confirm('Permanently delete this app, retained releases and SQLite data?')) return
  return operate(async current => { await api.deleteMiniapp(current.id, current.revision); if (route.params.id === current.id) await router.push(appList.value) })
}
</script>

<style scoped>
.miniapp { min-width: 0; min-height: 0; height: 100%; }
.miniapp__head { flex: none; gap: 12px; padding: 14px 24px; border-bottom: 1px solid var(--border); background: var(--surface-panel); min-width: 0; }
.miniapp__identity { min-width: 0; }
.miniapp__head h1 { margin: 0; font-size: 1rem; font-weight: 600; letter-spacing: -0.02em; line-height: 1.4; }
.miniapp__head p { margin: 4px 0 0; }
.miniapp__selected { background: var(--accent-soft); color: var(--accent); }
.miniapp__frame { border: 0; width: 100%; min-height: 0; background: var(--surface-app); }
.miniapp__notice { padding: 12px 18px; margin: 0; }
.miniapp__details { min-height: 0; padding: 32px; background: var(--surface-app); overscroll-behavior-y: contain; }
.miniapp__info-inner { max-width: 880px; margin: auto; }
.miniapp__info-inner .caption { line-height: 1.7; }
.miniapp__section { margin-top: 28px; padding-top: 22px; border-top: 1px solid var(--border); }
.miniapp__section-head { display: flex; align-items: center; justify-content: space-between; gap: 12px; margin-bottom: 16px; }
.miniapp__section-head h2 { margin: 0; font-size: 1rem; font-weight: 600; letter-spacing: -0.02em; }
.miniapp__section-head p { margin: 8px 0 0; }
.miniapp__actions { display: flex; flex-wrap: wrap; gap: 8px; }
.miniapp__actions .material-icons { font-size: 1.125rem; }
.miniapp__actions a { text-decoration: none; }
.miniapp__release { padding: 18px; margin-bottom: 12px; border: 1px solid var(--border); border-radius: var(--radius-md); background: var(--surface-panel); }
.miniapp__release-row { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }
.miniapp__release-row strong { font-size: .875rem; }
.miniapp__release-row time { margin-left: auto; }
.miniapp__release-actions { display: flex; gap: 8px; margin-top: 12px; flex-wrap: wrap; }
.miniapp__pin-label { display: inline-flex; gap: 3px; align-items: center; color: var(--accent); font-size: .6875rem; }
.miniapp__pin-label .material-icons { font-size: .875rem; }
.miniapp__error { white-space: pre-wrap; overflow-wrap: anywhere; max-height: 240px; overflow-y: auto; color: var(--danger); padding: 12px; background: var(--surface-panel); border-radius: var(--radius-xs); }
.miniapp__meta { margin: 12px 0 0; }
.miniapp__disclosure summary { padding-block: 6px; cursor: pointer; font-size: .9375rem; font-weight: 600; line-height: 1.6; }
.miniapp__disclosure summary span { margin-left: 8px; font-weight: 400; }
.miniapp__disclosure[open] summary { margin-bottom: 16px; color: var(--accent); }
.miniapp__grants { list-style: none; padding: 0; }
.miniapp__grants li { padding: 10px 0; font-size: .75rem; overflow-wrap: anywhere; }
.miniapp__grants pre { white-space: pre-wrap; margin: 4px 0 0; color: var(--text-dim); }
.miniapp__operation { display: flex; flex-wrap: wrap; align-items: center; gap: 10px; padding: 10px 0; border-bottom: 1px solid var(--border); }
.miniapp__operation code { font-size: .75rem; overflow-wrap: anywhere; margin-right: auto; }
.miniapp__failed { color: var(--danger); }
.miniapp__footer { display: flex; align-items: center; flex-wrap: wrap; justify-content: space-between; gap: 12px; margin-top: 28px; }
.miniapp__back { display: none; }
.miniapp__runtime-link { color: var(--text-muted); font-size: .8125rem; text-underline-offset: 4px; }
.miniapp__runtime-link:focus-visible, .miniapp__disclosure summary:focus-visible { outline: 2px solid var(--accent); outline-offset: 4px; }
@media (max-width: 900px) { .miniapp__back { display: inline-flex; } .miniapp__head { padding: calc(8px + env(safe-area-inset-top)) 8px 8px; gap: 4px; --app-avatar-size: 30px; } .miniapp__details { padding: 20px 14px max(24px, env(safe-area-inset-bottom)); } .miniapp__section-head { align-items: flex-start; } }
@media (max-width: 380px) { .miniapp__head > .app-avatar { display: none; } }
</style>
