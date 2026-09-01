<template>
  <div class="msg" :class="`msg--${role}`">
    <div class="bubble" :class="{ 'bubble--run': run }">
      <RouterLink v-if="run" class="bubble__run" :to="`/runs/${run.workflow_id}`">
        <span class="material-icons">bolt</span>
        <span class="grow truncate">{{ run.workflow }}</span>
        <span class="chip" :class="`chip--${RUN_TONE[run.status] || ''}`">{{ run.status }}</span>
      </RouterLink>
      <div class="bubble__body" v-html="html" />
      <div v-if="tools.length" class="bubble__tools">
        <span v-for="tool in tools" :key="tool" class="chip">
          <span class="material-icons" style="font-size: 13px">handyman</span>{{ tool }}
        </span>
      </div>
      <div v-if="error" class="bubble__error caption">{{ error }}</div>
    </div>
    <span v-if="time" class="msg__time caption">{{ time }}</span>
  </div>
</template>

<script setup>
import { computed } from 'vue'
import { renderMarkdown } from '../markdown'
import { RUN_TONE, shortTime } from '../format'

const props = defineProps({
  role: { type: String, default: 'assistant' },
  content: { type: String, default: '' },
  meta: { type: Object, default: () => ({}) },
  createdAt: { type: Number, default: 0 }
})

const html = computed(() => renderMarkdown(props.content))
const tools = computed(() => props.meta?.tools || [])
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

.bubble--run {
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

.bubble__tools {
  display: flex;
  flex-wrap: wrap;
  gap: 5px;
  margin-top: 8px;
}

.bubble__error {
  margin-top: 6px;
  color: var(--danger);
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
</style>
