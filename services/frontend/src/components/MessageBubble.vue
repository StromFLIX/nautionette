<template>
  <div class="msg" data-skin-part="message" :data-role="role" :class="`msg--${role}`">
    <div class="bubble" :class="{ 'bubble--wide': run || hasTools }">
      <RouterLink v-if="run" class="bubble__run" :to="`/runs/${run.workflow_id}`">
        <span class="material-icons">bolt</span>
        <span class="grow truncate">{{ run.workflow }}</span>
        <span class="chip" :class="`chip--${RUN_TONE[run.status] || ''}`">{{ run.status }}</span>
      </RouterLink>
      <div v-if="meta.attachments?.length" class="bubble__images">
        <ChatImage v-for="image in meta.attachments" :key="image.id" :image="image" :chat-id="chatId" />
      </div>
      <template v-for="(part, index) in groupedParts" :key="part.id || index">
        <ToolCallGroup v-if="part.kind === 'tool-group'" :steps="part.steps" :live="live" :timing="meta.timing">
          <ToolCall v-for="(step, stepIndex) in part.steps" :key="step.id || stepIndex" :step="step" :live="live" />
        </ToolCallGroup>
        <div v-else-if="part.text.trim()" class="bubble__body" @click="copyCode" v-html="renderMarkdown(part.text)" />
      </template>
      <div v-if="status" class="bubble__status caption">
        <span class="material-icons bubble__spinner">autorenew</span>{{ status }}
      </div>
      <div v-if="error" class="bubble__error caption">{{ error }}</div>
      <div v-if="role === 'assistant' && responseText.trim()" class="bubble__actions">
        <button type="button" class="btn btn--icon btn--sm" aria-label="Copy response" @click="copy(responseText, 'Response')">
          <span class="material-icons" aria-hidden="true">content_copy</span><q-tooltip>Copy response</q-tooltip>
        </button>
      </div>
      <div class="bubble__copy-status caption" aria-live="polite" aria-atomic="true">{{ copyStatus }}</div>
    </div>
    <span v-if="(time && preferences.showTimestamps) || deliveryState" class="msg__time caption">
      {{ preferences.showTimestamps ? time : '' }}
      <span v-if="deliveryState === 'sending'" class="msg__delivery" role="status">
        <span class="material-icons" aria-hidden="true">schedule</span>Sending
      </span>
      <span v-else-if="deliveryState === 'failed'" class="msg__delivery msg__delivery--failed" role="status">
        <span :title="deliveryError">Not sent</span>
        <button class="btn btn--icon" title="Retry message" aria-label="Retry message" @click="$emit('retry')">
          <span class="material-icons">refresh</span>
        </button>
        <button class="btn btn--icon" title="Discard message" aria-label="Discard message" @click="$emit('discard')">
          <span class="material-icons">close</span>
        </button>
      </span>
      <span v-else-if="showSent" class="material-icons msg__sent" title="Sent" aria-label="Sent">check</span>
    </span>
  </div>
</template>

<script setup>
import { computed, onUnmounted, ref, watch } from 'vue'
import ToolCall from './ToolCall.vue'
import ToolCallGroup from './ToolCallGroup.vue'
import ChatImage from './ChatImage.vue'
import { groupToolCalls } from '../timeline'
import { renderMarkdown } from '../markdown'
import { copyText } from '../clipboard'
import { RUN_TONE, shortTime } from '../format'
import { preferences } from '../preferences'

const props = defineProps({
  role: { type: String, default: 'assistant' },
  chatId: { type: String, default: '' },
  content: { type: String, default: '' },
  meta: { type: Object, default: () => ({}) },
  createdAt: { type: Number, default: 0 },
  live: { type: Boolean, default: false },
  status: { type: String, default: '' },
  deliveryState: { type: String, default: '' },
  deliveryError: { type: String, default: '' }
})
defineEmits(['retry', 'discard'])

const showSent = ref(false)
let sentTimer
watch(() => [props.deliveryState, props.createdAt], () => {
  clearTimeout(sentTimer)
  showSent.value = props.role === 'user' && !props.deliveryState && Date.now() - props.createdAt * 1000 < 5000
  if (showSent.value) sentTimer = setTimeout(() => { showSent.value = false }, 3000)
}, { immediate: true })
onUnmounted(() => clearTimeout(sentTimer))

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
const groupedParts = computed(() => groupToolCalls(parts.value))
// Copy the whole answer, including narration between tool groups, as Markdown.
// Tool arguments/results and UI status labels are not part of the answer.
const responseText = computed(() => [
  ...parts.value.filter((part) => part.kind === 'text').map((part) => part.text),
  error.value
].filter((text) => text.trim()).join('\n\n'))
const copyStatus = ref('')
let copyTimer
onUnmounted(() => clearTimeout(copyTimer))

async function copy (text, label) {
  clearTimeout(copyTimer)
  copyStatus.value = ''
  try {
    await copyText(text)
    copyStatus.value = `${label} copied`
  } catch {
    copyStatus.value = 'Could not copy. Select the text and copy it manually.'
  }
  copyTimer = setTimeout(() => { copyStatus.value = '' }, 3000)
}

function copyCode (event) {
  const button = event.target.closest('button.code-block__copy')
  if (!button || !event.currentTarget.contains(button)) return
  const code = button.closest('.code-block')?.querySelector('pre code')
  if (code) copy(code.textContent, 'Code')
}

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
.bubble__images { display: flex; flex-wrap: wrap; gap: 8px; margin-bottom: 6px; }
.msg {
  display: flex;
  align-items: flex-end;
  gap: 8px;
  margin-bottom: var(--space-5);
}

.msg--user {
  flex-direction: row-reverse;
}

.bubble {
  max-width: 86%;
  padding: var(--space-3) var(--space-4);
  font-size: var(--chat-font-size);
  border-radius: var(--radius-lg);
  overflow-wrap: anywhere;
}

.msg--user .bubble {
  background: var(--bubble-out);
  border-bottom-right-radius: var(--radius-xs);
  color: var(--bubble-text);
}

.msg--assistant .bubble {
  width: 100%;
  max-width: calc(100% - 60px);
  padding: 9px 0;
  background: transparent;
  border: 0;
  border-radius: 0;
}

.bubble--wide {
  max-width: 92%;
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
  font-size: 0.75rem;
  text-decoration: none;
}

.bubble__run:hover {
  color: var(--text);
}

.bubble__run .material-icons {
  font-size: 0.875rem;
  color: var(--accent-hover);
}

.msg__time {
  flex: none;
  padding-bottom: 3px;
  color: var(--text-dim);
  font-size: 0.6875rem;
}

.msg__delivery {
  display: inline-flex;
  align-items: center;
  gap: 3px;
  margin-left: 4px;
}

.msg__delivery .material-icons,
.msg__sent {
  font-size: 0.8125rem;
  vertical-align: middle;
}

.msg__sent {
  color: var(--accent);
}

.msg__delivery--failed {
  color: var(--danger);
}

.msg__delivery .btn {
  width: 22px;
  height: 22px;
  min-width: 22px;
  padding: 0;
}

.bubble__actions {
  display: flex;
  margin-top: 8px;
}

.bubble__actions .material-icons {
  font-size: 0.9375rem;
}

.bubble__copy-status:not(:empty) {
  margin-top: 4px;
  color: var(--text-muted);
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
  font-size: 0.875rem;
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
  font-size: calc(var(--chat-font-size) * 1.12);
  font-weight: 600;
}

.bubble__body code {
  padding: 1px 5px;
  border-radius: var(--radius-xs);
  background: var(--surface-active);
}

.bubble__body pre {
  margin: 8px 0;
  padding: 11px 13px;
  border-radius: var(--radius-md);
  background: var(--surface-code);
  color: var(--text);
  overflow-x: auto;
}

.bubble__body pre code {
  padding: 0;
  background: none;
}

.bubble__body {
  -webkit-user-select: text;
  user-select: text;
}

.bubble__body .code-block {
  margin: 8px 0;
  border: 1px solid var(--border);
  border-radius: var(--radius-md);
  background: var(--surface-code);
  color: var(--text);
  overflow: hidden;
}

.bubble__body .code-block__toolbar {
  display: flex;
  justify-content: flex-end;
  padding: 4px 8px;
  border-bottom: 1px solid var(--border);
}

.bubble__body .code-block pre {
  margin: 0;
  border-radius: 0;
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
  font-size: 0.8125rem;
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

  .msg--assistant .bubble { max-width: 100%; }
}

@media (hover: hover) {
  .bubble__actions { opacity: 0; transition: opacity var(--transition); }
  .msg:hover .bubble__actions, .msg:focus-within .bubble__actions { opacity: 1; }
}
</style>
