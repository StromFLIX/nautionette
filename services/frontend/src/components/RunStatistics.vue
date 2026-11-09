<template>
  <section class="run-statistics" aria-label="Run statistics">
    <template v-if="statistics?.available">
      <p v-if="statistics.coverage === 'partial'" class="caption dim" role="status">Only part of this run was recorded. Counts below may be incomplete.</p>
      <div class="statistics-cards">
        <div v-for="metric in metrics" :key="metric.label" class="statistics-card">
          <div class="section-label">{{ metric.label }}</div>
          <strong>{{ number(metric.value) }}</strong>
        </div>
      </div>
      <p class="caption dim">{{ number(statistics.model_requests) }} recorded model responses · {{ number(statistics.direct_mcp_calls) }} direct MCP calls. Counts include retry attempts and failed calls.</p>

      <h3>Token usage <span v-if="statistics.tokens && !statistics.usage_complete" class="chip chip--warning">Partial</span></h3>
      <dl v-if="statistics.tokens" class="token-counts">
        <div v-for="[key, label] in tokenLabels" :key="key"><dt>{{ label }}</dt><dd>{{ number(statistics.tokens[key]) }}</dd></div>
      </dl>
      <p v-else class="caption dim">{{ statistics.agent_calls ? 'Token usage was not reported by the provider or agent image.' : 'No agent token usage recorded.' }}</p>
      <p v-if="statistics.agent_calls && !statistics.usage_complete" class="caption dim" role="status">Usage is incomplete. Reported tokens are a lower bound, not an estimate; older agent images only report the final response.</p>
      <p class="caption dim">Totals sum all reported model responses, including tool-loop turns. Input excludes cache reads/writes; those are listed separately. Output includes reasoning tokens when reported.</p>

      <h3>Agents &amp; models</h3>
      <div v-if="statistics.agents.length" class="statistics-table-wrap">
        <table class="statistics-table">
          <thead><tr><th>Agent set / model</th><th>Calls</th><th>Failed</th><th>Active / interrupted</th></tr></thead>
          <tbody><tr v-for="agent in statistics.agents" :key="`${agent.agent_set}:${agent.model}`">
            <th scope="row"><span>{{ agent.agent_set }}</span><small>{{ agent.model || 'Unknown model' }}</small></th>
            <td>{{ number(agent.calls) }}</td><td>{{ number(agent.failed || 0) }}</td><td>{{ number(agent.running || 0) }} / {{ number(agent.interrupted || 0) }}</td>
          </tr></tbody>
        </table>
      </div>
      <p v-else class="caption dim">No agent calls recorded.</p>

      <h3>Tools</h3>
      <div v-if="statistics.tools.length" class="statistics-table-wrap">
        <table class="statistics-table">
          <thead><tr><th>Tool / source</th><th>Calls</th><th>Failed</th><th>Active / interrupted</th></tr></thead>
          <tbody><tr v-for="tool in statistics.tools" :key="`${tool.source}:${tool.name}`">
            <th scope="row"><span class="mono">{{ tool.name }}</span><small>{{ sources[tool.source] || 'Unknown source' }}</small></th>
            <td>{{ number(tool.calls) }}</td><td>{{ number(tool.failed || 0) }}</td><td>{{ number(tool.running || 0) }} / {{ number(tool.interrupted || 0) }}</td>
          </tr></tbody>
        </table>
      </div>
      <p v-else class="caption dim">No tool calls recorded.</p>
      <p class="caption dim statistics-scope">Counts cover workflow agent activities, their observed tools, and direct MCP calls. Extension-managed child agents are not included. Tool arguments, results, and credentials are not stored in these statistics.</p>
    </template>
    <p v-else class="caption dim" role="status">Statistics were not recorded for this run. New runs will collect agent, token, and tool usage.</p>
  </section>
</template>

<script setup>
import { computed } from 'vue'

const props = defineProps({ statistics: { type: Object, default: null } })
const number = value => value == null ? '—' : value.toLocaleString()
const metrics = computed(() => [
  { label: 'Agent calls', value: props.statistics?.agent_calls },
  { label: 'Tool calls', value: props.statistics?.tool_calls },
  { label: 'MCP calls', value: props.statistics?.mcp_calls },
  { label: 'Reported tokens', value: props.statistics?.tokens?.total },
])
const tokenLabels = [['input', 'Input (uncached)'], ['output', 'Output'], ['cacheRead', 'Cache read'], ['cacheWrite', 'Cache write']]
const sources = { workflow: 'MCP · direct workflow call', agent_mcp: 'MCP · agent call', local: 'Local agent tool', extension: 'Extension / unclassified agent tool' }
</script>

<style scoped>
.run-statistics { max-width: 860px; }
.run-statistics .section-label { font-size: 0.8125rem; text-transform: none; letter-spacing: 0; color: var(--text-muted); }
.statistics-cards { display: grid; grid-template-columns: repeat(auto-fit, minmax(130px, 1fr)); gap: 10px; }
.statistics-card { border: 1px solid var(--border); border-radius: var(--radius-md); background: var(--surface-panel); padding: 18px; }
.statistics-card strong { display: block; font-size: 1.625rem; font-weight: 600; letter-spacing: -0.035em; margin-top: 10px; font-variant-numeric: tabular-nums; }
h3 { font-size: 1rem; font-weight: 600; margin: 32px 0 16px; padding-top: 24px; border-top: 1px solid var(--border); letter-spacing: -0.02em; }
.caption { line-height: 1.6; }
.token-counts { display: grid; grid-template-columns: repeat(auto-fit, minmax(130px, 1fr)); gap: 12px; font-size: 0.8125rem; }
.token-counts dt { color: var(--text-muted); }
.token-counts dd { margin: 4px 0 0; font-variant-numeric: tabular-nums; }
.statistics-table-wrap { overflow-x: auto; overscroll-behavior-inline: contain; border: 1px solid var(--border); border-radius: var(--radius-sm); }
.statistics-table { width: 100%; border-collapse: collapse; font-size: 0.8125rem; }
.statistics-table th, .statistics-table td { text-align: right; padding: 13px 12px; border-bottom: 1px solid var(--border); font-variant-numeric: tabular-nums; }
.statistics-table th:first-child { text-align: left; width: 55%; min-width: 110px; overflow-wrap: anywhere; word-break: break-word; }
.statistics-table thead th { font-weight: 500; color: var(--text-muted); background: var(--surface-panel); }
.statistics-table tbody tr:last-child > * { border-bottom: 0; }
.statistics-table tbody th { font-weight: 500; }
.statistics-table small { display: block; color: var(--text-muted); margin-top: 4px; font-weight: 400; }
.statistics-scope { margin-top: 24px; }
</style>
