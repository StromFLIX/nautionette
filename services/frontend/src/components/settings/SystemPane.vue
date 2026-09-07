<template>
  <section class="system-health" aria-labelledby="system-title" :aria-busy="loading">
    <header class="system-health__header">
      <div>
        <h2 id="system-title" class="settings__title">System health</h2>
        <p class="caption dim" role="status">{{ loading ? 'Checking services...' : checkedAt ? `Last checked ${checkedAt}` : 'Not checked yet' }}</p>
      </div>
      <button class="btn btn--icon" title="Refresh system health" aria-label="Refresh system health" :disabled="loading" @click="refresh">
        <span class="material-icons" :class="{ 'system-health__refreshing': loading }" aria-hidden="true">refresh</span>
      </button>
    </header>

    <div v-if="error" class="system-health__error" role="alert">
      {{ error }}<span v-if="components.length"> Showing the last available results.</span>
    </div>

    <div class="system-health__overview" :data-tone="overall.tone">
      <span class="material-icons" aria-hidden="true">{{ overall.icon }}</span>
      <div>
        <strong>{{ overall.label }}</strong>
        <p class="caption dim">{{ healthyCount }} of {{ components.length }} services healthy</p>
      </div>
    </div>

    <dl class="system-health__config">
      <div><dt>Version</dt><dd class="mono">{{ store.system.version || 'Unknown' }}</dd></div>
      <div><dt>Authentication</dt><dd>{{ flag(store.system.auth_enabled, 'Enabled', 'Disabled') }}</dd></div>
      <div><dt>Default model</dt><dd class="mono">{{ store.system.model || 'Not configured' }}</dd></div>
      <div><dt>Model credentials</dt><dd>{{ flag(store.system.model_key_present, 'Available', 'Not detected') }}</dd></div>
    </dl>

    <section class="system-health__section" aria-labelledby="system-services">
      <h3 id="system-services">Services <span class="dim">{{ components.length }}</span></h3>
      <p v-if="!components.length" class="caption dim">{{ loading ? 'Loading service health...' : 'No service health reported.' }}</p>
      <article v-for="component in components" :key="component.name" class="system-service" :aria-label="service(component.name).label">
        <header class="system-service__header">
          <span class="material-icons system-service__icon" aria-hidden="true">{{ service(component.name).icon }}</span>
          <div class="system-service__name">
            <h4>{{ service(component.name).label }}</h4>
            <p class="caption dim">{{ service(component.name).description || component.name }}</p>
          </div>
          <span class="system-health__badge" :data-tone="tone(component)">{{ statusLabel(component) }}</span>
        </header>
        <HealthDetails v-if="component.detail !== undefined && component.detail !== null && component.detail !== ''" :value="component.detail" />
        <p v-else class="caption dim">No diagnostics reported.</p>
      </article>
    </section>

    <section class="system-health__section" aria-labelledby="system-agents">
      <h3 id="system-agents">Agent images <span class="dim">{{ agentSets.length }}</span></h3>
      <p v-if="!agentSets.length" class="caption dim">{{ components.some(component => component.name === 'broker' && tone(component) === 'danger') ? 'Agent image readiness unavailable while the Docker broker is down.' : 'No agent image readiness reported.' }}</p>
      <div v-for="agent in agentSets" :key="agent.name" class="system-health__agent">
        <span class="material-icons dim" aria-hidden="true">smart_toy</span>
        <div><strong>{{ agent.name }}</strong><p class="caption mono dim">{{ agent.image || 'No image reported' }}</p></div>
        <span class="system-health__badge" :data-tone="agent.ready === true ? 'success' : agent.ready === false ? 'warning' : 'muted'">{{ flag(agent.ready, 'Ready', 'Missing') }}</span>
      </div>
    </section>

    <details class="system-health__raw">
      <summary>Raw system response</summary>
      <pre>{{ JSON.stringify(store.system, null, 2) }}</pre>
    </details>
  </section>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue'
import { api } from '../../api'
import { store } from '../../store'
import HealthDetails from './HealthDetails.vue'

const loading = ref(false)
const error = ref('')
const checkedAt = ref('')
const components = computed(() => store.system.components || [])
const agentSets = computed(() => store.system.agent_sets || [])
const healthyCount = computed(() => components.value.filter(component => tone(component) === 'success').length)
const overall = computed(() => {
  if (error.value) return { tone: 'warning', icon: 'cloud_off', label: 'Health check unavailable' }
  if (!components.value.length) return { tone: 'muted', icon: 'monitor_heart', label: 'Awaiting health data' }
  if (healthyCount.value !== components.value.length) return { tone: 'warning', icon: 'warning_amber', label: 'Services need attention' }
  return { tone: 'success', icon: 'check_circle', label: 'All services healthy' }
})

const services = {
  temporal: { label: 'Temporal', icon: 'account_tree', description: 'Workflow orchestration' },
  broker: { label: 'Docker broker', icon: 'dns', description: 'Container runtime, images and workers' },
  agentgateway: { label: 'Agent gateway', icon: 'hub', description: 'Model and tool routing' },
  'workflow-mcp': { label: 'Workflow MCP', icon: 'code', description: 'Workflow authoring and deployment' }
}
const service = name => services[name] || { label: name, icon: 'settings_ethernet' }
const flag = (value, yes, no) => value === true ? yes : value === false ? no : 'Unknown'
function status (component) {
  if (component.status === 'down') return 'down'
  const reported = component.detail?.status
  if (reported && !['ok', 'ready', 'healthy'].includes(reported)) return reported
  return component.status || 'unknown'
}
function tone (component) {
  const value = status(component)
  return ['ok', 'ready', 'healthy'].includes(value) ? 'success' : ['down', 'error', 'failed'].includes(value) ? 'danger' : value === 'unknown' ? 'muted' : 'warning'
}
function statusLabel (component) {
  const value = status(component)
  return value === 'ok' ? 'Healthy' : value.charAt(0).toUpperCase() + value.slice(1)
}
async function refresh () {
  if (loading.value) return
  loading.value = true
  error.value = ''
  try {
    store.system = await api.system()
    checkedAt.value = new Date().toLocaleTimeString()
  } catch (failure) {
    if (failure.status === 401) store.needsToken = true
    error.value = 'Could not refresh system health. Check your connection and try again.'
  } finally {
    loading.value = false
  }
}
onMounted(refresh)
</script>

<style scoped>
.system-health { min-width: 0; letter-spacing: 0; }
.system-health p { margin: 0; }
.system-health__header, .system-service__header { display: flex; align-items: center; gap: 12px; }
.system-health__header { justify-content: space-between; margin-bottom: 20px; }
.system-health .settings__title { letter-spacing: 0; }
.system-health__overview { display: flex; align-items: center; gap: 12px; padding: 16px 0; border-block: 1px solid var(--border); }
.system-health__overview > .material-icons { color: var(--health-color); font-size: 26px; }
.system-health__overview strong { font-size: 15px; }
[data-tone="success"] { --health-color: var(--success); }
[data-tone="warning"] { --health-color: var(--warning); }
[data-tone="danger"] { --health-color: var(--danger); }
[data-tone="muted"] { --health-color: var(--text-muted); }
.system-health__config { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 16px 24px; margin: 20px 0 28px; }
.system-health__config dt { color: var(--text-muted); font-size: 12px; margin-bottom: 4px; }
.system-health__config dd { margin: 0; font-size: 13px; overflow-wrap: anywhere; }
.system-health__section { margin-top: 24px; }
.system-health h3 { display: flex; gap: 8px; margin: 0 0 12px; font-size: 13px; line-height: 1.4; font-weight: 650; letter-spacing: 0; }
.system-service { border: 1px solid var(--border); border-radius: 8px; background: var(--surface-panel); padding: 16px; margin-top: 10px; min-width: 0; }
.system-service__header { margin-bottom: 14px; flex-wrap: wrap; }
.system-service__icon { color: var(--text-muted); font-size: 21px; }
.system-service__name { flex: 1; min-width: 120px; overflow-wrap: anywhere; }
.system-service h4 { margin: 0 0 3px; font-size: 14px; line-height: 1.4; font-weight: 600; letter-spacing: 0; }
.system-health__badge { flex: none; max-width: 100%; overflow-wrap: anywhere; font-size: 11px; font-weight: 600; padding: 3px 8px; border-radius: 4px; color: var(--health-color); background: color-mix(in srgb, var(--health-color) 10%, transparent); }
.system-health__agent { display: grid; grid-template-columns: 20px minmax(0, 1fr) auto; align-items: center; gap: 10px; border-top: 1px solid var(--border); padding: 14px 0; font-size: 13px; }
.system-health__agent > .material-icons { font-size: 20px; }
.system-health__agent strong, .system-health__agent p { overflow-wrap: anywhere; }
.system-health__raw { border-top: 1px solid var(--border); margin-top: 24px; padding-top: 16px; font-size: 12px; color: var(--text-muted); }
.system-health__raw summary { cursor: pointer; width: fit-content; }
.system-health__raw pre { white-space: pre-wrap; overflow-wrap: anywhere; font-size: 11px; color: var(--text); background: var(--surface-panel); padding: 12px; border-radius: 4px; }
.system-health__error { color: var(--danger); font-size: 13px; margin-bottom: 16px; }
.system-health__refreshing { animation: health-spin 1s linear infinite; }
@keyframes health-spin { to { transform: rotate(360deg); } }
@media (prefers-reduced-motion: reduce) { .system-health__refreshing { animation: none; } }
@media (max-width: 420px) {
  .system-health__config { grid-template-columns: minmax(0, 1fr); gap: 14px; }
  .system-service { padding: 12px; }
  .system-service__header { gap: 8px; }
  .system-service__icon { display: none; }
}
</style>
