<template>
  <div class="welcome scroll-y">
    <div class="welcome__inner">
      <img src="/favicon.svg" width="48" height="48" alt="" class="welcome__mark" />
      <h1 class="welcome__title">What should happen?</h1>

      <Composer
        ref="composer"
        v-model="text"
        v-model:agent-set="agentSet"
        v-model:model="model"
        v-model:tools="tools"
        variant="welcome"
        :busy="busy"
        placeholder="e.g. Summarise the changelog at this URL every morning at 8."
        @send="$emit('start', { text, agentSet, model, tools })"
      />
      <div class="welcome__chips">
        <button v-for="prompt in prompts" :key="prompt" class="starter" @click="use(prompt)">
          {{ prompt }}
        </button>
      </div>

      <div class="welcome__facts">
        <div class="fact">
          <span class="material-icons">smart_toy</span>
          <div>
            <div class="fact__title">{{ agentSet || 'default' }}</div>
            <div class="caption dim">agent set</div>
          </div>
        </div>
        <div class="fact">
          <span class="material-icons">memory</span>
          <div>
            <div class="fact__title truncate">{{ model || store.catalog.default_model || 'no model' }}</div>
            <div class="caption dim">model</div>
          </div>
        </div>
        <div class="fact">
          <span class="material-icons">handyman</span>
          <div>
            <div class="fact__title">{{ toolSummary }}</div>
            <button class="caption fact__link" @click="actions.openSettings('mcp')">manage</button>
          </div>
        </div>
        <div class="fact">
          <span class="material-icons">history</span>
          <div>
            <div class="fact__title">{{ compactChars(historyBudget(model)) }} chars</div>
            <button class="caption fact__link" @click="actions.openSettings('general')">context</button>
          </div>
        </div>
      </div>

      <p v-if="!store.system.model_key_present" class="welcome__warn caption">
        <span class="material-icons">warning</span>
        No model key is configured, so agent calls will fail. Set
        <code>OPENROUTER_API_KEY</code> and restart the gateway.
      </p>
    </div>
  </div>
</template>

<script setup>
import { computed, nextTick, ref, watch } from 'vue'
import Composer from './Composer.vue'
import { compactChars } from '../format'
import { actions, historyBudget, store } from '../store'

defineProps({ busy: { type: Boolean, default: false } })
defineEmits(['start'])

const prompts = [
  'Summarise the top Hacker News stories every morning at 8',
  'Check this URL for changes and tell me when it moves',
  'Draft a weekly digest from these release notes',
  'Watch an RSS feed and save anything about Temporal'
]

const text = ref('')
const agentSet = ref(store.catalog.default_agent_set || 'default')
const model = ref(store.catalog.default_model || '')
const tools = ref(null)
const composer = ref(null)

function use (prompt) {
  text.value = prompt
  nextTick(() => composer.value?.focus())
}

const toolSummary = computed(() => {
  const total = (store.catalog.tools || []).length
  return tools.value === null ? `${total} MCP tools` : `${tools.value.length} of ${total} tools`
})

watch(() => store.catalog, (catalog) => {
  if (!model.value) model.value = catalog.default_model || ''
  if (!agentSet.value) agentSet.value = catalog.default_agent_set || 'default'
}, { deep: true })
</script>

<style scoped>
.welcome {
  flex: 1;
  display: flex;
  justify-content: center;
  padding: 6vh 24px 40px;
}

.welcome__inner {
  width: 100%;
  max-width: 720px;
}

.welcome__mark {
  display: block;
  margin: 0 auto 18px;
  opacity: 0.92;
}

.welcome__title {
  margin: 0 0 8px;
  font-size: 26px;
  font-weight: 650;
  letter-spacing: -0.02em;
  text-align: center;
}

.welcome__lead {
  margin: 0 auto 26px;
  max-width: 520px;
  color: var(--text-muted);
  text-align: center;
}

.welcome__chips {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin-top: 14px;
}

.starter {
  padding: 7px 12px;
  border: 1px solid var(--border);
  border-radius: var(--radius-pill);
  background: var(--surface-panel);
  color: var(--text-muted);
  font: inherit;
  font-size: 12.5px;
  cursor: pointer;
  transition: border-color var(--transition), color var(--transition), background var(--transition);
}

.starter:hover {
  border-color: var(--border-strong);
  background: var(--surface-raised);
  color: var(--text);
}

.welcome__facts {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(190px, 1fr));
  gap: 10px;
  margin-top: 34px;
}

.fact {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 12px 14px;
  border: 1px solid var(--border);
  border-radius: var(--radius-md);
  background: var(--surface-panel);
  min-width: 0;
}

.fact > div {
  min-width: 0;
}

.fact .material-icons {
  font-size: 19px;
  color: var(--accent-hover);
}

.fact__title {
  font-size: 13px;
  font-weight: 600;
}

.fact__link {
  border: none;
  background: none;
  padding: 0;
  color: var(--accent-hover);
  font: inherit;
  font-size: 12px;
  cursor: pointer;
}

.welcome__warn {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-top: 20px;
  padding: 10px 12px;
  border-radius: var(--radius-md);
  background: var(--warning-soft);
  color: var(--warning);
}

.welcome__warn .material-icons {
  font-size: 17px;
}
</style>
