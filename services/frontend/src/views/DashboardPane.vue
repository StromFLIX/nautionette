<template>
  <section class="dashboard-pane" data-skin-part="dashboard">
    <header class="pane-head dashboard-pane__head" data-skin-part="header">
      <button v-if="id" class="btn btn--icon dashboard-pane__back" aria-label="Back to dashboards" @click="backTo({ path: '/dashboards', query: route.query })"><span class="material-icons" aria-hidden="true">arrow_back</span></button>
      <div class="dashboard-pane__heading"><h1 class="pane-head__title">{{ view?.manifest.title || 'Dashboards' }}</h1><p v-if="view?.manifest.description" class="caption muted">{{ view.manifest.description }}</p></div>
      <template v-if="view">
        <button class="btn btn--icon" :disabled="pinBusy" :aria-label="view.manifest.pinned ? 'Unpin dashboard' : 'Pin dashboard'" :aria-pressed="view.manifest.pinned" @click="pin"><span class="material-icons" aria-hidden="true">{{ view.manifest.pinned ? 'push_pin' : 'add_to_home_screen' }}</span></button>
        <button class="btn btn--icon" :disabled="loading" aria-label="Refresh dashboard" @click="refresh"><span class="material-icons" aria-hidden="true">refresh</span></button>
      </template>
    </header>
    <p v-if="error || store.dashboardsError" class="dashboard-pane__notice" role="alert">{{ error || store.dashboardsError }}</p>
    <div v-if="!id" class="dashboard-pane__content scroll-y">
      <p class="muted">Agent-built interfaces, live read-only data, and workflow actions. Ask an agent to create or extend a dashboard; no code review is needed.</p>
      <div class="dashboard-index">
        <article v-for="(item, index) in items" :key="item.id" class="dashboard-card" data-skin-part="dashboard-card">
          <RouterLink :to="location(item)"><h2>{{ item.title }}</h2></RouterLink><p class="caption muted">{{ item.description || agentName(item.agent_id) }}</p>
          <div class="dashboard-index__controls"><span class="caption muted">{{ item.pinned ? 'Pinned' : 'Unpinned' }}</span><button class="btn btn--icon" :disabled="index === 0" :aria-label="`Move ${item.title} earlier`" @click="move(item.id, -1)"><span class="material-icons" aria-hidden="true">arrow_upward</span></button><button class="btn btn--icon" :disabled="index === items.length - 1" :aria-label="`Move ${item.title} later`" @click="move(item.id, 1)"><span class="material-icons" aria-hidden="true">arrow_downward</span></button></div>
        </article>
      </div>
      <p v-if="!items.length && store.ready && !store.dashboardsError" class="caption muted">No dashboards yet. Your agent can discover the component registry and publish one here.</p>
    </div>
    <div v-else-if="view" class="dashboard-pane__content scroll-y">
      <div class="dashboard-pane__meta caption muted"><span>{{ agentName(view.manifest.agent_id) }}</span><span>Revision {{ view.revision }}</span><span role="status">{{ connected ? 'Live updates' : 'Updates disconnected — actions paused' }}</span></div>
      <div class="dashboard-grid">
        <article v-for="widget in view.widgets" :key="widget.id" class="dashboard-card" data-skin-part="dashboard-card" :style="{ '--dashboard-span': widget.span }">
          <header class="dashboard-card__head"><h2 v-if="widget.title">{{ widget.title }}</h2><span v-if="widget.source" class="chip" :class="{ 'dashboard-card__stale': !['fresh', 'static'].includes(source(widget).state) }">{{ source(widget).state === 'static' ? 'Static / illustrative' : source(widget).state }}</span></header>
          <p v-if="widget.error" class="caption muted" role="status">{{ widget.error }}</p>
          <DashboardPart v-for="(part, index) in widget.parts" :key="index" :component="part.component" :props="part.props" :actions="view.actions" :runs="view.runs" :busy="!connected || Boolean(submitting)" @action="review" />
          <footer v-if="widget.source" class="dashboard-card__footer caption muted"><time v-if="source(widget).observed_at" :datetime="new Date(source(widget).observed_at * 1000).toISOString()">Observed {{ age(source(widget).observed_at) }}</time><span v-if="source(widget).error">{{ source(widget).error }}</span></footer>
        </article>
      </div>
      <p v-if="!view.widgets.length" class="muted">This dashboard has no cards yet. Ask its agent to add registered components.</p>
      <section v-if="view.runs.length" class="dashboard-pane__runs" aria-label="Dashboard workflow runs"><h2>Workflow activity</h2><article v-for="run in view.runs" :key="run.workflow_id" class="dashboard-run"><RouterLink :to="run.url">{{ run.workflow }}</RouterLink><span class="chip">{{ run.status }}</span><button v-if="['uncertain', 'starting'].includes(run.status)" class="btn" :disabled="Boolean(submitting) || !connected" @click="retry(run)">Retry same submission</button><details v-if="run.result"><summary>Result</summary><pre>{{ text(run.result) }}</pre></details></article></section>
    </div>
    <p v-else class="dashboard-pane__content muted" role="status">{{ loading ? 'Loading dashboard…' : 'Dashboard unavailable. Refresh or choose another dashboard.' }}</p>

    <q-dialog v-model="reviewOpen" aria-labelledby="dashboard-action-title">
      <q-card v-if="selected" class="dashboard-review">
        <form @submit.prevent="submit">
          <h2 id="dashboard-action-title">{{ selected.action.label }}</h2><p class="caption muted">Workflow: {{ selected.action.workflow }} · Dashboard revision {{ selected.revision }}</p>
          <p>{{ selected.action.confirmation || 'Review the exact workflow inputs before starting. This runs the deployed workflow; it does not edit the dashboard.' }}</p>
          <label v-for="(field, name) in selected.action.input_schema.properties" :key="name" class="dashboard-review__field">
            <span>{{ field.title || name }}{{ selected.action.input_schema.required?.includes(name) ? ' *' : '' }}</span>
            <select v-if="field.enum" v-model="form[name]" class="field" :disabled="Boolean(selected.pending)"><option v-for="option in field.enum" :key="String(option)" :value="option">{{ option }}</option></select>
            <input v-else-if="field.type === 'boolean'" v-model="form[name]" type="checkbox" :disabled="Boolean(selected.pending)" />
            <textarea v-else-if="['object', 'array'].includes(field.type)" v-model="form[name]" class="field" rows="3" :disabled="Boolean(selected.pending)" />
            <input v-else v-model="form[name]" class="field" :type="['number', 'integer'].includes(field.type) ? 'number' : 'text'" :step="field.type === 'integer' ? 1 : 'any'" :required="selected.action.input_schema.required?.includes(name)" :min="field.minimum" :max="field.maximum" :minlength="field.minLength" :maxlength="field.maxLength" :pattern="field.pattern" :disabled="Boolean(selected.pending)" />
            <small v-if="field.description" class="muted">{{ field.description }}</small>
          </label>
          <h3>Fixed and live-bound inputs</h3><pre>{{ text(selected.action.resolved_inputs) }}</pre>
          <p v-if="reviewStale && !selected.pending" class="dashboard-pane__notice" role="alert">Dashboard or source data changed. Close this review and reopen the action.</p>
          <p v-if="actionError" class="dashboard-pane__notice" role="alert">{{ actionError }}</p>
          <p v-if="selected.pending" class="caption muted">A submission may already exist. Retrying preserves its exact request ID and inputs; it will not start a replacement workflow.</p>
          <div class="dashboard-review__buttons"><button type="button" class="btn" :disabled="Boolean(submitting)" @click="reviewOpen = false">Close</button><button class="btn btn--primary" :disabled="Boolean(submitting) || !connected || (reviewStale && !selected.pending)" type="submit">{{ submitting ? 'Submitting…' : selected.pending ? 'Retry same submission' : `Run ${selected.action.label}` }}</button></div>
        </form>
      </q-card>
    </q-dialog>
  </section>
</template>

<script setup>
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { api, dashboardStream } from '../api'
import { actions, onLiveEvent, store } from '../store'
import { backTo } from '../router'
import { chatCacheScope } from '../chat-cache'
import { dashboardInputs, dashboardLocation as location, dashboardText as text } from '../dashboard'
import { moveDashboard, orderedDashboards } from '../dashboard-navigation'
import DashboardPart from '../components/DashboardPart.vue'

const route = useRoute()
const id = computed(() => route.params.id || '')
const view = ref(null), loading = ref(false), error = ref(''), connected = ref(false), pinBusy = ref(false)
const reviewOpen = ref(false), selected = ref(null), form = ref({}), actionError = ref(''), submitting = ref('')
const now = ref(Date.now())
const items = orderedDashboards(() => store.dashboards.filter(item => !route.query.agent || item.agent_id === route.query.agent))
const reviewStale = computed(() => !selected.value || view.value?.revision !== selected.value.revision || view.value?.actions[selected.value.id]?.context_hash !== selected.value.action.context_hash || !view.value?.actions[selected.value.id]?.enabled)
let stream = null, generation = 0, timer = null
function agentName (agentId) { return store.catalog.agents?.find(agent => agent.id === agentId)?.name || (agentId ? 'Unavailable agent' : 'Default agent') }
function source (widget) { const state = view.value.sources[widget.source] || { state: 'empty' }; return state.stale_after && now.value >= state.stale_after * 1000 ? { ...state, state: 'stale' } : state }
function age (at) { const seconds = Math.max(0, Math.round((now.value - at * 1000) / 1000)); return seconds < 60 ? `${seconds}s ago` : seconds < 3600 ? `${Math.floor(seconds / 60)}m ago` : `${Math.floor(seconds / 3600)}h ago` }
function receive (data, epoch) {
  if (epoch !== generation) return
  if (view.value && (data.revision < view.value.revision || (data.revision === view.value.revision && data.observed_at < view.value.observed_at))) return
  view.value = data; loading.value = false; error.value = ''; connected.value = true
}
function start () {
  stream?.close(); stream = null
  const epoch = ++generation
  connected.value = false; loading.value = Boolean(id.value)
  if (!id.value || document.hidden) return
  stream = dashboardStream(id.value, data => receive(data, epoch), failure => {
    if (epoch !== generation) return
    connected.value = false; loading.value = false; error.value = failure.message
    if (failure.status === 401) store.needsToken = true
    if (failure.permanent) { stream?.close(); stream = null }
  })
}
watch(id, () => { view.value = null; error.value = ''; reviewOpen.value = false; selected.value = null; start() }, { immediate: true })
async function refresh () {
  if (!id.value) return
  const epoch = generation
  loading.value = true
  try { receive(await api.refreshDashboard(id.value, AbortSignal.timeout(45000)), epoch) }
  catch (failure) { if (epoch === generation) error.value = failure.message }
  finally { if (epoch === generation) loading.value = false }
}
async function pin () {
  pinBusy.value = true
  const epoch = generation
  try { await api.pinDashboard(id.value, { expected_revision: view.value.revision, pinned: !view.value.manifest.pinned }); await actions.loadDashboards(); if (epoch === generation) await refresh() }
  catch (failure) { if (epoch === generation) error.value = failure.message }
  finally { pinBusy.value = false }
}
function move (dashboardId, direction) { try { moveDashboard(store.dashboards, dashboardId, direction) } catch { error.value = 'Could not save dashboard order on this device.' } }
function pendingKey (dashboardId, actionId) { return `nautionette.dashboard-submission:${chatCacheScope()}:${dashboardId}:${actionId}` }
function review (actionId) {
  const action = view.value?.actions[actionId]
  if (!action?.enabled || !connected.value) return
  let saved = null
  try { saved = JSON.parse(sessionStorage.getItem(pendingKey(id.value, actionId)) || 'null') } catch { /* unavailable storage is reported before submission */ }
  const pending = saved?.request || saved
  const reviewedAction = saved?.action || action
  selected.value = { id: actionId, action: JSON.parse(JSON.stringify(reviewedAction)), revision: pending?.expected_revision || view.value.revision, pending }
  form.value = Object.fromEntries(Object.entries(reviewedAction.input_schema.properties || {}).map(([key, field]) => {
    const value = pending?.inputs?.[key] ?? field.default ?? (field.type === 'boolean' ? false : '')
    return [key, ['object', 'array'].includes(field.type) && typeof value !== 'string' ? JSON.stringify(value) : value]
  }))
  actionError.value = ''; reviewOpen.value = true
}
async function send (dashboardId, actionId, payload) {
  const epoch = generation, storageKey = pendingKey(dashboardId, actionId)
  // Refuse to submit if its retry key cannot survive a reload in this tab.
  sessionStorage.setItem(storageKey, JSON.stringify({ request: payload, action: selected.value?.id === actionId ? selected.value.action : null }))
  submitting.value = actionId
  try {
    await api.dashboardAction(dashboardId, actionId, payload)
    sessionStorage.removeItem(storageKey)
    if (epoch === generation) { reviewOpen.value = false; await refresh() }
  } catch (failure) {
    if (failure.status >= 400 && failure.status < 500) sessionStorage.removeItem(storageKey)
    if (epoch === generation) {
      actionError.value = failure.message
      if (selected.value) selected.value.pending = failure.status >= 400 && failure.status < 500 ? null : payload
      if (!reviewOpen.value) error.value = failure.message
    }
  } finally { submitting.value = '' }
}
async function submit () {
  if (submitting.value) return
  try {
    const item = selected.value
    const payload = item.pending || { expected_revision: item.revision, request_id: crypto.randomUUID(), inputs: dashboardInputs(item.action.input_schema, form.value), context_hash: item.action.context_hash, confirmed: true }
    await send(id.value, item.id, payload)
  } catch (failure) { actionError.value = failure.message }
}
async function retry (run) {
  try { await send(id.value, run.action_id, run.request) } catch (failure) { error.value = failure.message }
}
function visibility () { if (document.hidden) { stream?.close(); stream = null; generation++; connected.value = false } else start() }
const unsubscribe = onLiveEvent(event => { if (event.kind === 'client.reconnect') { view.value = null; reviewOpen.value = false; selected.value = null; start() } })
onMounted(() => { document.addEventListener('visibilitychange', visibility); timer = setInterval(() => { now.value = Date.now() }, 5000) })
onUnmounted(() => { generation++; stream?.close(); unsubscribe(); document.removeEventListener('visibilitychange', visibility); clearInterval(timer) })
</script>

<style scoped>
.dashboard-pane { height: 100%; min-height: 0; display: flex; flex-direction: column; }
.dashboard-pane__head { flex: none; gap: .6rem; }
.dashboard-pane__heading { flex: 1; min-width: 0; }
.dashboard-pane__heading h1 { font-size: 1.15rem; margin: 0; overflow-wrap: anywhere; }
.dashboard-pane__heading p { margin: .2rem 0 0; }
.dashboard-pane__content { container-type: inline-size; flex: 1; min-height: 0; min-width: 0; padding: 1.25rem; }
.dashboard-pane__meta { display: flex; flex-wrap: wrap; gap: .8rem; margin-bottom: 1rem; }
.dashboard-pane__notice { padding: .75rem 1rem; margin: 0; color: var(--warning); background: var(--warning-soft); overflow-wrap: anywhere; }
.dashboard-grid { display: grid; grid-template-columns: repeat(12, minmax(0, 1fr)); gap: 1rem; align-items: start; }
.dashboard-card { grid-column: span var(--dashboard-span, 4); min-width: 0; padding: 1rem; border: 1px solid var(--border); border-radius: var(--radius-lg); background: var(--surface-panel); display: flex; flex-direction: column; gap: .8rem; }
.dashboard-card__head { display: flex; flex-wrap: wrap; justify-content: space-between; align-items: baseline; gap: .4rem; }
.dashboard-card h2, .dashboard-pane__runs h2 { font-size: .9rem; font-weight: 600; margin: 0; overflow-wrap: anywhere; }
.dashboard-card__stale { color: var(--warning); background: var(--warning-soft); }
.dashboard-card__footer { display: grid; gap: .25rem; padding-top: .5rem; border-top: 1px solid var(--border); overflow-wrap: anywhere; }
.dashboard-index { display: grid; grid-template-columns: repeat(auto-fit, minmax(min(100%, 16rem), 1fr)); gap: 1rem; margin-top: 1.5rem; }
.dashboard-index__controls { display: flex; align-items: center; gap: .25rem; }
.dashboard-index__controls > span { flex: 1; }
.dashboard-pane__runs { margin-top: 1.5rem; }
.dashboard-run { padding: .75rem 0; display: flex; align-items: baseline; gap: .7rem; flex-wrap: wrap; border-bottom: 1px solid var(--border); }
.dashboard-run details { width: 100%; }
.dashboard-review { width: min(34rem, 95vw); max-width: 95vw; padding: 1.5rem; color: var(--text); background: var(--surface-panel); border: 1px solid var(--border); border-radius: var(--radius-lg); }
.dashboard-review h2 { margin-top: 0; font-size: 1.2rem; }
.dashboard-review h3 { font-size: .85rem; }
.dashboard-review__field { display: grid; gap: .4rem; margin: 1rem 0; }
.dashboard-review__buttons { display: flex; justify-content: flex-end; gap: .5rem; margin-top: 1rem; }
:is(.dashboard-review, .dashboard-pane) pre { white-space: pre-wrap; overflow-wrap: anywhere; max-height: 14rem; overflow: auto; font-size: .75rem; background: var(--surface-code); padding: .75rem; border-radius: var(--radius-sm); }
.dashboard-pane__back { display: none; }
@container (max-width: 680px) { .dashboard-card { grid-column: 1 / -1; } }
@media (max-width: 1100px) { .dashboard-card { grid-column: span 6; } }
@media (max-width: 900px) { .dashboard-pane__back { display: inline-flex; } }
@media (max-width: 600px) { .dashboard-grid { grid-template-columns: minmax(0, 1fr); gap: .75rem; } .dashboard-card { grid-column: 1; } .dashboard-pane__content { padding: .75rem; } .dashboard-review { padding: 1rem; } }
</style>
