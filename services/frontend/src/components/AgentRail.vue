<template>
  <nav class="agents" aria-label="Chats by agent">
    <div class="agents__list">
      <button class="agents__item agents__all" type="button" aria-label="Show all chats" :aria-pressed="!selectedId" :title="`All · ${store.chats.length} chats`" @click="select(null)">
        <AgentAvatar /><span class="agents__name">All</span>
      </button>
      <div class="agents__people">
        <button v-for="agent in agents" :key="agent.id" class="agents__item" type="button" :aria-label="`Chats with ${agent.name}`" :aria-pressed="selectedId === agent.id" :title="agent.name" @click="select(agent.id)">
          <AgentAvatar :agent="agent" /><span class="agents__name">{{ agent.name }}</span>
        </button>
        <span v-if="!store.catalogLoaded" class="agents__status caption dim" role="status">{{ store.catalogError ? 'Unavailable' : 'Loading…' }}</span>
      </div>
    </div>
    <RouterLink class="agents__manage" to="/settings/agents#agent-profiles" aria-label="Manage agents" title="Manage agents"><span class="material-icons" aria-hidden="true">person_add_alt</span></RouterLink>
  </nav>
</template>

<script setup>
import { computed, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { store } from '../store'
import { chatLocation } from '../router'
import AgentAvatar from './AgentAvatar.vue'

const route = useRoute(), router = useRouter()
const agents = computed(() => store.catalog.agents || [])
const selectedId = computed(() => typeof route.query.agent === 'string' ? route.query.agent : null)
function select (id) { router.push(chatLocation('', id)) }
// A removed or invalid selection must not leave the user in an empty dead end.
watch([agents, () => store.catalogLoaded, selectedId], () => {
  if (store.catalogLoaded && selectedId.value && !agents.value.some(agent => agent.id === selectedId.value)) {
    router.replace(chatLocation(route.params.id || '', null))
  }
}, { immediate: true })
</script>

<style scoped>
.agents { display: flex; flex-direction: column; min-height: 0; min-width: 0; width: 92px; padding: 14px 6px 10px; border-right: 1px solid var(--border); background: var(--surface-panel); }
.agents__list { display: flex; flex-direction: column; flex: 1; min-height: 0; gap: 8px; }
.agents__people { display: flex; flex-direction: column; gap: 6px; overflow-y: auto; min-height: 0; padding: 2px; }
.agents__item { display: flex; flex-direction: column; align-items: center; flex: none; gap: 7px; width: 100%; min-height: 76px; padding: 10px 4px; border: 1px solid transparent; border-radius: var(--radius-md); background: transparent; color: var(--text-muted); font: inherit; cursor: pointer; }
.agents__all { flex: none; border-bottom-color: var(--border); }
.agents__item:hover, .agents__manage:hover { background: var(--surface-hover); color: var(--text); }
.agents__item[aria-pressed="true"] { background: var(--accent-soft); border-color: var(--border-strong); color: var(--accent); }
.agents__name { display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden; overflow-wrap: anywhere; max-width: 100%; font-size: 0.6875rem; font-weight: 600; line-height: 1.3; }
.agents__item:focus-visible, .agents__manage:focus-visible { outline: 2px solid var(--accent); outline-offset: -2px; }
.agents__manage { display: grid; place-items: center; flex: none; min-height: 44px; margin-top: 8px; border-radius: var(--radius-md); color: var(--text-dim); text-decoration: none; }
.agents__manage .material-icons { font-size: 1.25rem; }
.agents__status { padding: 8px 0; text-align: center; font-size: 0.625rem; overflow-wrap: anywhere; }
@media (max-width: 900px) {
  .agents { flex-direction: row; width: 100%; padding: calc(6px + env(safe-area-inset-top)) max(8px, env(safe-area-inset-right)) 6px max(8px, env(safe-area-inset-left)); border-right: 0; border-bottom: 1px solid var(--border); }
  .agents__list { flex-direction: row; gap: 6px; min-width: 0; }
  .agents__people { flex-direction: row; overflow-y: hidden; overflow-x: auto; min-width: 0; }
  .agents__item { width: 70px; min-height: 68px; padding: 6px 4px; gap: 4px; --agent-avatar-size: 32px; }
  .agents__all { border-bottom-color: transparent; }
  .agents__manage { width: 44px; margin: 0 0 0 4px; }
}
</style>
