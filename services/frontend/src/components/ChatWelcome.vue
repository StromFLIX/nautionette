<template>
  <div class="welcome scroll-y" data-skin-part="welcome">
    <div class="welcome__inner">
      <div class="welcome__intro">
        <BrandMark class="welcome__mark" />
        <span class="welcome__eyebrow">Nautionette</span>
        <h1 class="welcome__title">What should happen next?</h1>
        <p class="welcome__lead">An idea. A conversation. A workflow.</p>
      </div>
      <Composer v-model="text" v-model:attachments="attachments" :agent-id="config.agent_id" :agent-name="config.agent_name || ''" :agent-customized="Boolean(config.agent_customized)" :system-prompt="config.system_prompt || ''"
        :agent-set="config.agent_set" :model="config.model" :reasoning-effort="config.reasoning_effort" :tools="config.tools" :project-ids="config.project_ids" :packages="config.packages || []"
        variant="welcome" :busy="busy" :configuration-ready="store.catalogLoaded" placeholder="Ask, build, or automate…"
        @update:agent-id="chooseAgent" @update:agent-set="override('agent_set', $event)" @update:model="override('model', $event)"
        @update:reasoning-effort="override('reasoning_effort', $event)" @update:tools="override('tools', $event)" @update:project-ids="override('project_ids', $event)"
        @update:packages="override('packages', $event)"
        @send="$emit('start', { text, configuration: config, attachments })" />

      <p v-if="!store.catalogLoaded && store.catalogError" class="caption" role="alert">{{ store.catalogError }} <button class="btn btn--sm" @click="actions.loadCatalog(true)">Retry defaults</button></p>

      <div v-if="store.ready && store.system.model_key_present === false" class="welcome__warn" role="status">
        <span class="material-icons" aria-hidden="true">info_outline</span>
        <span class="grow">Connect a model to start chatting.</span>
        <button class="btn btn--sm btn--outline" @click="actions.openSettings('agents')">Connect model<span class="material-icons" aria-hidden="true">arrow_outward</span></button>
      </div>
    </div>
  </div>
</template>

<script setup>
import { computed, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { chatLocation } from '../router'
import Composer from './Composer.vue'
import BrandMark from './BrandMark.vue'
import { actions, chatSettings, store } from '../store'
import { preferences } from '../preferences'
import { agentConfig, copyConfig, defaultChatConfig, resolveAgentConfig } from '../agent-config'
import { reusableChatSettings } from '../new-chat-settings'
import { chatCacheScope } from '../chat-cache'

defineProps({ busy: { type: Boolean, default: false } })
defineEmits(['start'])
const text = ref('')
const attachments = ref([])
// Capture history once per new-chat draft, so saving an override cannot feed back
// into its own source. With no history (or in defaults mode), follow async defaults.
const scope = chatCacheScope()
const remembered = chatSettings.load(scope)
const route = useRoute(), router = useRouter()
const selectedAgent = ref(typeof route.query.agent === 'string' ? route.query.agent : undefined)
const overrides = ref({})
const customized = ref(false)
watch(() => route.query.agent, id => {
  if (typeof id === 'string' && id) { selectedAgent.value = id; overrides.value = {}; customized.value = false }
})
const source = computed(() => {
  if (selectedAgent.value !== undefined) return agentConfig(store.catalog, selectedAgent.value)
  if (preferences.newChatSettings === 'last' && remembered) {
    const saved = store.catalogLoaded ? reusableChatSettings(store.catalog, remembered) : copyConfig(remembered)
    return { ...saved, agent_name: agentConfig(store.catalog, saved.agent_id)?.agent_name ?? null }
  }
  return defaultChatConfig(store.catalog)
})
const lastSource = ref(defaultChatConfig(store.catalog))
watch(source, value => { if (value) lastSource.value = copyConfig(value) }, { immediate: true, deep: true })
const config = computed(() => ({ ...resolveAgentConfig(overrides.value, source.value || lastSource.value),
  agent_customized: customized.value || Boolean((source.value || lastSource.value).agent_customized) }))
watch([config, () => store.catalogLoaded], () => {
  // Remember deliberate choices, including unsent drafts, but never loading placeholders.
  if (store.catalogLoaded && scope === chatCacheScope() && (selectedAgent.value !== undefined || Object.keys(overrides.value).length)) {
    chatSettings.remember(config.value, scope)
  }
}, { deep: true })
function chooseAgent (id) {
  selectedAgent.value = id; overrides.value = {}; customized.value = false
  if (route.query.agent && route.query.agent !== id) router.replace(chatLocation('', null))
}
function override (key, value) {
  if (JSON.stringify(value) === JSON.stringify(config.value[key])) return
  const changedModel = key === 'model' && value !== config.value.model
  overrides.value = { ...overrides.value, [key]: value, ...(changedModel ? { reasoning_effort: null } : {}) }
  customized.value = true
  if (route.query.agent) router.replace(chatLocation('', null))
}
</script>

<style scoped>
.welcome { flex: 1; display: flex; justify-content: center; padding: clamp(48px, 11vh, 120px) 36px 40px; background: radial-gradient(ellipse at 50% 24%, color-mix(in srgb, var(--accent) 3%, transparent), transparent 65%); }
.welcome__inner { width: 100%; max-width: 740px; }
.welcome__intro { text-align: center; margin-bottom: 32px; }
.welcome__mark { width: 72px; height: 72px; margin: 0 auto 22px; }
.welcome__eyebrow { display: block; margin-bottom: 12px; text-transform: uppercase; letter-spacing: 0.22em; font-size: 0.625rem; font-weight: 600; color: var(--accent); }
.welcome__title { margin: 0; font-size: clamp(1.5625rem, 2.5vw, 2.25rem); font-weight: 500; line-height: 1.2; letter-spacing: -0.045em; }
.welcome__lead { margin: 14px 0 0; color: var(--text-muted); font-size: 0.8125rem; }
.welcome__warn { display: flex; align-items: center; flex-wrap: wrap; gap: 10px; margin-top: 24px; padding: 12px 14px; border: 1px solid var(--warning-soft); border-radius: var(--radius-md); background: var(--warning-soft); color: var(--warning); font-size: 0.75rem; }
.welcome__warn .material-icons { font-size: 1.0625rem; }
@media (max-width: 900px) { .welcome { padding: 32px max(16px, env(safe-area-inset-right)) max(24px, env(safe-area-inset-bottom)) max(16px, env(safe-area-inset-left)); } }
</style>
