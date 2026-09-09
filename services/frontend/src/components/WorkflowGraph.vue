<template>
  <section ref="container" class="flow" :class="{ 'flow--expanded': expanded }" aria-label="Workflow flow">
    <div class="flow__toolbar">
      <div class="flow__source">
        <slot name="source">
          <span class="flow__eyebrow">{{ graph?.mode === 'execution' ? 'Execution history' : graph?.mode === 'changes' ? 'Draft changes' : 'Definition' }}</span>
        </slot>
      </div>
      <div class="flow__tools">
        <button class="btn btn--icon" :class="{ 'flow__pressed': searching }" aria-label="Search steps" @click="searching = !searching">
          <span class="material-icons">search</span><q-tooltip>Search steps</q-tooltip>
        </button>
        <button class="btn btn--icon" aria-label="Change flow direction" @click="rotate">
          <span class="material-icons">{{ direction === 'TB' ? 'swap_horiz' : 'swap_vert' }}</span><q-tooltip>Change direction</q-tooltip>
        </button>
        <button class="btn btn--icon" :aria-label="expanded ? 'Exit full screen' : 'Full screen'" @click="expanded = !expanded">
          <span class="material-icons">{{ expanded ? 'fullscreen_exit' : 'fullscreen' }}</span><q-tooltip>{{ expanded ? 'Exit full screen' : 'Full screen' }}</q-tooltip>
        </button>
      </div>
    </div>

    <div v-if="searching" class="flow__search">
      <span class="material-icons">search</span>
      <input v-model="search" class="field" placeholder="Search steps" aria-label="Search steps" @keydown.enter="nextMatch" />
      <span class="caption dim">{{ matches.length }}</span>
      <button class="btn btn--icon" aria-label="Next matching step" :disabled="!matches.length" @click="nextMatch">
        <span class="material-icons">arrow_downward</span><q-tooltip>Next match</q-tooltip>
      </button>
    </div>

    <div v-if="graph?.mode === 'changes'" class="flow__changes">
      <span v-for="change in ['added', 'changed', 'removed']" :key="change" :class="`flow__change flow__change--${change}`">
        {{ graph.nodes.filter((node) => node.change === change).length }} {{ change }}
      </span>
    </div>
    <div v-for="warning in graph?.warnings || []" :key="warning" class="flow__warning">
      <span class="material-icons">info_outline</span><span>{{ warning }}</span>
    </div>

    <div class="flow__workspace">
      <div class="flow__canvas">
        <VueFlow
          :id="flowId" :nodes="nodes" :edges="edges" :nodes-draggable="false" :nodes-connectable="false"
          :edges-updatable="false" :delete-key-code="null" :min-zoom="0.1" :max-zoom="1.8"
          :zoom-on-double-click="false" :connect-on-click="false" :select-nodes-on-drag="false"
          @node-click="({ node }) => selectedId = node.id" @pane-click="selectedId = null"
          @nodes-initialized="initialize"
        >
          <Background :gap="22" :size="1" pattern-color="var(--border-strong)" />
          <template #node-loop="{ data, sourcePosition, targetPosition }">
            <Handle type="target" :position="targetPosition" />
            <div class="flow-loop" :class="[
              data.change && `flow-loop--${data.change}`,
              { 'flow-loop--selected': selectedId === data.id, 'flow-node--dimmed': search && !matches.includes(data.id) }
            ]">
              <header class="flow-loop__header" :title="data.label">
                <div class="flow-node__top">
                  <span class="flow-node__kind"><span class="material-icons">repeat</span>Loop</span>
                  <span v-if="data.change" :class="`flow__change--${data.change}`">{{ data.change }}</span>
                  <span v-else class="caption dim">{{ data.line ? `Line ${data.line}` : '' }}</span>
                </div>
                <div class="flow-loop__name">{{ data.label }}</div>
              </header>
            </div>
            <Handle type="source" :position="sourcePosition" />
          </template>
          <template #node-operation="{ data, sourcePosition, targetPosition }">
            <Handle type="target" :position="targetPosition" />
            <div
              class="flow-node" :class="[
                `flow-node--${data.kind}`, `flow-node--${data.status || 'definition'}`,
                data.change && `flow-node--${data.change}`,
                { 'flow-node--selected': selectedId === data.id, 'flow-node--dimmed': search && !matches.includes(data.id) }
              ]"
              :title="data.label"
            >
              <div class="flow-node__top">
                <span class="flow-node__kind"><span class="material-icons">{{ icon(data.activity_type || data.kind) }}</span>{{ data.category || kindLabel(data.kind) }}</span>
                <span v-if="data.change" :class="`flow__change--${data.change}`">{{ data.change }}</span>
                <span v-else-if="data.status" class="flow-node__status"><i />{{ data.status.replaceAll('_', ' ') }}</span>
                <span v-else class="material-icons flow-node__inspect">open_in_full</span>
              </div>
              <div class="flow-node__name">{{ data.title || data.label }}</div>
              <dl v-if="data.details?.length" class="flow-node__details">
                <template v-for="detail in previewDetails(data)" :key="detail.label">
                  <dt>{{ detail.label }}</dt><dd :title="detail.value" :class="{ 'flow-node__expression': detail.dynamic }">{{ detail.value }}</dd>
                </template>
              </dl>
              <div class="flow-node__bottom">
                <span>{{ data.activity_type || data.task_queue || data.description || (data.line ? `Line ${data.line}` : data.workflow_id || '') }}</span>
                <span v-if="data.attempt > 1">Attempt {{ data.attempt }}</span>
                <span v-else>{{ duration(data.started_at, data.finished_at, now) }}</span>
              </div>
            </div>
            <Handle type="source" :position="sourcePosition" />
          </template>
        </VueFlow>

        <div class="flow__zoom">
          <button class="btn btn--icon" aria-label="Zoom out" @click="zoomOut()"><span class="material-icons">remove</span><q-tooltip>Zoom out</q-tooltip></button>
          <span class="flow__scale">{{ Math.round(viewport.zoom * 100) }}%</span>
          <button class="btn btn--icon" aria-label="Zoom in" @click="zoomIn()"><span class="material-icons">add</span><q-tooltip>Zoom in</q-tooltip></button>
          <span class="flow__separator" />
          <button class="btn btn--icon" aria-label="Fit all steps" @click="fitAll"><span class="material-icons">fit_screen</span><q-tooltip>Fit all steps</q-tooltip></button>
          <button v-if="activeNode" class="btn btn--icon flow__active-control" aria-label="Focus active step" @click="focus(activeNode.id)"><span class="material-icons">my_location</span><q-tooltip>Focus active step</q-tooltip></button>
        </div>
        <div v-if="!graph?.nodes?.length" class="flow__empty">No diagram available.</div>
      </div>

      <aside v-if="selected" class="flow__inspector scroll-y" aria-label="Step details">
        <header class="flow__inspector-head">
          <span class="flow__eyebrow">{{ selected.category || kindLabel(selected.kind) }}</span>
          <button class="btn btn--icon" aria-label="Close step details" @click="selectedId = null"><span class="material-icons">close</span></button>
        </header>
        <h2>{{ selected.title || selected.label }}</h2>
        <span v-if="selected.change" class="flow__change" :class="`flow__change--${selected.change}`">{{ selected.change }}</span>
        <dl class="flow__facts">
          <template v-for="(value, label) in facts" :key="label"><dt>{{ label }}</dt><dd>{{ value }}</dd></template>
          <template v-for="detail in selected.details || []" :key="detail.label">
            <dt>{{ detail.label }}</dt><dd>{{ detail.value }}<span v-if="detail.dynamic" class="flow__expression-label">Expression</span></dd>
          </template>
        </dl>
        <RouterLink v-if="selected.kind === 'child' && selected.workflow_id" class="btn btn--outline" :to="`/runs/${encodeURIComponent(selected.workflow_id)}`">
          <span class="material-icons">open_in_new</span>Open child run
        </RouterLink>
        <section v-for="field in payloadFields" :key="field.key" class="flow__payload">
          <h3>{{ field.label }}</h3>
          <pre :class="{ 'flow__error': field.key === 'error' }">{{ pretty(selected[field.key]) }}</pre>
        </section>
      </aside>
    </div>
    <footer class="flow__footer">
      <span>{{ graph?.nodes?.length || 0 }} steps</span>
      <span v-if="graph?.mode === 'execution'" class="flow__live" :class="{ 'flow__live--running': graph.status === 'running' && !livePaused }">
        <i />{{ livePaused ? 'Updates paused' : graph.status === 'running' ? 'Live' : graph.status?.replaceAll('_', ' ') }}<span class="flow__completed">{{ completed }} completed</span>
      </span>
      <span v-else>{{ graph?.mode === 'changes' ? 'Proposed revision' : 'Source preview' }}</span>
    </footer>
  </section>
</template>

<script setup>
import { computed, nextTick, onMounted, onUnmounted, ref, useId, watch } from 'vue'
import { VueFlow, Handle, useVueFlow } from '@vue-flow/core'
import { Background } from '@vue-flow/background'
import { duration, layoutGraph } from '../flow'
import { preferences, reducedMotion } from '../preferences'
import '@vue-flow/core/dist/style.css'
import '@vue-flow/core/dist/theme-default.css'

const props = defineProps({ graph: { type: Object, default: null }, livePaused: Boolean })
const flowId = useId()
const { fitView, setViewport, zoomIn, zoomOut, viewport, dimensions } = useVueFlow({ id: flowId })
const container = ref(null)
const direction = ref(preferences.flowDirection)
const expanded = ref(false)
const selectedId = ref(null)
const searching = ref(false)
const search = ref('')
const now = ref(Date.now())
let clock
let initialized = false
const positioned = computed(() => layoutGraph(props.graph, direction.value, preferences.interfaceSize / 100))
const nodes = computed(() => positioned.value.nodes.map((node) => ({ ...node, ariaLabel: `${node.data.label}, ${node.data.status || node.data.kind}` })))
const edges = computed(() => positioned.value.edges.map((edge) => {
  const target = props.graph.nodes.find((node) => node.id === edge.target)
  const color = edge.change === 'removed' ? 'var(--danger)' : edge.change === 'added' ? 'var(--success)' : 'var(--text-dim)'
  return {
    ...edge,
    animated: ['running', 'retrying'].includes(target?.status),
    style: { stroke: color, strokeWidth: 1.5, strokeDasharray: edge.change === 'removed' ? '5 5' : undefined },
    markerEnd: { type: 'arrowclosed', color, width: 14, height: 14 },
    labelStyle: { fill: 'var(--text-muted)', fontSize: '0.6875rem' },
    labelBgStyle: { fill: 'var(--surface-app)' }, labelBgPadding: [6, 4]
  }
}))
const selected = computed(() => props.graph?.nodes.find((node) => node.id === selectedId.value))
const activeNode = computed(() => props.graph?.nodes.find((node) => node.kind !== 'workflow' && ['running', 'retrying', 'waiting', 'scheduled'].includes(node.status)))
const completed = computed(() => props.graph?.nodes.filter((node) => !['workflow', 'return'].includes(node.kind) && node.status === 'completed').length || 0)
const matches = computed(() => (props.graph?.nodes || []).filter((node) => [node.label, node.title, ...(node.details || []).map((detail) => detail.value)].join(' ').toLowerCase().includes(search.value.toLowerCase())).map((node) => node.id))
const facts = computed(() => {
  if (!selected.value) return {}
  const node = selected.value
  return Object.fromEntries(Object.entries({
    Status: node.status?.replaceAll('_', ' '), Queue: node.task_queue, Attempt: node.attempt,
    Scheduled: node.scheduled_at, Started: node.started_at, Finished: node.finished_at,
    Duration: duration(node.started_at, node.finished_at, now.value), Line: node.line,
    'Workflow ID': node.workflow_id, 'Run ID': node.run_id, Activity: node.activity_type || node.activity_id,
    'Activity ID': node.activity_type ? node.activity_id : null,
    'Next run': node.new_execution_run_id
  }).filter(([, value]) => value != null && value !== ''))
})
const payloadFields = computed(() => [
  { key: 'error', label: 'Error' }, { key: 'input', label: 'Input' }, { key: 'result', label: 'Result' },
  { key: 'before_code', label: 'Deployed code' }, { key: 'code', label: selected.value?.change === 'changed' ? 'Proposed code' : 'Source' }
].filter((field) => selected.value?.[field.key] != null))

function icon (kind) {
  return { agent_call: 'smart_toy', mcp_call: 'extension', http_fetch: 'http', save_artifact: 'save_alt', read_artifact: 'description', emit_event: 'sensors', workflow: 'account_tree', activity: 'bolt', child: 'account_tree', condition: 'call_split', loop: 'repeat', parallel: 'call_split', join: 'call_merge', timer: 'schedule', return: 'flag', error: 'error_outline', signal: 'sensors', continue: 'autorenew', step: 'code' }[kind] || 'code'
}

function kindLabel (kind) {
  return { child: 'Child workflow', return: 'Result', join: 'Join', step: 'Source block', continue: 'Continue as new' }[kind] || kind
}

function pretty (value) { return typeof value === 'string' ? value : JSON.stringify(value, null, 2) }

function previewDetails (node) {
  const repeated = { agent_call: 'Agent set', mcp_call: 'Tool', http_fetch: 'Method' }[node.activity_type]
  return node.details.filter((detail) => detail.label !== repeated).slice(0, 2)
}

async function focus (id) {
  await nextTick()
  const node = nodes.value.find((item) => item.id === id)
  if (!node) return
  const canvas = container.value.querySelector('.flow__canvas').getBoundingClientRect()
  const inspector = container.value.querySelector('.flow__inspector')?.getBoundingClientRect()
  const visibleHeight = inspector && inspector.left < canvas.right && inspector.top > canvas.top
    ? inspector.top - canvas.top : canvas.height
  const zoom = Math.max(0.1, Math.min(1, (canvas.width - 32) / node.width, (visibleHeight - 48) / node.height))
  setViewport({
    x: canvas.width / 2 - (node.absolutePosition.x + node.width / 2) * zoom,
    y: visibleHeight / 2 - (node.absolutePosition.y + node.height / 2) * zoom,
    zoom
  }, { duration: reducedMotion() ? 0 : 200 })
}

function initialize () {
  if (initialized || !nodes.value.length) return
  initialized = true
  const root = nodes.value[0]
  const active = nodes.value.find((node) => node.id === activeNode.value?.id)
  const zoom = Math.max(0.1, Math.min(1, (dimensions.value.width - 48) / (active?.width || root.width), (dimensions.value.height - 60) / (active?.height || root.height)))
  if (active && props.graph?.mode === 'execution') {
    const top = Math.min(36 - root.absolutePosition.y * zoom, dimensions.value.height - 24 - (active.absolutePosition.y + active.height) * zoom)
    setViewport({ x: dimensions.value.width / 2 - (active.absolutePosition.x + active.width / 2) * zoom, y: top, zoom })
    return
  }
  setViewport({ x: dimensions.value.width / 2 - (root.position.x + root.width / 2) * zoom, y: 36 - root.position.y * zoom, zoom })
}

async function fitAll () {
  await nextTick()
  fitView({ padding: 0.12, maxZoom: 1, duration: 0 })
}

async function rotate () {
  direction.value = direction.value === 'TB' ? 'LR' : 'TB'
  await fitAll()
}

function nextMatch () {
  const index = matches.value.indexOf(selectedId.value)
  selectedId.value = matches.value[(index + 1) % matches.value.length]
  focus(selectedId.value)
}

function escape (event) {
  if (event.key !== 'Escape') return
  if (selectedId.value) selectedId.value = null
  else expanded.value = false
}

watch(() => props.graph?.mode, () => { initialized = false; selectedId.value = null; nextTick(initialize) })
watch(searching, (value) => { if (!value) search.value = '' })
watch(() => preferences.flowDirection, async value => { direction.value = value; await fitAll() })
onMounted(() => {
  clock = setInterval(() => { now.value = Date.now() }, 1000)
  document.addEventListener('keydown', escape)
})
onUnmounted(() => { clearInterval(clock); document.removeEventListener('keydown', escape) })
</script>

<style scoped>
.flow { display: flex; flex: 1; flex-direction: column; min-width: 0; min-height: 0; background: var(--surface-app); }
.flow--expanded { position: fixed; inset: 0; height: var(--app-height); z-index: 5000; padding: env(safe-area-inset-top) env(safe-area-inset-right) env(safe-area-inset-bottom) env(safe-area-inset-left); }
.flow__toolbar { display: flex; flex: none; align-items: center; justify-content: space-between; gap: 8px; min-height: 52px; padding: 8px 16px; border-bottom: 1px solid var(--border); background: var(--surface-panel); }
.flow__source { min-width: 0; flex: 1; }
.flow__eyebrow { font-size: 0.6875rem; font-weight: 600; color: var(--text-muted); text-transform: uppercase; letter-spacing: 0; }
.flow__tools { display: flex; gap: 2px; flex: none; }
.flow .btn--icon { width: 34px; height: 34px; }
.flow .btn .material-icons { font-size: 1.25rem; }
.flow__pressed { color: var(--accent); background: var(--accent-soft); }
.flow__workspace { display: flex; flex: 1; min-height: 0; position: relative; }
.flow__canvas { position: relative; flex: 1; min-width: 0; min-height: 180px; }
.flow :deep(.vue-flow) { position: absolute; inset: 0; }
.flow :deep(.vue-flow__handle) { width: 6px; height: 6px; background: var(--text-dim); border: 1px solid var(--surface-panel); }
.flow :deep(.vue-flow__node) { border: none; border-radius: 8px; width: 264px; padding: 0; background: transparent; box-shadow: none; }
.flow :deep(.vue-flow__node:focus-visible) { outline: 2px solid var(--accent); outline-offset: 4px; }
.flow :deep(.vue-flow__edge-path) { transition: stroke 160ms; }
.flow-node { --node-color: var(--text-muted); display: flex; flex-direction: column; width: 100%; height: 100%; padding: 0.75rem 0.875rem 0.625rem; border: 1px solid var(--border-strong); border-left: 3px solid var(--node-color); border-radius: var(--radius-sm); background: var(--surface-panel); color: var(--text); box-shadow: var(--shadow-sm); cursor: pointer; transition: border-color 140ms, box-shadow 140ms; text-align: left; }
.flow-loop { --node-color: var(--warning); width: 100%; height: 100%; border: 1px dashed color-mix(in srgb, var(--warning) 65%, var(--border)); border-radius: 8px; background: color-mix(in srgb, var(--warning) 3%, transparent); cursor: pointer; }
.flow-loop__header { height: 6.625rem; padding: 0.875rem 1.125rem; border-bottom: 1px dashed color-mix(in srgb, var(--warning) 35%, var(--border)); background: color-mix(in srgb, var(--warning) 7%, var(--surface-app)); border-radius: 8px 8px 0 0; }
.flow-loop__name { display: -webkit-box; -webkit-line-clamp: 2; line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden; overflow-wrap: anywhere; font-size: 0.875rem; line-height: 1.3125rem; font-weight: 600; margin-top: 8px; }
.flow-loop--selected { border-color: var(--accent); box-shadow: 0 0 0 2px var(--accent-soft); }
.flow-loop--added { border-color: var(--success); }
.flow-loop--removed { border-color: var(--danger); }
.flow-loop--changed { border-color: var(--warning); }
.flow-node--workflow, .flow-node--child { --node-color: var(--accent-hover); }
.flow-node--activity { --node-color: var(--flow-activity); }
.flow-node--condition, .flow-node--loop, .flow-node--timer { --node-color: var(--warning); }
.flow-node--signal { --node-color: var(--flow-signal); }
.flow-node--completed, .flow-node--added { border-color: var(--success); }
.flow-node--running { border-color: var(--accent); box-shadow: 0 0 0 3px var(--accent-soft), var(--shadow-md); }
.flow-node--failed, .flow-node--timed_out, .flow-node--removed { border-color: var(--danger); }
.flow-node--removed { border-style: dashed; background: color-mix(in srgb, var(--danger-soft) 25%, var(--surface-panel)); }
.flow-node--changed, .flow-node--retrying { border-color: var(--warning); }
.flow-node:hover, .flow-node--selected { box-shadow: 0 0 0 3px var(--accent-soft), var(--shadow-md); border-color: var(--accent-hover); }
.flow-node--dimmed { opacity: 0.3; }
.flow-node__top { display: flex; align-items: center; justify-content: space-between; gap: 6px; font-size: 0.625rem; line-height: 1rem; }
.flow-node__kind { display: flex; align-items: center; gap: 4px; color: var(--node-color); text-transform: uppercase; font-weight: 600; }
.flow-node__kind .material-icons { font-size: 0.9375rem; }
.flow-node__status { display: flex; align-items: center; gap: 4px; color: var(--text-muted); }
.flow-node__status i, .flow__live i { display: inline-block; width: 5px; height: 5px; border-radius: 50%; background: currentColor; }
.flow-node--running .flow-node__status { color: var(--accent-hover); }
.flow-node--running .flow-node__status i, .flow__live--running i { animation: flow-pulse 1.8s ease-in-out infinite; }
.flow-node--completed .flow-node__status { color: var(--success); }
.flow-node--failed .flow-node__status, .flow-node--timed_out .flow-node__status { color: var(--danger); }
.flow-node__inspect { font-size: 0.875rem; color: var(--text-dim); }
.flow-node__name { display: -webkit-box; -webkit-line-clamp: 2; line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden; overflow-wrap: anywhere; font-size: 0.875rem; font-weight: 600; line-height: 1.1875rem; height: 2.375rem; margin: 6px 0; }
.flow-node__details { display: grid; grid-template-columns: auto minmax(0, 1fr); gap: 4px 8px; margin: 0 0 8px; font-size: 0.6875rem; line-height: 1rem; }
.flow-node__details dt { color: var(--text-dim); }
.flow-node__details dd { margin: 0; overflow: hidden; white-space: nowrap; text-overflow: ellipsis; color: var(--text-muted); }
.flow-node__expression { font-family: monospace; }
.flow-node__bottom { display: flex; justify-content: space-between; gap: 8px; margin-top: auto; color: var(--text-dim); font-size: 0.625rem; line-height: 0.9375rem; }
.flow-node__bottom span { overflow: hidden; white-space: nowrap; text-overflow: ellipsis; }
.flow-node__bottom span:last-child { flex: none; }
.flow__zoom { position: absolute; bottom: 18px; left: 50%; transform: translateX(-50%); display: flex; align-items: center; gap: 2px; padding: 4px; border: 1px solid var(--border-strong); border-radius: 8px; background: var(--surface-raised); box-shadow: var(--shadow-md); }
.flow__scale { width: 42px; text-align: center; font-size: 0.6875rem; font-variant-numeric: tabular-nums; color: var(--text-muted); }
.flow__separator { width: 1px; height: 18px; background: var(--border-strong); margin: 0 4px; }
.flow__active-control { color: var(--accent-hover); }
.flow__footer { display: flex; justify-content: space-between; align-items: center; gap: 8px; flex: none; min-height: 34px; padding: 7px 16px; border-top: 1px solid var(--border); font-size: 0.625rem; color: var(--text-dim); }
.flow__live { display: flex; align-items: center; gap: 6px; text-transform: capitalize; }
.flow__live--running { color: var(--accent-hover); }
.flow__completed { color: var(--text-dim); margin-left: 8px; }
.flow__warning { display: flex; align-items: flex-start; gap: 7px; padding: 7px 16px; color: var(--warning); background: var(--warning-soft); font-size: 0.6875rem; overflow-wrap: anywhere; }
.flow__warning .material-icons { font-size: 0.9375rem; flex: none; }
.flow__search { display: flex; align-items: center; gap: 8px; padding: 8px 16px; border-bottom: 1px solid var(--border); }
.flow__search > .material-icons { font-size: 1.125rem; color: var(--text-dim); }
.flow__search input { flex: 1; min-width: 0; }
.flow__changes { display: flex; gap: 16px; padding: 8px 16px; border-bottom: 1px solid var(--border); }
.flow__change { font-size: 0.6875rem; }
.flow__change--added { color: var(--success); }
.flow__change--changed { color: var(--warning); }
.flow__change--removed { color: var(--danger); }
.flow__inspector { width: 310px; max-width: 45%; flex: none; padding: 14px 18px 24px; border-left: 1px solid var(--border-strong); background: var(--surface-panel); }
.flow__inspector-head { display: flex; justify-content: space-between; align-items: center; }
.flow__inspector h2 { font-size: 1rem; font-weight: 600; line-height: 1.45; overflow-wrap: anywhere; margin: 6px 0 12px; }
.flow__facts { display: grid; grid-template-columns: auto minmax(0, 1fr); gap: 9px 12px; font-size: 0.6875rem; margin: 16px 0; }
.flow__facts dt { color: var(--text-dim); }
.flow__facts dd { margin: 0; overflow-wrap: anywhere; }
.flow__expression-label { display: block; font-size: 0.625rem; color: var(--text-dim); margin-top: 3px; }
.flow__payload { margin-top: 20px; }
.flow__payload h3 { color: var(--text-muted); font-size: 0.6875rem; font-weight: 600; margin-bottom: 8px; }
.flow__payload pre { margin: 0; font-size: 0.6875rem; line-height: 1.6; white-space: pre-wrap; overflow-wrap: anywhere; color: var(--text-muted); }
.flow__payload .flow__error { color: var(--danger); }
.flow__empty { position: absolute; top: 40%; width: 100%; text-align: center; color: var(--text-dim); }
@keyframes flow-pulse { 50% { opacity: 0.3; } }
@media (prefers-reduced-motion: reduce) { .flow * { animation: none !important; transition: none !important; } .flow :deep(.vue-flow__edge-path) { animation: none !important; } }
@media (max-width: 700px) {
  .flow__toolbar { padding: 7px 10px; }
  .flow .btn--icon { min-width: 36px; width: 36px; height: 36px; }
  .flow__inspector { position: absolute; bottom: 0; left: 0; right: 0; width: 100%; max-width: none; max-height: 54%; border-left: none; border-top: 1px solid var(--border-strong); box-shadow: var(--shadow-lg); z-index: 6; padding-bottom: max(20px, env(safe-area-inset-bottom)); }
  .flow__inspector-head { position: sticky; top: -14px; background: var(--surface-panel); z-index: 1; }
  .flow__footer { padding: 7px 12px; }
  .flow__warning { font-size: 0.625rem; padding: 5px 12px; }
  .flow__zoom { bottom: 12px; }
}
</style>