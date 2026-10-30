<template>
  <span class="agent-activity">
    <span :id="descriptionId" class="sr-only">{{ activityLabel(counts) }}</span>
    <span v-if="counts?.running" class="agent-activity__badge agent-activity__badge--running" aria-hidden="true">
      {{ compact(counts.running) }}
    </span>
    <span v-if="counts?.queued" class="agent-activity__badge agent-activity__badge--queued" aria-hidden="true">
      {{ compact(counts.queued) }}
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
.agent-activity__badge { position: absolute; right: 2px; display: grid; place-items: center; min-width: 16px; height: 16px; padding: 0 4px; border-radius: var(--radius-xs); font-size: 0.625rem; line-height: 1; font-weight: 600; font-variant-numeric: tabular-nums; box-shadow: 0 0 0 2px var(--surface-panel); }
.agent-activity__badge--running { bottom: 2px; background: var(--accent); color: var(--accent-text); }
.agent-activity__badge--queued { top: 2px; background: var(--surface-raised); color: var(--text-muted); }
</style>
