<template>
  <section id="app-runtimes" class="runtime-settings">
    <div class="runtime-settings__heading"><div><h2 class="settings__title">App runtimes</h2><p class="caption muted">Live resource samples. Sleeping apps stay asleep.</p></div><button class="btn btn--outline btn--sm" :disabled="loading" @click="load"><span class="material-icons" aria-hidden="true">refresh</span>Refresh</button></div>
    <p v-if="error" class="caption" role="alert">{{ error }}</p>
    <p v-if="loading && !overview" class="caption dim" role="status">Reading app resources…</p>
    <template v-if="overview">
      <div class="runtime-settings__summary" aria-label="App runtime totals">
        <div><strong>{{ overview.apps.filter(row => row.runtime.status === 'running').length }}</strong><span>Running / {{ overview.apps.length }} apps</span></div>
        <div><strong>{{ appBytes(overview.totals.memory_bytes) }}</strong><span>Memory in use</span></div>
        <div><strong>{{ appBytes(overview.totals.data_bytes) }}</strong><span>SQLite data</span></div>
        <div><strong>{{ appBytes(overview.totals.image_bytes) }}</strong><span>App images</span></div>
      </div>
      <p class="caption dim" role="status">{{ overview.incomplete ? 'Partial sample · some runtimes are building or unavailable.' : 'Updated ' + sampleTime }} · Shared compiler/cache not included.</p>
      <article v-for="row in overview.apps" :key="row.app.id" class="runtime-settings__app" :aria-label="row.app.title">
        <header><AppAvatar :app="row.app" /><div class="grow"><RouterLink :to="{ ...appLocation(row.app, row.app.agent_id), hash: '#info' }" class="runtime-settings__name">{{ row.app.title }}</RouterLink><p class="caption dim">{{ appOwner(row.app, store.catalog.agents) }} · v{{ row.app.active_revision || 'pending' }} · {{ row.runtime.mode || (row.runtime.status === 'frontend-only' ? 'frontend' : 'runtime') }}</p></div><span class="chip" :class="{ 'chip--success': row.runtime.status === 'running' && !row.app.archived, 'chip--danger': row.runtime.status === 'unavailable' }">{{ appState(row.runtime, row.app.archived) }}</span></header>
        <AppResources :runtime="row.runtime" />
        <p v-if="row.runtime.oom_killed" class="caption" role="alert">Memory limit reached. Inspect the app or increase its published limit.</p>
      </article>
      <p v-if="!overview.apps.length" class="caption muted">No apps published yet.</p>
    </template>
  </section>
</template>

<script setup>
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { api } from '../../api'
import { store } from '../../store'
import { appBytes, appLocation, appOwner, appState } from '../../miniapps'
import AppAvatar from '../AppAvatar.vue'
import AppResources from '../AppResources.vue'
const overview = ref(null), loading = ref(false), error = ref('')
const sampleTime = computed(() => new Date(overview.value.sampled_at * 1000).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' }))
let controller, timer
async function load () {
  if (loading.value) return
  loading.value = true; error.value = ''; controller = new AbortController()
  try { overview.value = await api.miniappOverview(controller.signal) }
  catch (e) { if (e.name !== 'AbortError') error.value = e.message }
  finally { loading.value = false }
}
function refreshVisible () { if (!document.hidden) load() }
onMounted(() => { load(); timer = setInterval(refreshVisible, 15000); document.addEventListener('visibilitychange', refreshVisible) })
onUnmounted(() => { clearInterval(timer); controller?.abort(); document.removeEventListener('visibilitychange', refreshVisible) })
</script>

<style scoped>
.runtime-settings__heading { display: flex; align-items: flex-start; justify-content: space-between; gap: 12px; margin-bottom: 20px; }
.runtime-settings__heading p { margin: 8px 0 0; }
.runtime-settings__summary { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 12px; }
.runtime-settings__summary > div { padding: 16px; border: 1px solid var(--border); border-radius: var(--radius-sm); background: var(--surface-panel); }
.runtime-settings__summary strong { display: block; font-size: 1.25rem; font-weight: 600; }
.runtime-settings__summary span { display: block; color: var(--text-dim); font-size: .6875rem; margin-top: 6px; }
.runtime-settings__app { border-top: 1px solid var(--border); padding-top: 20px; margin-top: 24px; }
.runtime-settings__app header { display: flex; align-items: center; gap: 12px; margin-bottom: 14px; }
.runtime-settings__app header .grow { min-width: 0; overflow-wrap: anywhere; }
.runtime-settings__app header p { margin: 4px 0 0; }
.runtime-settings__name { color: var(--text); font-weight: 600; text-decoration: none; }
.runtime-settings__name:hover { color: var(--accent); text-decoration: underline; }
@media (max-width: 760px) { .runtime-settings__summary { grid-template-columns: repeat(2, minmax(0, 1fr)); } }
</style>
