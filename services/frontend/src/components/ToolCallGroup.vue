<template>
  <details class="tool-group">
    <summary class="tool-group__summary">
      <span class="tool-group__label" aria-live="polite" aria-atomic="true">{{ summary.label }}</span>
      <span v-if="live && summary.pending" class="tool-group__pulse" role="img" aria-label="Tool calls in progress" />
      <span v-if="summary.failed" class="tool-group__failed">{{ summary.failed }} failed</span>
      <span class="material-icons tool-group__chevron" aria-hidden="true">chevron_right</span>
    </summary>
    <div class="tool-group__timeline">
      <slot />
    </div>
  </details>
</template>

<script setup>
import { computed } from 'vue'
import { summarizeToolCalls } from '../timeline'

const props = defineProps({
  steps: { type: Array, required: true },
  live: { type: Boolean, default: false }
})

const summary = computed(() => summarizeToolCalls(props.steps))
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

.tool-group__pulse {
  flex: none;
  width: 7px;
  height: 7px;
  border-radius: 50%;
  background: var(--accent);
  animation: tool-group-pulse 1s ease-in-out infinite;
}

@keyframes tool-group-pulse {
  50% {
    opacity: 0.25;
  }
}

@media (prefers-reduced-motion: reduce) {
  .tool-group__pulse { animation: none; }
  .tool-group__chevron { transition: none; }
}
</style>
