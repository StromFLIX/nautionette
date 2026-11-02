<template>
  <dl class="app-resources" aria-label="App resources">
    <div><dt>Memory</dt><dd>{{ appBytes(runtime?.memory_bytes) }}<small v-if="runtime?.memory_limit_bytes"> / {{ appBytes(runtime.memory_limit_bytes) }}</small></dd><meter v-if="runtime?.memory_limit_bytes" :value="runtime.memory_bytes || 0" :max="runtime.memory_limit_bytes" aria-label="Memory usage" /></div>
    <div><dt>CPU <small v-if="runtime?.cpu_limit">· {{ runtime.cpu_limit }} core limit</small></dt><dd>{{ runtime?.cpu_percent == null ? '—' : `${runtime.cpu_percent.toFixed(1)}%` }}</dd><small v-if="runtime?.pids != null">{{ runtime.pids }} / {{ runtime.pid_limit || 32 }} processes</small></div>
    <div><dt>SQLite data</dt><dd>{{ appBytes(runtime?.data_bytes) }}<small v-if="runtime?.persistent_limit_bytes"> / {{ appBytes(runtime.persistent_limit_bytes) }}</small></dd><meter v-if="runtime?.persistent_limit_bytes" :value="runtime.data_bytes || 0" :max="runtime.persistent_limit_bytes" aria-label="Data usage" /></div>
    <div><dt>Runtime image</dt><dd>{{ appBytes(runtime?.image_bytes) }}</dd><small v-if="runtime?.status === 'frontend-only'">No container needed</small><small v-else>Shared compiler excluded</small></div>
  </dl>
</template>

<script setup>
import { appBytes } from '../miniapps'
defineProps({ runtime: { type: Object, default: null } })
</script>

<style scoped>
.app-resources { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 12px; margin: 0; }
.app-resources > div { min-width: 0; padding: 14px; border: 1px solid var(--border); border-radius: var(--radius-sm); background: var(--surface-app); }
.app-resources dt { color: var(--text-muted); font-size: .6875rem; margin-bottom: 8px; }
.app-resources dd { margin: 0; font-size: 1rem; font-weight: 600; overflow-wrap: anywhere; }
.app-resources small { color: var(--text-dim); font-size: .625rem; font-weight: 400; }
.app-resources meter { display: block; width: 100%; height: 6px; margin-top: 8px; accent-color: var(--accent); }
@media (max-width: 1100px) { .app-resources { grid-template-columns: repeat(2, minmax(0, 1fr)); } }
@media (max-width: 380px) { .app-resources { gap: 6px; } .app-resources > div { padding: 10px; } }
</style>
