<template>
  <h2 class="settings__title">Activity</h2>
  <div class="setting">
    <div v-for="(event, index) in store.events" :key="index" class="line line--stacked">
      <div class="row">
        <span class="mono grow truncate">{{ event.kind }}</span>
        <span class="caption dim">{{ new Date(event.at * 1000).toLocaleTimeString() }}</span>
      </div>
      <span class="caption dim mono truncate">{{ summary(event) }}</span>
    </div>
    <p v-if="!store.events.length" class="caption dim">
      Waiting for the system to do something.
    </p>
  </div>
</template>

<script setup>
import { onMounted } from 'vue'
import { actions, store } from '../../store'

function summary (event) {
  const { kind, at, ...rest } = event
  return JSON.stringify(rest).slice(0, 200)
}

onMounted(() => actions.loadEvents())
</script>
