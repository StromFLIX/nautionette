<template>
  <div class="execution-flow">
    <div v-if="error" class="execution-flow__error" role="status">
      <span class="material-icons">cloud_off</span>
      <span class="grow">{{ graph ? 'Live updates paused. ' : '' }}{{ error }}</span>
      <button class="btn btn--sm btn--outline" @click="refresh">Retry</button>
    </div>
    <WorkflowGraph v-if="graph" :key="graph.nodes[0]?.run_id || workflowId" :graph="graph" :live-paused="Boolean(error)"><template #source><slot name="source"><span class="caption muted">Execution history</span></slot></template></WorkflowGraph>
    <div v-else-if="!error" class="empty"><q-spinner size="24px" /><span>Loading execution...</span></div>
  </div>
</template>

<script setup>
import { onMounted, onUnmounted, ref, watch } from 'vue'
import { api } from '../api'
import { onLiveEvent } from '../store'
import WorkflowGraph from './WorkflowGraph.vue'

const props = defineProps({ workflowId: { type: String, required: true } })
const emit = defineEmits(['status'])
const graph = ref(null)
const error = ref('')
let timer
let controller
let generation = 0
let disposed = false
let off = () => {}

async function refresh () {
  clearTimeout(timer)
  if (disposed || document.hidden || controller || !props.workflowId) return
  const version = generation
  controller = new AbortController()
  try {
    const result = await api.runGraph(props.workflowId, controller.signal)
    if (version !== generation || disposed) return
    graph.value = result
    error.value = ''
    emit('status', result.status)
  } catch (failure) {
    if (version === generation && failure.name !== 'AbortError') error.value = failure.message
  } finally {
    if (version === generation && !disposed) {
      controller = null
      if (error.value || graph.value?.status === 'running') timer = setTimeout(refresh, error.value ? 5000 : 3000)
    }
  }
}

function visibility () {
  clearTimeout(timer)
  if (!document.hidden) refresh()
}

watch(() => props.workflowId, () => {
  generation++
  controller?.abort()
  controller = null
  graph.value = null
  error.value = ''
  refresh()
}, { immediate: true })
onMounted(() => {
  document.addEventListener('visibilitychange', visibility)
  off = onLiveEvent((event) => { if (event.workflow_id === props.workflowId) refresh() })
})
onUnmounted(() => {
  disposed = true
  clearTimeout(timer)
  controller?.abort()
  off()
  document.removeEventListener('visibilitychange', visibility)
})
</script>

<style scoped>
.execution-flow { display: flex; flex: 1; flex-direction: column; min-width: 0; min-height: 0; }
.execution-flow__error { display: flex; align-items: center; gap: 10px; padding: 10px 16px; font-size: 0.75rem; background: var(--warning-soft); color: var(--warning); overflow-wrap: anywhere; }
.execution-flow__error > .material-icons { font-size: 1.125rem; }
</style>