<template>
  <nav v-if="items.length || !pinnedOnly" class="dashboard-links" aria-label="Agent dashboards">
    <div v-if="pinnedOnly" class="dashboard-links__heading section-label">Dashboards <RouterLink :to="{ path: '/dashboards', query: route.query.agent ? { agent: route.query.agent } : {} }">All</RouterLink></div>
    <RouterLink v-for="item in items" :key="item.id" :to="location(item)" class="row-item dashboard-links__item" :class="{ 'row-item--active': route.name === 'dashboards' && route.params.id === item.id }">
      <span class="material-icons" aria-hidden="true">space_dashboard</span>
      <span class="dashboard-links__body"><span class="truncate">{{ item.title }}</span><small v-if="!pinnedOnly" class="muted truncate">{{ item.description || agentName(item.agent_id) }}</small></span>
    </RouterLink>
    <p v-if="store.dashboardsError && !pinnedOnly" class="caption dashboard-links__empty" role="status">{{ store.dashboardsError }}</p>
    <p v-else-if="!items.length && !pinnedOnly" class="caption muted dashboard-links__empty">No dashboards yet. Ask an agent to build one.</p>
  </nav>
</template>
<script setup>
import { useRoute } from 'vue-router'
import { store } from '../store'
import { dashboardLocation as location } from '../dashboard'
import { orderedDashboards } from '../dashboard-navigation'
const props = defineProps({ pinnedOnly: Boolean, query: { type: String, default: '' } })
const route = useRoute()
const items = orderedDashboards(() => store.dashboards.filter(item => (!props.pinnedOnly || item.pinned) && (!route.query.agent || item.agent_id === route.query.agent) && `${item.title} ${item.description}`.toLowerCase().includes(props.query.toLowerCase())))
function agentName (id) { return store.catalog.agents?.find(agent => agent.id === id)?.name || 'Default agent' }
</script>
<style scoped>
.dashboard-links { min-width: 0; }
.dashboard-links__heading { display: flex; justify-content: space-between; padding: .5rem .75rem; }
.dashboard-links__heading a { color: var(--text-muted); font-weight: 400; }
.dashboard-links__item { display: flex; gap: .65rem; align-items: center; min-height: 2.75rem; text-decoration: none; }
.dashboard-links__item > .material-icons { color: var(--accent); font-size: 1.1rem; }
.dashboard-links__body { display: flex; flex-direction: column; min-width: 0; }
.dashboard-links__body > span { display: block; }
.dashboard-links__empty { padding: .5rem .75rem; }
</style>
