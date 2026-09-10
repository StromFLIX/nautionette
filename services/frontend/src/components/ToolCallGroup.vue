<template>
  <details class="tool-group" @toggle="open = $event.target.open">
    <summary class="tool-group__summary">
      <svg class="tool-group__indicator" viewBox="0 0 24 24" focusable="false"
        :role="running ? 'img' : undefined" :aria-label="running ? 'Tool calls in progress' : undefined" :aria-hidden="running ? undefined : true">
        <path class="tool-group__indicator-track" d="M8 2H16L22 8V16L16 22H8L2 16V8Z" />
        <path v-if="running" class="tool-group__indicator-arc" d="M8 2H16L22 8V16L16 22H8L2 16V8Z" pathLength="100" />
      </svg>
      <span class="tool-group__label" aria-live="polite" aria-atomic="true">{{ summary.label }}</span>
      <span v-if="summary.failed" class="tool-group__failed">{{ summary.failed }} failed</span>
      <span class="material-icons tool-group__chevron" aria-hidden="true">chevron_right</span>
    </summary>
    <ActivityTiming v-if="open && timing" :timing="timing" :live="live" />
    <div class="tool-group__timeline">
      <slot />
    </div>
  </details>
</template>

<script setup>
import { computed, ref } from 'vue'
import { summarizeToolCalls } from '../timeline'
import ActivityTiming from './ActivityTiming.vue'

const props = defineProps({
  steps: { type: Array, required: true },
  timing: { type: Object, default: null },
  live: { type: Boolean, default: false }
})

const open = ref(false)
const summary = computed(() => summarizeToolCalls(props.steps))
const running = computed(() => props.live && summary.value.pending > 0)
</script>

<style scoped>
.tool-group {
  margin: 6px 0;
}

.tool-group__summary {
  display: flex;
  align-items: center;
  gap: 6px;
  width: fit-content;
  max-width: 100%;
  padding: 6px 0;
  border-radius: var(--radius-xs);
  color: var(--text-muted);
  font-size: 0.8125rem;
  line-height: 1.5;
  list-style: none;
  cursor: pointer;
}

.tool-group__summary::-webkit-details-marker {
  display: none;
}

.tool-group__summary:hover {
  color: var(--text);
}

.tool-group__label {
  min-width: 0;
}

.tool-group__chevron {
  flex: none;
  font-size: 1rem;
  transition: transform 0.15s ease;
}

.tool-group[open] > .tool-group__summary .tool-group__chevron {
  transform: rotate(90deg);
}

.tool-group__timeline {
  margin: 4px 0 8px;
}

.tool-group__failed {
  flex: none;
  color: var(--danger);
}

.tool-group__indicator {
  flex: none;
  width: 0.75rem;
  height: 0.75rem;
  fill: none;
  stroke: currentColor;
  stroke-width: 2;
  stroke-linejoin: round;
  pointer-events: none;
}

.tool-group__indicator-track {
  opacity: 0.35;
}

.tool-group__indicator-arc {
  stroke: var(--accent-hover);
  stroke-linecap: round;
  stroke-dasharray: 24 76;
  animation: tool-group-orbit 2s linear infinite;
}

@keyframes tool-group-orbit {
  to { stroke-dashoffset: -100; }
}

@media (prefers-reduced-motion: reduce) {
  .tool-group__indicator-arc { animation: none; }
  .tool-group__chevron { transition: none; }
}
</style>
