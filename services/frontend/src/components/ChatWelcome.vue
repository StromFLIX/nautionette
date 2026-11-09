<template>
  <div class="welcome scroll-y" data-skin-part="welcome">
    <button class="btn btn--icon welcome__back" aria-label="Back to chats" @click="backTo(chatLocation())">
      <span class="material-icons" aria-hidden="true">arrow_back</span>
    </button>
    <div class="welcome__inner">
      <div class="welcome__intro">
        <div class="welcome__wordmark"><BrandMark class="welcome__mark" /><span>{{ brand.name }}</span></div>
        <h1 class="welcome__title">What should happen next?</h1>
        <p class="welcome__lead">A little more room for what’s on your mind.</p>
      </div>
      <Composer ref="composer" v-model="text" v-model:attachments="attachments" :agent-id="config.agent_id" :agent-name="config.agent_name || ''" :agent-customized="Boolean(config.agent_customized)" :system-prompt="config.system_prompt || ''"
        :agent-set="config.agent_set" :model="config.model" :reasoning-effort="config.reasoning_effort" :tools="config.tools" :project-ids="config.project_ids" :packages="config.packages || []"
        variant="welcome" :busy="busy" :configuration-ready="store.catalogLoaded" placeholder="Ask, build, or automate…"
        @update:agent-id="chooseAgent" @update:agent-set="override('agent_set', $event)" @update:model="override('model', $event)"
        @update:reasoning-effort="override('reasoning_effort', $event)" @update:tools="override('tools', $event)" @update:project-ids="override('project_ids', $event)"
        @update:packages="override('packages', $event)"
        @send="$emit('start', { text, configuration: config, attachments })" />

      <nav class="welcome__paths" aria-label="Ways to work">
        <RouterLink to="/settings/creative-apps"><i class="welcome__tile welcome__tile--create" aria-hidden="true" /><span>Create<small>Code, slides, drawings</small></span></RouterLink>
        <RouterLink to="/workflows"><i class="welcome__tile welcome__tile--process" aria-hidden="true" /><span>Process<small>Repeatable workflows</small></span></RouterLink>
        <RouterLink to="/apps"><i class="welcome__tile welcome__tile--show" aria-hidden="true" /><span>Show<small>Your own miniapps</small></span></RouterLink>
      </nav>
      <p v-if="draftError" class="caption" role="alert">{{ draftError }}</p>
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
import { computed, nextTick, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { backTo, chatLocation } from '../router'
import Composer from './Composer.vue'
import BrandMark from './BrandMark.vue'
import { brand } from '../brand'
import { actions, chatSettings, store } from '../store'
import { preferences } from '../preferences'
import { agentConfig, copyConfig, defaultChatConfig, resolveAgentConfig } from '../agent-config'
import { reusableChatSettings } from '../new-chat-settings'
import { chatCacheScope } from '../chat-cache'
import { useChatDraft } from '../chat-drafts'

defineProps({ busy: { type: Boolean, default: false } })
defineEmits(['start'])
const { text, attachments, error: draftError } = useChatDraft(ref('local:welcome'))
// Capture history once per new-chat draft, so saving an override cannot feed back
// into its own source. With no history (or in defaults mode), follow async defaults.
const scope = chatCacheScope()
const remembered = chatSettings.load(scope)
const route = useRoute(), router = useRouter()
const composer = ref(null)
async function focusWelcome () {
  // /chats itself is the list on mobile: never focus its hidden composer.
  if (route.query.welcome !== '1' || store.needsToken || store.needsServer) return
  await nextTick()
  composer.value?.focus()
}
onMounted(focusWelcome)
watch(() => [route.query.welcome, store.needsToken, store.needsServer], focusWelcome)
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
}
</script>

<style scoped>
.welcome { position: relative; isolation: isolate; flex: 1; display: flex; justify-content: center; padding: clamp(48px, 10vh, 110px) 40px 40px; background: var(--surface-app); }
.welcome::before { content: ''; position: absolute; z-index: -1; inset: 0; pointer-events: none; background-image: linear-gradient(var(--border) 1px, transparent 1px), linear-gradient(90deg, var(--border) 1px, transparent 1px); background-size: 28px 28px; opacity: .35; mask-image: radial-gradient(ellipse at 50% 0, #000, transparent 72%); }
.welcome__back { display: none; }
.welcome__inner { width: 100%; max-width: 760px; min-width: 0; }
.welcome__intro { text-align: center; margin-bottom: 32px; }
.welcome__wordmark { display: flex; justify-content: center; align-items: center; gap: 14px; margin-bottom: 36px; }
.welcome__wordmark > span { font-size: clamp(1.7rem, 3.2vw, 2.5rem); font-weight: 600; letter-spacing: -.075em; line-height: 1.2; }
.welcome__mark { width: 44px; height: 44px; }
.welcome__title { margin: 0; font-size: clamp(1.3rem, 2vw, 1.75rem); font-weight: 500; line-height: 1.25; letter-spacing: -.05em; }
.welcome__lead { margin: 14px 0 0; color: var(--text-muted); font-family: 'Source Serif', Georgia, serif; font-size: 1.125rem; line-height: 1.5; }
.welcome__paths { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 18px; margin-top: 24px; }
.welcome__paths a { display: flex; align-items: flex-start; gap: 9px; min-width: 0; min-height: 44px; padding: 8px 0; color: var(--text); text-decoration: none; font-size: .75rem; }
.welcome__paths small { display: block; color: var(--text-muted); font-size: .625rem; margin-top: 3px; }
.welcome__tile { display: block; flex: none; width: 9px; height: 9px; margin-top: 5px; border-radius: 3px; }
.welcome__tile--create { background: var(--agent-color); }
.welcome__tile--process { background: var(--workflow-color); }
.welcome__tile--show { background: var(--session-color); }
@media (hover: hover) and (pointer: fine) { .welcome__paths a:hover { color: var(--accent); } }
@media (max-width: 600px) { .welcome__paths { gap: 10px; } .welcome__paths a { gap: 6px; font-size: .6875rem; } .welcome__paths small { font-size: .5625rem; } .welcome__wordmark { margin-bottom: 25px; } }
@media (max-width: 370px) { .welcome__paths { grid-template-columns: 1fr; gap: 0; } .welcome__paths a { align-items: center; padding: 2px 0; } .welcome__paths a > span { display: flex; gap: 10px; align-items: baseline; } .welcome__paths small { margin: 0; } }
.welcome__warn { display: flex; align-items: center; flex-wrap: wrap; gap: 10px; margin-top: 24px; padding: 12px 14px; border: 1px solid var(--warning-soft); border-radius: var(--radius-md); background: var(--warning-soft); color: var(--warning); font-size: 0.75rem; }
.welcome__warn .material-icons { font-size: 1.0625rem; }
@media (max-width: 900px) {
  .welcome { padding: calc(56px + env(safe-area-inset-top)) max(16px, env(safe-area-inset-right)) max(24px, env(safe-area-inset-bottom)) max(16px, env(safe-area-inset-left)); }
  .welcome__back { display: inline-flex; position: absolute; top: calc(8px + env(safe-area-inset-top)); left: max(8px, env(safe-area-inset-left)); }
  .welcome__intro { margin-bottom: 20px; }
  .welcome__wordmark { margin-bottom: 18px; }
}
/* Keep the invitation and editor together when a phone keyboard reduces the viewport. */
@media (max-width: 900px) and (max-height: 600px) {
  .welcome { padding-top: calc(48px + env(safe-area-inset-top)); }
  .welcome__wordmark, .welcome__lead, .welcome__paths { display: none; }
  .welcome__intro { margin-bottom: 16px; }
}
</style>
