<template>
  <div class="msg" :class="`msg--${role}`">
    <div class="bubble" :class="{ 'bubble--wide': run || hasTools }">
      <RouterLink v-if="run" class="bubble__run" :to="`/runs/${run.workflow_id}`">
        <span class="material-icons">bolt</span>
        <span class="grow truncate">{{ run.workflow }}</span>
        <span class="chip" :class="`chip--${RUN_TONE[run.status] || ''}`">{{ run.status }}</span>
      </RouterLink>
      <template v-for="(part, index) in parts" :key="part.id || index">
        <ToolCall v-if="part.kind === 'tool'" :step="part" :live="live" />
        <div v-else-if="part.text.trim()" class="bubble__body" v-html="renderMarkdown(part.text)" />
      </template>
      <div v-if="status" class="bubble__status caption">
        <span class="material-icons bubble__spinner">autorenew</span>{{ status }}
      </div>
      <div v-if="error" class="bubble__error caption">{{ error }}</div>
    </div>
    <span v-if="time" class="msg__time caption">{{ time }}</span>
  </div>
</template>

<script setup>
import { computed } from 'vue'
import ToolCall from './ToolCall.vue'
import { renderMarkdown } from '../markdown'
import { RUN_TONE, shortTime } from '../format'

const props = defineProps({
  role: { type: String, default: 'assistant' },
  content: { type: String, default: '' },
  meta: { type: Object, default: () => ({}) },
  createdAt: { type: Number, default: 0 },
  live: { type: Boolean, default: false },
  status: { type: String, default: '' }
})

// Messages written before answers kept a timeline only remember the tool names.
const steps = computed(() => {
  const stored = props.meta?.steps
  if (Array.isArray(stored) && stored.length) return stored
  return (props.meta?.tools || []).map((name) => ({ kind: 'tool', name, args: null, ok: null, result: '' }))
})

const parts = computed(() => {
  if (!steps.value.length) return [{ kind: 'text', text: props.content }]
  const spoken = steps.value.some((step) => step.kind === 'text' && step.text.trim())
  return spoken ? steps.value : [...steps.value, { kind: 'text', text: props.content }]
})
const hasTools = computed(() => steps.value.some((step) => step.kind === 'tool'))
const run = computed(() => props.meta?.run || null)
// The backend folds a failure into the body too, so only add it when it is new.
const error = computed(() => {
  const detail = props.meta?.error || ''
  return detail && !props.content.includes(detail) ? detail : ''
})
const time = computed(() => shortTime(props.createdAt))
</script>

<style scoped>
.msg {
  display: flex;
  align-items: flex-end;
  gap: 8px;
  margin-bottom: 14px;
}

.msg--user {
  flex-direction: row-reverse;
}

.bubble {
  max-width: min(680px, 78%);
  padding: 9px 13px;
  border-radius: var(--radius-lg);
  overflow-wrap: anywhere;
}

.msg--user .bubble {
  background: var(--bubble-out);
  border-bottom-right-radius: var(--radius-xs);
  color: #fff;
}

.msg--assistant .bubble {
  background: var(--bubble-in);
  border: 1px solid var(--border);
  border-bottom-left-radius: var(--radius-xs);
}

.bubble--wide {
  max-width: min(760px, 92%);
}

.bubble__run {
  display: flex;
  align-items: center;
  gap: 7px;
  margin: -3px -5px 8px;
  padding: 5px 7px;
  border-radius: var(--radius-sm);
  background: var(--surface-active);
  color: var(--text-muted);
  font-size: 12px;
  text-decoration: none;
}

.bubble__run:hover {
  color: var(--text);
}

.bubble__run .material-icons {
  font-size: 14px;
  color: var(--accent-hover);
}

.msg__time {
  flex: none;
  padding-bottom: 3px;
  color: var(--text-dim);
  font-size: 11px;
}

.bubble__error {
  margin-top: 6px;
  color: var(--danger);
}

.bubble__status {
  display: flex;
  align-items: center;
  gap: 6px;
  color: var(--text-muted);
}

.bubble__spinner {
  font-size: 14px;
  animation: bubble-spin 1.4s linear infinite;
}

@keyframes bubble-spin {
  to {
    transform: rotate(360deg);
  }
}
</style>

<style>
.bubble__body > :first-child {
  margin-top: 0;
}

.bubble__body > :last-child {
  margin-bottom: 0;
}

.bubble__body p {
  margin: 0 0 8px;
  line-height: 1.55;
}

.bubble__body ul,
.bubble__body ol {
  margin: 0 0 8px;
  padding-left: 20px;
}

.bubble__body li {
  margin-bottom: 3px;
}

.bubble__body h1,
.bubble__body h2,
.bubble__body h3,
.bubble__body h4 {
  margin: 12px 0 6px;
  font-size: 14px;
  font-weight: 650;
}

.bubble__body code {
  padding: 1px 5px;
  border-radius: var(--radius-xs);
  background: rgba(0, 0, 0, 0.28);
}

.bubble__body pre {
  margin: 8px 0;
  padding: 11px 13px;
  border-radius: var(--radius-md);
  background: rgba(0, 0, 0, 0.32);
  overflow-x: auto;
}

.bubble__body pre code {
  padding: 0;
  background: none;
}

.bubble__body a {
  color: inherit;
  text-decoration: underline;
  text-underline-offset: 2px;
}

.msg--assistant .bubble__body a {
  color: var(--accent-hover);
}

.bubble__body blockquote {
  margin: 8px 0;
  padding-left: 10px;
  border-left: 2px solid var(--border-strong);
  color: var(--text-muted);
}

.bubble__body table {
  width: 100%;
  margin: 8px 0;
  border-collapse: collapse;
  font-size: 13px;
}

.bubble__body th,
.bubble__body td {
  padding: 5px 8px;
  border: 1px solid var(--border);
  text-align: left;
}

@media (max-width: 480px) {
  .msg {
    position: relative;
    display: block;
    margin-bottom: 28px;
  }

  .msg--user {
    text-align: right;
  }

  .bubble {
    display: inline-block;
    max-width: 86%;
    text-align: left;
  }

  .msg__time {
    position: absolute;
    top: calc(100% + 3px);
    right: 0;
    padding: 0;
  }

  .msg--assistant .msg__time {
    right: auto;
    left: 0;
  }
}
</style>
