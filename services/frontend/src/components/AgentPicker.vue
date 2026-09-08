<template>
  <q-menu ref="menu" anchor="top left" self="bottom left" class="agent-picker" @show="query = ''" @keydown.esc.stop.prevent="menu?.hide($event)">
    <div class="agent-picker__head"><input v-model="query" class="field" aria-label="Search agents" placeholder="Search agents" /></div>
    <div class="agent-picker__list scroll-y" role="menu" aria-label="Saved agents">
      <button v-for="agent in choices" :key="agent.id || 'global'" v-close-popup class="agent-picker__choice" type="button" role="menuitemradio"
        :aria-label="agent.name" :aria-checked="agent.id === modelValue" @click="$emit('update:modelValue', agent.id)">
        <span class="material-icons agent-picker__icon" aria-hidden="true">{{ agent.id ? 'smart_toy' : 'tune' }}</span>
        <span class="grow"><strong>{{ agent.name }}</strong><span v-if="agent.description" class="caption dim">{{ agent.description }}</span>
          <span class="caption dim">{{ toolLabel(agent.resolved.tools) }} · {{ projectLabel(agent.resolved.project_ids) }}</span>
        </span>
        <span v-if="agent.id === (store.catalog.default_agent_id ?? null)" class="chip chip--accent">Default</span>
        <span v-if="agent.id === modelValue" class="material-icons agent-picker__check" aria-hidden="true">check</span>
      </button>
      <p v-if="!choices.length" class="caption dim agent-picker__note">No matching agents.</p>
    </div>
    <div class="agent-picker__foot"><p class="caption dim">Applies the agent's settings to this chat.</p>
      <RouterLink v-close-popup class="btn btn--sm" to="/settings/agents#agent-profiles">Manage agents<span class="material-icons" aria-hidden="true">arrow_outward</span></RouterLink>
    </div>
  </q-menu>
</template>

<script setup>
import { computed, ref } from 'vue'
import { store } from '../store'
import { globalChatConfig, resolveAgentConfig, toolLabel, projectLabel } from '../agent-config'
defineProps({ modelValue: { type: String, default: null } })
defineEmits(['update:modelValue'])
const menu = ref(null)
const query = ref('')
const choices = computed(() => {
  const defaults = globalChatConfig(store.catalog)
  return [{ id: null, name: 'Global defaults', resolved: defaults }, ...(store.catalog.agents || []).map(agent => ({
    ...agent, resolved: agent.resolved || resolveAgentConfig(agent.config, defaults)
  }))].filter(agent => `${agent.name} ${agent.description || ''}`.toLowerCase().includes(query.value.trim().toLowerCase()))
})
</script>

<style>
.agent-picker { width: min(420px, calc(100vw - 24px)); min-width: 0; }
</style>
<style scoped>
.agent-picker__head { padding: 8px; border-bottom: 1px solid var(--border); }
.agent-picker__head input { width: 100%; }
.agent-picker__list { max-height: min(360px, 45dvh); }
.agent-picker__choice { display: flex; align-items: center; gap: 10px; width: 100%; padding: 12px 10px; border: 0; border-radius: var(--radius-sm); background: none; color: var(--text); font: inherit; text-align: left; cursor: pointer; }
.agent-picker__choice:hover { background: var(--surface-hover); }
.agent-picker__choice > .grow { display: grid; gap: 4px; min-width: 0; overflow-wrap: anywhere; }
.agent-picker__choice strong { font-size: 13px; font-weight: 600; }
.agent-picker__icon { font-size: 19px; color: var(--text-muted); }
.agent-picker__check { font-size: 16px; color: var(--accent); }
.agent-picker__choice .chip { flex: none; }
.agent-picker__foot { padding: 8px; border-top: 1px solid var(--border); }
.agent-picker__foot p { margin: 4px 6px 8px; }
.agent-picker__foot .material-icons { font-size: 14px; }
.agent-picker__note { padding: 12px; margin: 0; }
</style>
