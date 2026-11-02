<template>
  <nav class="agents" aria-label="Chats by agent">
    <div class="agents__list">
      <button class="agents__item agents__all" type="button" aria-label="Show all chats" aria-describedby="agent-activity-all" :aria-pressed="!selectedId" @click="select(null)">
        <AgentAvatar /><span class="sr-only">All</span>
        <AgentActivity :counts="store.chatActivity" description-id="agent-activity-all" />
        <q-tooltip anchor="center right" self="center left" :offset="[10, 10]">All · {{ store.chats.length }} chats · {{ activityLabel(store.chatActivity) }}</q-tooltip>
      </button>
      <div class="agents__divider" aria-hidden="true" />
      <div class="agents__people">
        <button v-for="agent in agents" :key="agent.id" class="agents__item" type="button" :aria-label="`Chats with ${agent.name}`" :aria-describedby="`agent-activity-${agent.id}`" :aria-pressed="selectedId === agent.id" @click="select(agent.id)" @keydown="openAgentMenu($event, agent.id)">
          <AgentAvatar :agent="agent" /><span class="sr-only">{{ agent.name }}</span>
          <AgentActivity :counts="agentActivity(store.chatActivity, agent.id)" :description-id="`agent-activity-${agent.id}`" />
          <q-tooltip anchor="center right" self="center left" :offset="[10, 10]">{{ agent.name }} · {{ activityLabel(agentActivity(store.chatActivity, agent.id)) }}</q-tooltip>
          <q-menu :ref="menu => registerMenu(agent.id, menu)" context-menu class="pick-menu" @before-show="openAgentMenus.add(agent.id)" @hide="openAgentMenus.delete(agent.id)">
            <RouterLink v-close-popup class="pick-menu__item agents__menu-link" :to="editorLocation(agent)"><span class="material-icons" aria-hidden="true">edit</span>Edit agent</RouterLink>
            <RouterLink v-close-popup class="pick-menu__item agents__menu-link" :to="editorLocation(agent, true)"><span class="material-icons" aria-hidden="true">add_photo_alternate</span>Change image</RouterLink>
          </q-menu>
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
import AgentActivity from './AgentActivity.vue'
import { agentActivity, activityLabel } from '../agent-activity'

const route = useRoute(), router = useRouter()
const agents = computed(() => store.catalog.agents || [])
const selectedId = computed(() => typeof route.query.agent === 'string' ? route.query.agent : null)
const openAgentMenus = new Set()
function select (id) {
  // Touch release can synthesize a click after a long-press opens the menu.
  if (openAgentMenus.has(id)) return
  router.push(chatLocation('', id))
}
const agentMenus = new Map()
function registerMenu (id, menu) {
  if (menu) agentMenus.set(id, menu)
  else { agentMenus.delete(id); openAgentMenus.delete(id) }
}
function openAgentMenu (event, id) {
  if (event.key !== 'ContextMenu' && !(event.key === 'F10' && event.shiftKey)) return
  event.preventDefault()
  agentMenus.get(id)?.show(event)
}
function editorLocation (agent, image = false) {
  return { path: '/settings/agents', query: { edit: agent.id }, hash: image ? '#profile-image' : '#agent-profiles' }
}
// A removed or invalid selection must not leave the user in an empty dead end.
watch([agents, () => store.catalogLoaded, selectedId], () => {
  if (store.catalogLoaded && selectedId.value && !agents.value.some(agent => agent.id === selectedId.value)) {
    router.replace(chatLocation(route.params.id || '', null))
  }
}, { immediate: true })
</script>

<style scoped>
.agents { display: flex; flex-direction: column; min-height: 0; min-width: 0; width: 68px; padding: 10px 6px 8px; border-right: 1px solid var(--border); background: var(--surface-panel); }
.agents__list { display: flex; flex-direction: column; align-items: center; flex: 1; min-height: 0; gap: 4px; }
.agents__people { display: flex; flex-direction: column; align-items: center; gap: 4px; overflow-y: auto; min-height: 0; width: 100%; scrollbar-width: thin; }
.agents__item { position: relative; display: grid; place-items: center; flex: none; width: 48px; height: 48px; padding: 4px; border: 0; border-radius: var(--radius-sm); background: transparent; color: var(--text-muted); font: inherit; cursor: pointer; --agent-avatar-size: 40px; }
.agents__divider { flex: none; width: 24px; height: 1px; margin: 3px 0; background: var(--border-strong); }
.agents__item::before { content: ''; position: absolute; left: 0; width: 3px; height: 0; border-radius: var(--radius-pill); background: var(--text-muted); transition: height var(--transition); }
.agents__item:hover::before { height: 12px; }
.agents__item:hover, .agents__manage:hover { background: var(--surface-hover); color: var(--text); }
.agents__item[aria-pressed="true"] { background: var(--accent-soft); color: var(--accent); }
.agents__item[aria-pressed="true"]::before { height: 26px; background: var(--accent); }
.agents__item:focus-visible, .agents__manage:focus-visible { outline: 2px solid var(--accent); outline-offset: -2px; }
.agents__manage { display: grid; place-items: center; flex: none; min-height: 44px; margin-top: 8px; border-radius: var(--radius-sm); color: var(--text-dim); text-decoration: none; }
.agents__manage .material-icons { font-size: 1.25rem; }
.agents__menu-link { text-decoration: none; }
.agents__menu-link .material-icons { font-size: 1.125rem; }
.agents__status { padding: 8px 0; text-align: center; font-size: 0.625rem; overflow-wrap: anywhere; }
@media (max-width: 900px) {
  .agents { flex-direction: row; width: 100%; padding: calc(4px + env(safe-area-inset-top)) max(6px, env(safe-area-inset-right)) 4px max(6px, env(safe-area-inset-left)); border-right: 0; border-bottom: 1px solid var(--border); }
  .agents__list { flex-direction: row; min-width: 0; }
  .agents__people { flex-direction: row; overflow-y: hidden; overflow-x: auto; min-width: 0; }
  .agents__item { width: 44px; height: 48px; --agent-avatar-size: 36px; }
  .agents__item::before { left: auto; bottom: 0; width: 0; height: 3px; transition: width var(--transition); }
  .agents__item:hover::before { width: 12px; height: 3px; }
  .agents__item[aria-pressed="true"]::before { width: 24px; height: 3px; }
  .agents__divider { width: 1px; height: 24px; margin: 0 3px; }
  .agents__manage { width: 44px; margin: 0 0 0 4px; }
}
</style>
