<template>
  <button class="pick" type="button" :aria-label="ariaLabel" :disabled="busy || (!efforts.length && !modelValue)">
    <span aria-hidden="true" class="material-icons pick__icon">psychology</span>
    <span class="truncate">{{ modelValue ? label(modelValue) : 'Provider default' }}</span>
    <span aria-hidden="true" class="material-icons pick__caret">expand_more</span>
    <q-menu anchor="top left" self="bottom left" class="pick-menu">
      <div class="pick-menu__label section-label">Reasoning effort</div>
      <div role="menu" aria-label="Reasoning effort levels">
        <button v-for="effort in [null, ...efforts]" :key="effort || 'default'" v-close-popup
          class="pick-menu__item" type="button" role="menuitemradio"
          :aria-checked="modelValue === effort" @click="$emit('update:modelValue', effort)">
          <span class="grow">{{ effort ? label(effort) : 'Provider default' }}</span>
          <span v-if="modelValue === effort" aria-hidden="true" class="material-icons pick-menu__check">check</span>
        </button>
      </div>
    </q-menu>
    <q-tooltip>{{ efforts.length ? 'Reasoning effort for the next message. Higher levels may take longer and cost more. Default leaves the choice to the provider.' : 'No selectable effort levels advertised for this model/API route.' }}</q-tooltip>
  </button>
</template>

<script setup>
import { computed } from 'vue'
const props = defineProps({
  modelValue: { type: String, default: null },
  capabilities: { type: Object, default: null },
  busy: { type: Boolean, default: false },
  ariaLabel: { type: String, default: 'Reasoning effort' }
})
defineEmits(['update:modelValue'])
const efforts = computed(() => props.capabilities?.reasoning_efforts || [])
const label = (effort) => ({ none: 'None', minimal: 'Minimal', low: 'Low', medium: 'Medium', high: 'High', xhigh: 'Extra high', max: 'Max' })[effort] || effort
</script>

<style scoped>
.pick__icon, .pick__caret { font-size: 0.9375rem; }
.pick__caret { opacity: 0.6; }
@media (max-width: 380px) { .pick__caret { display: none; } }
</style>
