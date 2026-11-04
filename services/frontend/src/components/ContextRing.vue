<template>
  <div class="context-ring" :class="{ 'context-ring--hot': meter.percent > 80, 'context-ring--unknown': meter.percent === null }"
    role="img" tabindex="0" :aria-label="`${meter.label}. ${meter.title}`" :title="meter.title">
    <svg viewBox="0 0 36 36" aria-hidden="true"><circle class="context-ring__track" cx="18" cy="18" r="14" /><circle class="context-ring__fill" cx="18" cy="18" r="14" pathLength="100" :stroke-dasharray="`${meter.width} 100`" /></svg>
    <span aria-hidden="true">{{ meter.percent === null ? '—' : `${meter.percent}%` }}</span>
    <q-tooltip>{{ meter.label }} · {{ meter.title }}</q-tooltip>
  </div>
</template>

<script setup>
import { computed } from 'vue'
import { contextMeter, modelContextWindow } from '../context'
import { store } from '../store'
const props = defineProps({ context: { type: Object, default: null }, model: { type: String, default: '' } })
const meter = computed(() => contextMeter(props.context, modelContextWindow(store.catalog, props.model)))
</script>

<style scoped>
.context-ring { position: relative; display: grid; place-items: center; flex: none; width: 38px; height: 38px; color: var(--text-muted); }
.context-ring svg { position: absolute; inset: 0; width: 100%; height: 100%; transform: rotate(-90deg); }
.context-ring circle { fill: none; stroke-width: 2.5; }
.context-ring__track { stroke: var(--border-strong); }
.context-ring__fill { stroke: var(--accent); stroke-linecap: round; transition: stroke-dasharray 200ms; }
.context-ring--hot .context-ring__fill { stroke: var(--warning); }
.context-ring--unknown .context-ring__track { stroke-dasharray: 2 3; }
.context-ring span { font-size: .5rem; font-variant-numeric: tabular-nums; }
@media (prefers-reduced-motion: reduce) { .context-ring__fill { transition: none; } }
</style>
