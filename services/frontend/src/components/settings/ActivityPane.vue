<template>
  <h2 class="settings__title">Activity</h2>
  <p class="settings__intro">A live record of your workspace.</p>
  <section id="activity-log" class="setting">
    <input v-model="query" class="field" type="search" placeholder="Filter events…" aria-label="Filter activity" />
    <details v-for="(event, index) in events" :key="`${event.at}-${event.kind}-${index}`" class="settings-disclosure activity-event">
      <summary>
        <span class="grow truncate">{{ label(event.kind) }}</span>
        <time class="caption dim" :datetime="new Date(event.at * 1000).toISOString()">{{ new Date(event.at * 1000).toLocaleTimeString() }}</time>
      </summary>
      <pre>{{ JSON.stringify(event, null, 2) }}</pre>
    </details>
    <p v-if="!events.length" class="caption dim activity-empty">{{ query ? 'No matching events.' : 'No recent activity.' }}</p>
  </section>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue'
import { actions, store } from '../../store'
const query = ref('')
const events = computed(() => store.events.filter(event => JSON.stringify(event).toLowerCase().includes(query.value.trim().toLowerCase())))
const label = kind => String(kind || 'Event').replaceAll(/[._]/g, ' ').replace(/^./, letter => letter.toUpperCase())
onMounted(() => actions.loadEvents())
</script>

<style scoped>
.activity-event { margin-top: 10px; }
.activity-event pre { margin: 0; padding: 12px 16px; border-top: 1px solid var(--border); white-space: pre-wrap; overflow-wrap: anywhere; color: var(--text-muted); }
.activity-empty { padding: 24px 0; }
</style>
