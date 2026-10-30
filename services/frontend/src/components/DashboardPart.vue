<template>
  <div class="dashboard-part" :class="`dashboard-part--${tone}`">
    <template v-if="['metric', 'status'].includes(component)">
      <div v-if="props.label" class="caption muted">{{ props.label }}</div>
      <div :class="component === 'metric' ? 'dashboard-part__metric' : 'dashboard-part__status'">{{ text(props.value) }}</div>
    </template>
    <p v-else-if="component === 'text'" class="dashboard-part__text">{{ props.text }}</p>
    <ul v-else-if="component === 'list'" class="dashboard-part__list">
      <li v-for="(item, index) in props.items?.slice(0, 100)" :key="index">
        <a v-if="href(item?.href)" :href="href(item.href)" target="_blank" rel="noopener noreferrer">{{ text(item.title ?? item.label ?? item.value ?? item) }}</a>
        <span v-else>{{ text(item?.title ?? item?.label ?? item?.value ?? item) }}</span>
        <small v-if="item?.description" class="muted">{{ text(item.description) }}</small>
      </li>
      <li v-if="!props.items?.length" class="muted">No items.</li>
    </ul>
    <div v-else-if="component === 'table'" class="dashboard-part__table" tabindex="0" aria-label="Scrollable data table">
      <table><thead><tr><th v-for="column in columns" :key="column.key" scope="col">{{ column.label }}</th></tr></thead>
        <tbody><tr v-for="(item, index) in props.items?.slice(0, 100)" :key="index"><td v-for="column in columns" :key="column.key">{{ text(item?.[column.key]) }}</td></tr></tbody>
      </table>
      <p v-if="!props.items?.length" class="caption muted">No rows.</p>
    </div>
    <dl v-else-if="component === 'key-value'" class="dashboard-part__values">
      <template v-for="(value, key) in props.items" :key="key"><dt class="muted">{{ key }}</dt><dd>{{ text(value) }}</dd></template>
    </dl>
    <template v-else-if="component === 'action'">
      <button class="btn btn--primary" :disabled="!action?.enabled || Boolean(activeRun) || busy" @click="$emit('action', props.action)">{{ action?.label || 'Workflow unavailable' }}</button>
      <p v-if="!action?.enabled" class="caption muted">{{ action?.reason || 'Action registration is unavailable.' }}</p>
      <p v-if="activeRun" class="caption"><RouterLink :to="activeRun.url">{{ activeRun.status === 'uncertain' ? 'Submission uncertain — inspect run' : 'Workflow running — view progress' }}</RouterLink></p>
    </template>
    <template v-else-if="component === 'workflow'">
      <ul class="dashboard-part__list"><li v-for="run in matchingRuns" :key="run.workflow_id"><RouterLink :to="run.url">{{ run.workflow }}</RouterLink><span class="chip">{{ run.status }}</span><small v-if="run.result" class="muted">{{ text(run.result) }}</small></li></ul>
      <p v-if="!matchingRuns.length" class="caption muted">No runs from this dashboard yet.</p>
    </template>
    <p v-if="props.description" class="caption muted">{{ props.description }}</p>
    <a v-if="href(props.href)" class="caption" :href="href(props.href)" target="_blank" rel="noopener noreferrer">Open details ↗</a>
  </div>
</template>

<script setup>
import { computed } from 'vue'
import { dashboardText as text, safeDashboardHref as href } from '../dashboard'
const model = defineProps({ component: String, props: { type: Object, default: () => ({}) }, actions: Object, runs: Array, busy: Boolean })
defineEmits(['action'])
const tone = computed(() => ['accent', 'success', 'warning', 'danger'].includes(model.props.tone) ? model.props.tone : 'neutral')
const columns = computed(() => model.props.columns || Object.keys(model.props.items?.[0] || {}).slice(0, 12).map(key => ({ key, label: key })))
const action = computed(() => model.actions?.[model.props.action])
const matchingRuns = computed(() => (model.runs || []).filter(run => model.props.action ? run.action_id === model.props.action : run.workflow === model.props.workflow))
const activeRun = computed(() => (model.runs || []).find(run => run.action_id === model.props.action && ['starting', 'running', 'uncertain'].includes(run.status)))
</script>

<style scoped>
.dashboard-part { min-width: 0; overflow-wrap: anywhere; }
.dashboard-part--accent { --part-tone: var(--accent); }
.dashboard-part--success { --part-tone: var(--success); }
.dashboard-part--warning { --part-tone: var(--warning); }
.dashboard-part--danger { --part-tone: var(--danger); }
.dashboard-part__metric { font-size: 2rem; font-weight: 650; letter-spacing: -.035em; color: var(--part-tone, var(--text)); }
.dashboard-part__status { font-size: 1.1rem; font-weight: 600; color: var(--part-tone, var(--text)); }
.dashboard-part__text { white-space: pre-wrap; margin: 0; }
.dashboard-part__list { list-style: none; padding: 0; margin: 0; display: grid; gap: .75rem; }
.dashboard-part__list li { display: flex; flex-wrap: wrap; gap: .35rem; align-items: baseline; }
.dashboard-part__list small { flex-basis: 100%; }
.dashboard-part__table { overflow-x: auto; max-width: 100%; }
.dashboard-part__table table { border-collapse: collapse; width: 100%; font-size: .8rem; }
.dashboard-part__table :is(th, td) { text-align: left; vertical-align: top; padding: .5rem; border-bottom: 1px solid var(--border); min-width: 5rem; max-width: 20rem; overflow-wrap: anywhere; }
.dashboard-part__table th { color: var(--text-muted); font-weight: 500; }
.dashboard-part__values { display: grid; grid-template-columns: minmax(0, 1fr) minmax(0, 2fr); gap: .5rem; margin: 0; font-size: .85rem; }
.dashboard-part__values dd { margin: 0; }
</style>
