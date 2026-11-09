<template>
  <EmptyState v-if="!id" icon="history" title="Inspect an execution" description="Runs are the history of your workflows. Select a run to see its steps, inputs, and outcome, or open a workflow to start one." to="/workflows" action="Open workflows" />

  <div v-else-if="detail" class="stack grow run-pane">
    <header class="pane-head" data-skin-part="header">
      <button class="btn btn--icon pane-head__back" aria-label="Back to runs" @click="backTo('/runs')">
        <span class="material-icons">arrow_back</span>
      </button>
      <div class="run-identity" aria-hidden="true"><span class="material-icons">history</span></div>
      <div class="grow">
        <div class="pane-head__title truncate">{{ run?.workflow || temporal?.workflow_type || id }}</div>
        <div class="caption dim mono truncate">{{ id }}</div>
      </div>
      <span class="chip" :class="`chip--${RUN_TONE[status] || ''}`">{{ status }}</span>
      <RouterLink v-if="run?.workflow" class="btn btn--icon" :to="`/workflows/${run.workflow}`" title="Open workflow" aria-label="Open workflow">
        <span class="material-icons">account_tree</span>
      </RouterLink>
      <button v-if="isLive" class="btn btn--danger btn--sm" @click="cancel">Cancel</button>
    </header>

    <div v-if="detail.history_status === 'expired'" class="run-history-notice" role="status">
      <span class="material-icons" aria-hidden="true">history</span>
      <span>{{ detail.note }}</span>
    </div>
    <nav class="tabs" aria-label="Run views">
      <button v-for="item in ['Flow', 'Details', 'Statistics']" :key="item" class="tab" :class="{ 'tab--active': tab === item }" @click="tab = item">{{ item }}</button>
    </nav>
    <ExecutionFlow v-if="tab === 'Flow'" :key="id" :workflow-id="id" @status="updateStatus" @saved-details="tab = 'Details'" />
    <div v-else-if="tab === 'Statistics'" class="pane-body scroll-y grow">
      <RunStatistics :statistics="detail.statistics" />
    </div>
    <div v-else class="pane-body scroll-y grow">
      <section class="block">
        <div class="facts">
          <div class="fact-cell">
            <div class="section-label">Trigger</div>
            <div>{{ run?.trigger || '—' }}</div>
          </div>
          <div class="fact-cell">
            <div class="section-label">Started</div>
            <div>{{ fullTime(run?.created_at) || temporal?.start_time || '—' }}</div>
          </div>
          <div class="fact-cell">
            <div class="section-label">Finished</div>
            <div>{{ temporal?.close_time || (run?.status === 'running' ? 'still running' : '—') }}</div>
          </div>
        </div>
      </section>

      <section class="block">
        <h2 class="section-label">Input</h2>
        <pre class="code" style="margin-top: 8px">{{ pretty(run?.input) }}</pre>
      </section>

      <section class="block">
        <h2 class="section-label">Output</h2>
        <pre class="code" style="margin-top: 8px">{{ pretty(run?.result ?? temporal?.result) }}</pre>
      </section>

      <details class="block run-diagnostics">
        <summary>Temporal details</summary>
        <pre class="code" style="margin-top: 8px">{{ pretty(temporal) }}</pre>
      </details>
    </div>
  </div>

  <div v-else class="empty">
    <span>{{ loadError || 'Loading...' }}</span>
    <button v-if="loadError" class="btn btn--outline" @click="load">Retry</button>
  </div>
</template>

<script setup>
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { useQuasar } from 'quasar'
import { useRoute } from 'vue-router'
import { RUN_TONE, fullTime } from '../format'
import { backTo } from '../router'
import { actions, onLiveEvent } from '../store'
import { api } from '../api'
import ExecutionFlow from '../components/ExecutionFlow.vue'
import EmptyState from '../components/EmptyState.vue'
import RunStatistics from '../components/RunStatistics.vue'

const $q = useQuasar()
const route = useRoute()
const detail = ref(null)
const tab = ref('Flow')
const loadError = ref('')
let loadVersion = 0
let off = () => {}
let refreshTimer

const id = computed(() => route.params.id || '')
const run = computed(() => detail.value?.run)
const temporal = computed(() => detail.value?.temporal)
const status = computed(() => (temporal.value?.status || run.value?.status || 'unknown').toLowerCase())
const isLive = computed(() => detail.value?.history_status !== 'expired' && ['running', 'started'].includes(status.value))

async function load () {
  const version = ++loadVersion
  loadError.value = ''
  if (!id.value) return
  try {
    const result = await api.run(id.value)
    if (version === loadVersion) {
      detail.value = result
      if (result.history_status === 'expired' && tab.value === 'Flow') tab.value = 'Details'
    }
  } catch (error) {
    if (version === loadVersion) loadError.value = error.message
  }
}

function updateStatus (value) {
  if (detail.value?.temporal) detail.value.temporal.status = value.toUpperCase()
}

function pretty (value) {
  return value == null ? '—' : JSON.stringify(value, null, 2)
}

async function cancel () {
  try {
    await api.cancelRun(id.value)
    $q.notify({ type: 'positive', message: 'Cancellation requested' })
  } catch (error) {
    $q.notify({ type: 'negative', message: error.message })
  }
  load()
  actions.loadRuns()
}

watch(id, () => { detail.value = null; tab.value = 'Flow'; load() })
onMounted(() => {
  load()
  refreshTimer = setInterval(() => {
    if (tab.value === 'Statistics' && isLive.value && !document.hidden) load()
  }, 5000)
  off = onLiveEvent((event) => {
    if (event.workflow_id && event.workflow_id === id.value) load()
  })
})
onUnmounted(() => { off(); clearInterval(refreshTimer); loadVersion++ })
</script>

<style scoped>
.run-pane { min-width: 0; min-height: 0; }
.run-identity { display: grid; place-items: center; flex: none; width: 38px; height: 38px; border: 1px solid var(--border); border-radius: var(--radius-sm); color: var(--text-muted); background: var(--surface-panel); }
.run-identity .material-icons { font-size: 1.25rem; }
.pane-body {
  padding: 28px 32px 48px;
}
.pane-body .section-label { margin: 0 0 10px; font-size: 0.875rem; font-weight: 600; color: var(--text); text-transform: none; letter-spacing: 0; }

.block {
  max-width: 860px;
  margin-bottom: 28px;
}

.run-history-notice { display: flex; align-items: center; gap: 10px; padding: 12px 22px; background: var(--warning-soft); color: var(--warning); font-size: 0.8125rem; overflow-wrap: anywhere; }
.run-history-notice > .material-icons { font-size: 1.125rem; flex-shrink: 0; }

.run-diagnostics { padding-top: 20px; border-top: 1px solid var(--border); }
.run-diagnostics > summary { padding-block: 8px; font-size: 0.875rem; color: var(--text-muted); cursor: pointer; }

.facts {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(170px, 1fr));
  gap: 10px;
}

.fact-cell {
  padding: 16px;
  border: 1px solid var(--border);
  border-radius: var(--radius-md);
  background: var(--surface-panel);
  font-size: 0.875rem;
  line-height: 1.6;
  overflow-wrap: anywhere;
}

.fact-cell .section-label {
  color: var(--text-muted);
  font-size: 0.8125rem;
  font-weight: 500;
  margin-bottom: 8px;
}

.pane-head__back {
  display: none;
}

@media (max-width: 900px) {
  .pane-head__back {
    display: grid;
  }

  .pane-body {
    padding: 16px max(14px, env(safe-area-inset-right)) max(32px, env(safe-area-inset-bottom)) max(14px, env(safe-area-inset-left));
  }
}

@media (max-width: 420px) {
  .run-identity { display: none; }
  .pane-head .chip {
    max-width: 84px;
    overflow: hidden;
    text-overflow: ellipsis;
  }

  .pane-head .btn--danger {
    padding: 0 8px;
  }
}
</style>
