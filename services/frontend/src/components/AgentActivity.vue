<template>
  <span class="agent-activity">
    <span :id="descriptionId" class="sr-only">{{ activityLabel(counts) }}</span>
    <span v-if="counts?.running" class="agent-activity__badge agent-activity__badge--running" aria-hidden="true">
      <span class="material-icons">play_arrow</span>{{ compact(counts.running) }}
    </span>
    <span v-if="counts?.queued" class="agent-activity__badge agent-activity__badge--queued" aria-hidden="true">
      <span class="material-icons">hourglass_top</span>{{ compact(counts.queued) }}
    </span>
  </span>
</template>

<script setup>
import { activityLabel } from '../agent-activity'

defineProps({ counts: { type: Object, default: null }, descriptionId: { type: String, required: true } })
const compact = count => count > 99 ? '99+' : count
</script>

<style scoped>
.agent-activity { position: absolute; inset: 0; pointer-events: none; }
.agent-activity__badge { position: absolute; right: -2px; display: flex; align-items: center; justify-content: center; gap: 1px; min-width: 20px; height: 18px; padding: 0 3px 0 1px; border: 1px solid currentColor; border-radius: var(--radius-pill); background: var(--surface-panel); font-size: 0.625rem; line-height: 1; font-weight: 700; font-variant-numeric: tabular-nums; box-shadow: 0 0 0 1px var(--surface-panel); }
.agent-activity__badge .material-icons { font-size: 0.6875rem; }
.agent-activity__badge--running { top: -1px; color: var(--success); }
.agent-activity__badge--queued { bottom: -1px; color: var(--warning); }
</style>
