<template>
  <div class="message-origin" :class="{ 'bubble__sender': origin.kind === 'agent' }" :data-origin-kind="origin.kind">
    <template v-if="origin.kind === 'agent'">
      <AgentAvatar :agent="caller" class="message-origin__avatar" />
      <span>Called by <strong>{{ caller.name }}</strong></span>
      <RouterLink v-if="links.chat" :to="links.chat" class="message-origin__link">Source chat<span class="material-icons" aria-hidden="true">arrow_outward</span></RouterLink>
    </template>
    <template v-else>
      <span class="material-icons" aria-hidden="true">bolt</span>
      <span>{{ origin.relation === 'result' ? 'Result from' : 'Called by workflow' }}</span>
      <RouterLink :to="links.workflow" class="message-origin__link message-origin__name">{{ origin.workflow }}</RouterLink>
      <span v-if="triggerLabel(origin.trigger)" class="message-origin__trigger">{{ triggerLabel(origin.trigger) }}</span>
      <RouterLink v-if="links.run" :to="links.run" class="message-origin__link">View run<span class="material-icons" aria-hidden="true">arrow_outward</span></RouterLink>
      <span v-if="origin.status" class="chip" :class="`chip--${RUN_TONE[origin.status] || ''}`">{{ origin.status }}</span>
    </template>
    <span v-if="root" class="message-origin__root">
      <span class="material-icons" aria-hidden="true">account_tree</span>
      <RouterLink :to="rootLinks.workflow" class="message-origin__link">{{ root.workflow }}</RouterLink>
      <span v-if="triggerLabel(root.trigger)">{{ triggerLabel(root.trigger) }}</span>
      <RouterLink v-if="rootLinks.run" :to="rootLinks.run" class="message-origin__link">View workflow run</RouterLink>
    </span>
  </div>
</template>

<script setup>
import { computed } from 'vue'
import AgentAvatar from './AgentAvatar.vue'
import { store } from '../store'
import { RUN_TONE } from '../format'
import { originLinks, triggerLabel } from '../message-origin'

const props = defineProps({ origin: { type: Object, required: true } })
const links = computed(() => originLinks(props.origin))
const root = computed(() => props.origin.root?.kind === 'workflow' ? props.origin.root : null)
const rootLinks = computed(() => originLinks(root.value))
const caller = computed(() => ({
  ...store.catalog.agents?.find(agent => agent.id === props.origin.agent_id),
  id: props.origin.agent_id, name: props.origin.agent_name || 'Agent'
}))
</script>

<style scoped>
.message-origin { display: flex; flex-wrap: wrap; align-items: center; gap: 6px 8px; margin-bottom: 12px; color: var(--text-muted); font-size: 0.75rem; line-height: 1.5; }
.message-origin > .material-icons { color: var(--workflow-color); font-size: 1rem; }
.message-origin__avatar { --agent-avatar-size: 24px; }
.message-origin__name { min-width: 0; overflow-wrap: anywhere; font-weight: 600; }
.message-origin__link { display: inline-flex; align-items: center; gap: 3px; color: var(--text-muted); text-decoration: underline; text-underline-offset: 3px; }
.message-origin__link:hover { color: var(--accent); }
.message-origin__link:focus-visible { outline: 2px solid var(--accent); outline-offset: 3px; }
.message-origin__link .material-icons { font-size: 0.8125rem; }
.message-origin__trigger { color: var(--text-dim); }
.message-origin__root { display: flex; flex-wrap: wrap; align-items: center; gap: 6px 8px; flex-basis: 100%; }
.message-origin__root > .material-icons { font-size: 0.875rem; color: var(--workflow-color); }
</style>
