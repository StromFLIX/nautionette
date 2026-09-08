<template>
  <div class="welcome scroll-y">
    <div class="welcome__inner">
      <div class="welcome__intro">
        <BrandMark class="welcome__mark" />
        <span class="welcome__eyebrow">Nautionette</span>
        <h1 class="welcome__title">What should happen next?</h1>
        <p class="welcome__lead">An idea. A conversation. A workflow.</p>
      </div>
      <Composer ref="composer" v-model="text" v-model:attachments="attachments" v-model:agent-set="agentSet"
        v-model:model="model" v-model:reasoning-effort="reasoningEffort" v-model:tools="tools" v-model:project-ids="projectIds"
        variant="welcome" :busy="busy" placeholder="Ask, build, or automate…"
        @send="$emit('start', { text, agentSet, model, reasoningEffort, tools, projectIds, attachments })" />

      <div v-if="preferences.starterPrompts" class="welcome__starters" aria-label="Starter prompts">
        <button v-for="prompt in prompts" :key="prompt.label" class="starter" @click="use(prompt.text)">
          <span class="material-icons starter__icon" aria-hidden="true">{{ prompt.icon }}</span>
          <span class="grow">{{ prompt.label }}</span>
          <span class="material-icons starter__arrow" aria-hidden="true">north_east</span>
          <q-tooltip>{{ prompt.text }}</q-tooltip>
        </button>
      </div>

      <details class="welcome__details">
        <summary>Session details</summary>
        <div class="welcome__facts">
          <div><span>Agent set</span><strong>{{ agentSet || 'default' }}</strong></div>
          <div><span>Model</span><strong>{{ model || store.catalog.default_model || 'Not selected' }}</strong></div>
          <div><span>Tools <button @click="actions.openSettings('mcp')">Manage</button></span><strong>{{ toolSummary }}</strong></div>
          <div><span>Context window</span><strong>{{ contextWindow ? `${contextWindow.toLocaleString()} tokens` : 'Unknown' }}</strong></div>
        </div>
      </details>

      <div v-if="store.ready && store.system.model_key_present === false" class="welcome__warn" role="status">
        <span class="material-icons" aria-hidden="true">info_outline</span>
        <span class="grow">Connect a model to start chatting.</span>
        <button class="btn btn--sm btn--outline" @click="actions.openSettings('agents')">Connect model<span class="material-icons" aria-hidden="true">arrow_outward</span></button>
      </div>
    </div>
  </div>
</template>

<script setup>
import { computed, nextTick, ref, watch } from 'vue'
import Composer from './Composer.vue'
import BrandMark from './BrandMark.vue'
import { modelContextWindow } from '../context'
import { actions, store } from '../store'
import { preferences } from '../preferences'

defineProps({ busy: { type: Boolean, default: false } })
defineEmits(['start'])
const prompts = [
  { label: 'Daily briefing', icon: 'wb_twilight', text: 'Summarise the top Hacker News stories every morning at 8' },
  { label: 'Watch for changes', icon: 'track_changes', text: 'Check this URL for changes and tell me when it moves' },
  { label: 'Weekly digest', icon: 'view_week', text: 'Draft a weekly digest from these release notes' },
  { label: 'Follow a feed', icon: 'rss_feed', text: 'Watch an RSS feed and save anything about Temporal' }
]
const text = ref('')
const attachments = ref([])
const agentSet = ref(store.catalog.default_agent_set || 'default')
const model = ref(store.catalog.default_model || '')
const reasoningEffort = ref(null)
watch(model, () => { reasoningEffort.value = null })
const tools = ref(null)
const projectIds = ref([])
const composer = ref(null)
const contextWindow = computed(() => modelContextWindow(store.catalog, model.value))
function use (prompt) { text.value = prompt; nextTick(() => composer.value?.focus()) }
const toolSummary = computed(() => {
  const total = (store.catalog.tools || []).length
  return tools.value === null ? `${total} available` : `${tools.value.length} of ${total} enabled`
})
watch(() => store.catalog, catalog => {
  if (!model.value) model.value = catalog.default_model || ''
  if (!agentSet.value) agentSet.value = catalog.default_agent_set || 'default'
}, { deep: true })
</script>

<style scoped>
.welcome { flex: 1; display: flex; justify-content: center; padding: clamp(48px, 11vh, 120px) 36px 40px; background: radial-gradient(ellipse at 50% 24%, color-mix(in srgb, var(--accent) 3%, transparent), transparent 65%); }
.welcome__inner { width: 100%; max-width: 740px; }
.welcome__intro { text-align: center; margin-bottom: 32px; }
.welcome__mark { width: 72px; height: 72px; margin: 0 auto 22px; }
.welcome__eyebrow { display: block; margin-bottom: 12px; text-transform: uppercase; letter-spacing: 0.22em; font-size: 10px; font-weight: 600; color: var(--accent); }
.welcome__title { margin: 0; font-size: clamp(25px, 2.5vw, 36px); font-weight: 500; line-height: 1.2; letter-spacing: -0.045em; }
.welcome__lead { margin: 14px 0 0; color: var(--text-muted); font-size: 13px; }
.welcome__starters { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 10px; margin-top: 18px; }
.starter { display: flex; align-items: center; gap: 8px; padding: 13px 12px; border: 1px solid var(--border); border-radius: var(--radius-md); background: var(--surface-panel); color: var(--text-muted); font: inherit; font-size: 11.5px; text-align: left; cursor: pointer; transition: border-color var(--transition), color var(--transition), background var(--transition); }
.starter:hover { border-color: var(--border-strong); background: var(--surface-raised); color: var(--text); }
.starter__icon { flex: none; font-size: 18px; color: var(--text-dim); }
.starter__arrow { flex: none; font-size: 12px; opacity: 0.5; }
.welcome__details { margin-top: 26px; color: var(--text-dim); font-size: 11px; }
.welcome__details > summary { width: fit-content; margin: 0 auto; cursor: pointer; }
.welcome__facts { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 18px 32px; padding: 24px 0; }
.welcome__facts > div { min-width: 0; }
.welcome__facts span { display: flex; align-items: center; gap: 12px; margin-bottom: 5px; }
.welcome__facts strong { display: block; font-size: 12px; font-weight: 500; color: var(--text-muted); overflow-wrap: anywhere; }
.welcome__facts button { border: 0; padding: 0; background: none; color: var(--accent); font: inherit; cursor: pointer; }
.welcome__warn { display: flex; align-items: center; flex-wrap: wrap; gap: 10px; margin-top: 24px; padding: 12px 14px; border: 1px solid var(--warning-soft); border-radius: var(--radius-md); background: var(--warning-soft); color: var(--warning); font-size: 12px; }
.welcome__warn .material-icons { font-size: 17px; }
@media (max-width: 1200px) { .welcome__starters { grid-template-columns: repeat(2, minmax(0, 1fr)); } }
@media (max-width: 900px) { .welcome { padding: 32px max(16px, env(safe-area-inset-right)) max(24px, env(safe-area-inset-bottom)) max(16px, env(safe-area-inset-left)); } }
</style>
