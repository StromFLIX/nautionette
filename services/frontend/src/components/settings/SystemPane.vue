<template>
  <h2 class="settings__title">System</h2>
  <div class="setting">
    <div class="row">
      <div class="setting__label grow">Components</div>
      <button class="btn btn--sm btn--outline" @click="actions.loadSystem()">Refresh</button>
    </div>
    <div
      v-for="component in store.system.components || []" :key="component.name"
      class="line line--stacked"
    >
      <div class="row">
        <span class="dot" :class="component.status === 'ok' ? 'dot--ok' : 'dot--bad'" />
        <span class="grow">{{ component.name }}</span>
        <span class="chip" :class="component.status === 'ok' ? 'chip--success' : 'chip--danger'">
          {{ component.status }}
        </span>
      </div>
      <span class="caption dim mono clamp-2">{{ render(component.detail) }}</span>
    </div>
  </div>
</template>

<script setup>
import { actions, store } from '../../store'

function render (detail) {
  return typeof detail === 'string' ? detail : JSON.stringify(detail)
}
</script>
