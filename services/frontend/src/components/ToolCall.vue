<template>
  <div class="tool" :class="{ 'tool--open': open, 'tool--bad': step.ok === false }">
    <button class="tool__row" :aria-expanded="open" @click="open = !open">
      <span class="material-icons tool__chevron">{{ open ? 'expand_more' : 'chevron_right' }}</span>
      <span class="material-icons tool__icon">{{ shown.icon }}</span>
      <span class="tool__title truncate">{{ shown.title }}</span>
      <span v-if="shown.detail" class="tool__detail truncate">{{ shown.detail }}</span>
      <span v-if="server" class="tool__server">{{ server }}</span>
      <span v-if="running" class="tool__pulse" />
      <span v-else-if="step.ok === false" class="material-icons tool__failed">error_outline</span>
    </button>

    <div v-if="open" class="tool__panel">
      <div class="tool__meta caption dim">
        <span class="tool__name">{{ step.name }}</span>
        <template v-if="catalogued?.description"> · {{ catalogued.description }}</template>
      </div>
      <div v-if="args" class="tool__block">
        <div class="section-label">Arguments</div>
        <pre class="tool__pre">{{ args }}</pre>
      </div>
      <div class="tool__block">
        <div class="section-label">{{ step.ok === false ? 'Error' : 'Result' }}</div>
        <pre class="tool__pre">{{ result || (running ? 'Still running…' : 'No output.') }}</pre>
      </div>
    </div>
  </div>
</template>

<script setup>
import { computed, ref } from 'vue'
import { describeTool, prettyJson } from '../timeline'
import { store } from '../store'

const props = defineProps({
  step: { type: Object, required: true },
  // Only a call in the answer being streamed right now can still be running.
  live: { type: Boolean, default: false }
})

const open = ref(false)

const catalogued = computed(() =>
  (store.catalog.tools || []).find((tool) => tool.name === props.step.name) || null)
const server = computed(() => {
  const name = catalogued.value?.server
  return name && name !== 'other' ? name : ''
})
const shown = computed(() => describeTool(props.step, server.value))
const running = computed(() => props.live && props.step.ok === null)
const args = computed(() => prettyJson(props.step.args))
const result = computed(() => prettyJson(props.step.result))
</script>

<style scoped>
.tool {
  margin: 6px 0;
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
  background: rgba(0, 0, 0, 0.18);
  overflow: hidden;
}

.tool--bad {
  border-color: var(--danger-soft);
}

.tool__row {
  display: flex;
  align-items: center;
  gap: 6px;
  width: 100%;
  padding: 5px 9px 5px 4px;
  border: 0;
  background: transparent;
  color: var(--text-muted);
  font: inherit;
  font-size: 12.5px;
  text-align: left;
  cursor: pointer;
}

.tool__row:hover {
  background: var(--surface-hover);
}

.tool__chevron {
  flex: none;
  font-size: 17px;
  color: var(--text-dim);
}

.tool__icon {
  flex: none;
  font-size: 15px;
  color: var(--accent-hover);
}

.tool--bad .tool__icon {
  color: var(--danger);
}

.tool__title {
  flex: 0 1 auto;
  min-width: 0;
  color: var(--text);
  font-weight: 550;
}

.tool__detail {
  flex: 1 1 auto;
  min-width: 0;
  color: var(--text-dim);
  font-family: var(--font-mono);
  font-size: 11.5px;
}

.tool__server {
  flex: none;
  padding: 1px 6px;
  border-radius: var(--radius-pill);
  background: var(--surface-active);
  color: var(--text-dim);
  font-size: 10.5px;
}

.tool__failed {
  flex: none;
  font-size: 15px;
  color: var(--danger);
}

.tool__pulse {
  flex: none;
  width: 7px;
  height: 7px;
  border-radius: 50%;
  background: var(--accent);
  animation: tool-pulse 1s ease-in-out infinite;
}

@keyframes tool-pulse {
  50% {
    opacity: 0.25;
  }
}

.tool__panel {
  padding: 2px 10px 10px 30px;
  border-top: 1px solid var(--border);
}

.tool__meta {
  margin: 8px 0;
}

.tool__name {
  font-family: var(--font-mono);
  color: var(--text-muted);
}

.tool__block + .tool__block {
  margin-top: 10px;
}

.tool__pre {
  margin: 4px 0 0;
  max-height: 300px;
  padding: 8px 10px;
  border-radius: var(--radius-xs);
  background: rgba(0, 0, 0, 0.32);
  color: var(--text-muted);
  font-family: var(--font-mono);
  font-size: 11.5px;
  line-height: 1.5;
  white-space: pre-wrap;
  overflow: auto;
  overflow-wrap: anywhere;
}

@media (prefers-reduced-motion: reduce) {
  .tool__pulse {
    animation: none;
  }
}
</style>
