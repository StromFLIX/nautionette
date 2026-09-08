<template>
  <h2 class="settings__title">Automation</h2>
  <p class="settings__intro">Schedules, inputs and delivery live with each workflow.</p>
  <section id="workflow-settings" class="setting">
    <div class="row">
      <h3 class="setting__group-title grow">Workflows</h3>
      <RouterLink class="btn btn--sm btn--outline" to="/workflows">Open workflows<span class="material-icons" aria-hidden="true">arrow_outward</span></RouterLink>
    </div>
    <p v-if="!store.workflows.length" class="automation-empty caption muted">No workflows yet. Start a chat to create one.</p>
    <RouterLink v-for="workflow in store.workflows" :key="workflow.name" class="automation-row"
      :to="{ path: `/workflows/${workflow.name}`, query: { tab: 'Run' } }">
      <div class="avatar avatar--square" :style="avatarStyle(workflow.name)"><span class="material-icons" aria-hidden="true">account_tree</span></div>
      <div class="grow"><strong>{{ workflow.title || workflow.name }}</strong><p class="caption muted">{{ workflow.schedule?.description || 'On demand' }}<template v-if="workflow.schedule?.timezone"> · {{ workflow.schedule.timezone }}</template></p></div>
      <span v-if="workflow.settings?.disabled" class="chip chip--warning">Disabled</span>
      <span class="material-icons dim" aria-hidden="true">chevron_right</span>
    </RouterLink>
  </section>
</template>

<script setup>
import { store } from '../../store'
import { avatarStyle } from '../../format'
</script>

<style scoped>
.automation-row { display: flex; align-items: center; gap: 14px; padding: 16px 0; border-bottom: 1px solid var(--border); text-decoration: none; color: var(--text); }
.automation-row:hover { background: var(--surface-hover); }
.automation-row strong { font-size: 13px; overflow-wrap: anywhere; }
.automation-row p { margin: 4px 0 0; }
.automation-empty { padding: 24px 0; }
.btn .material-icons { font-size: 16px; }
</style>
