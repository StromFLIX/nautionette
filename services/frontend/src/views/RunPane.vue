<template>
  <EmptyState v-if="!id" icon="history" title="Every run, in view" description="Select a run to inspect its timeline and results." to="/workflows" action="Open workflows" />

  <div v-else-if="detail" class="stack grow">
    <header class="pane-head">
      <button class="btn btn--icon pane-head__back" aria-label="Back to runs" @click="backTo('/runs')">
        <span class="material-icons">arrow_back</span>
      </button>
      <div class="grow">
        <div class="pane-head__title truncate">{{ run?.workflow || temporal?.workflow_type || id }}</div>
        <div class="caption dim mono truncate">{{ id }}</div>
      </div>
      <span class="chip" :class="`chip--${RUN_TONE[status] || ''}`">{{ status }}</span>
      <RouterLink v-if="run?.workflow" class="btn btn--icon" :to="`/workflows/${run.workflow}`" title="Open workflow">
        <span class="material-icons">account_tree</span>
      </RouterLink>
      <button v-if="isLive" class="btn btn--danger btn--sm" @click="cancel">Cancel</button>
    </header>

    <nav class="tabs" aria-label="Run views">
      <button v-for="item in ['Flow', 'Details']" :key="item" class="tab" :class="{ 'tab--active': tab === item }" @click="tab = item">{{ item }}</button>
    </nav>
    <ExecutionFlow v-if="tab === 'Flow'" :key="id" :workflow-id="id" @status="updateStatus" />
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
        <div class="section-label">Input</div>
        <pre class="code" style="margin-top: 8px">{{ pretty(run?.input) }}</pre>
      </section>

      <section class="block">
        <div class="section-label">Output</div>
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

const $q = useQuasar()
const route = useRoute()
const detail = ref(null)
const tab = ref('Flow')
const loadError = ref('')
let loadVersion = 0
let off = () => {}

const id = computed(() => route.params.id || '')
const run = computed(() => detail.value?.run)
const temporal = computed(() => detail.value?.temporal)
const status = computed(() => (temporal.value?.status || run.value?.status || 'unknown').toLowerCase())
const isLive = computed(() => ['running', 'started'].includes(status.value))

async function load () {
  const version = ++loadVersion
  loadError.value = ''
  if (!id.value) return
  try {
    const result = await api.run(id.value)
    if (version === loadVersion) detail.value = result
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
  off = onLiveEvent((event) => {
    if (event.workflow_id && event.workflow_id === id.value) load()
  })
})
onUnmounted(() => off())
</script>

<style scoped>
.pane-body {
  padding: 18px 22px 40px;
}

.block {
  max-width: 860px;
  margin-bottom: 24px;
}

.run-diagnostics > summary { font-size: 12px; color: var(--text-muted); cursor: pointer; }

.facts {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(170px, 1fr));
  gap: 10px;
}

.fact-cell {
  padding: 11px 13px;
  border: 1px solid var(--border);
  border-radius: var(--radius-md);
  background: var(--surface-panel);
  font-size: 13px;
}

.fact-cell .section-label {
  margin-bottom: 3px;
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
