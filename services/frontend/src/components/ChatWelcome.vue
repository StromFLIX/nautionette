<template>
  <div class="welcome scroll-y">
    <div class="welcome__inner">
      <div class="welcome__intro">
        <BrandMark class="welcome__mark" />
        <span class="welcome__eyebrow">Nautionette</span>
        <h1 class="welcome__title">What should happen next?</h1>
        <p class="welcome__lead">An idea. A conversation. A workflow.</p>
      </div>
      <Composer ref="composer" v-model="text" v-model:attachments="attachments" :agent-id="config.agent_id" :agent-name="config.agent_name || ''"
        :agent-set="config.agent_set" :model="config.model" :reasoning-effort="config.reasoning_effort" :tools="config.tools" :project-ids="config.project_ids" :packages="config.packages || []"
        variant="welcome" :busy="busy" :configuration-ready="store.catalogLoaded" placeholder="Ask, build, or automate…"
        @update:agent-id="chooseAgent" @update:agent-set="override('agent_set', $event)" @update:model="override('model', $event)"
        @update:reasoning-effort="override('reasoning_effort', $event)" @update:tools="override('tools', $event)" @update:project-ids="override('project_ids', $event)"
        @update:packages="override('packages', $event)"
        @send="$emit('start', { text, configuration: config, attachments })" />

      <p v-if="!store.catalogLoaded && store.catalogError" class="caption" role="alert">{{ store.catalogError }} <button class="btn btn--sm" @click="actions.loadCatalog(true)">Retry defaults</button></p>

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
          <div><span>Agent</span><strong>{{ config.agent_name || 'Global defaults' }} · {{ config.agent_set }}</strong></div>
          <div><span>Model</span><strong>{{ config.model || 'Not selected' }}</strong></div>
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
import { agentConfig, copyConfig, defaultChatConfig, resolveAgentConfig } from '../agent-config'

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
// Follow asynchronously loaded defaults until a field (or an agent) is explicitly chosen.
const selectedAgent = ref(undefined)
const overrides = ref({})
const source = computed(() => selectedAgent.value === undefined ? defaultChatConfig(store.catalog) : agentConfig(store.catalog, selectedAgent.value))
const lastSource = ref(defaultChatConfig(store.catalog))
watch(source, value => { if (value) lastSource.value = copyConfig(value) }, { immediate: true, deep: true })
const config = computed(() => resolveAgentConfig(overrides.value, source.value || lastSource.value))
const composer = ref(null)
const contextWindow = computed(() => modelContextWindow(store.catalog, config.value.model))
function use (prompt) { text.value = prompt; nextTick(() => composer.value?.focus()) }
function chooseAgent (id) { selectedAgent.value = id; overrides.value = {} }
function override (key, value) {
  const changedModel = key === 'model' && value !== config.value.model
  overrides.value = { ...overrides.value, [key]: value, ...(changedModel ? { reasoning_effort: null } : {}) }
}
const toolSummary = computed(() => {
  const total = (store.catalog.tools || []).length
  return config.value.tools === null ? `${total} available` : `${config.value.tools.length} of ${total} enabled`
})
</script>

<style scoped>
.welcome { flex: 1; display: flex; justify-content: center; padding: clamp(48px, 11vh, 120px) 36px 40px; background: radial-gradient(ellipse at 50% 24%, color-mix(in srgb, var(--accent) 3%, transparent), transparent 65%); }
.welcome__inner { width: 100%; max-width: 740px; }
.welcome__intro { text-align: center; margin-bottom: 32px; }
.welcome__mark { width: 72px; height: 72px; margin: 0 auto 22px; }
.welcome__eyebrow { display: block; margin-bottom: 12px; text-transform: uppercase; letter-spacing: 0.22em; font-size: 0.625rem; font-weight: 600; color: var(--accent); }
.welcome__title { margin: 0; font-size: clamp(1.5625rem, 2.5vw, 2.25rem); font-weight: 500; line-height: 1.2; letter-spacing: -0.045em; }
.welcome__lead { margin: 14px 0 0; color: var(--text-muted); font-size: 0.8125rem; }
.welcome__starters { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 10px; margin-top: 18px; }
.starter { display: flex; align-items: center; gap: 8px; padding: 13px 12px; border: 1px solid var(--border); border-radius: var(--radius-md); background: var(--surface-panel); color: var(--text-muted); font: inherit; font-size: 0.71875rem; text-align: left; cursor: pointer; transition: border-color var(--transition), color var(--transition), background var(--transition); }
.starter:hover { border-color: var(--border-strong); background: var(--surface-raised); color: var(--text); }
.starter__icon { flex: none; font-size: 1.125rem; color: var(--text-dim); }
.starter__arrow { flex: none; font-size: 0.75rem; opacity: 0.5; }
.welcome__details { margin-top: 26px; color: var(--text-dim); font-size: 0.6875rem; }
.welcome__details > summary { width: fit-content; margin: 0 auto; cursor: pointer; }
.welcome__facts { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 18px 32px; padding: 24px 0; }
.welcome__facts > div { min-width: 0; }
.welcome__facts span { display: flex; align-items: center; gap: 12px; margin-bottom: 5px; }
.welcome__facts strong { display: block; font-size: 0.75rem; font-weight: 500; color: var(--text-muted); overflow-wrap: anywhere; }
.welcome__facts button { border: 0; padding: 0; background: none; color: var(--accent); font: inherit; cursor: pointer; }
.welcome__warn { display: flex; align-items: center; flex-wrap: wrap; gap: 10px; margin-top: 24px; padding: 12px 14px; border: 1px solid var(--warning-soft); border-radius: var(--radius-md); background: var(--warning-soft); color: var(--warning); font-size: 0.75rem; }
.welcome__warn .material-icons { font-size: 1.0625rem; }
@media (max-width: 1200px) { .welcome__starters { grid-template-columns: repeat(2, minmax(0, 1fr)); } }
@media (max-width: 900px) { .welcome { padding: 32px max(16px, env(safe-area-inset-right)) max(24px, env(safe-area-inset-bottom)) max(16px, env(safe-area-inset-left)); } }
</style>
