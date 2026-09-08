<template>
  <h2 class="settings__title">Workspace</h2>
  <p class="settings__intro">Keep it focused. Keep it yours.</p>
  <section v-for="group in WORKSPACE_GROUPS" :key="group" class="setting">
    <h3 class="setting__group-title">{{ group }}</h3>
    <div v-for="field in WORKSPACE_SETTINGS.filter(item => item.group === group)" :id="field.key" :key="field.key" class="preference-row">
      <div class="grow">
        <label :for="`preference-${field.key}`" class="setting__label">{{ field.label }}</label>
        <p v-if="field.description" class="caption muted">{{ field.description }}</p>
      </div>
      <input v-if="field.type === 'boolean'" :id="`preference-${field.key}`" type="checkbox" role="switch"
        :checked="preferences[field.key]" :aria-checked="preferences[field.key]" @change="setPreference(field.key, $event.target.checked)" />
      <select v-else-if="field.type === 'select'" :id="`preference-${field.key}`" class="field preference-row__control"
        :value="preferences[field.key]" @change="selectValue(field, $event.target.value)">
        <option v-for="[value, label] in field.options" :key="value" :value="value">{{ label }}</option>
      </select>
      <div v-else class="row preference-row__control">
        <input :id="`preference-${field.key}`" class="field" type="number" :value="preferences[field.key]"
          :min="field.min" :max="field.max" :step="field.step" @change="numberValue(field, $event)" />
        <span class="caption dim">{{ field.unit }}</span>
      </div>
    </div>
  </section>
  <footer class="settings__footer">
    <span class="caption muted grow" :role="preferenceError ? 'alert' : 'status'">{{ preferenceError || 'Saved automatically on this device' }}</span>
    <button class="btn btn--sm btn--outline" @click="resetWorkspace">Reset workspace</button>
  </footer>
</template>

<script setup>
import { preferences, preferenceError, resetWorkspace, setPreference } from '../../preferences'
import { WORKSPACE_GROUPS, WORKSPACE_SETTINGS } from '../../preferences-schema'

function selectValue (field, value) {
  setPreference(field.key, field.options.find(([option]) => String(option) === value)?.[0])
}
function numberValue (field, event) {
  if (!event.target.reportValidity() || event.target.value === '') return
  setPreference(field.key, Number(event.target.value))
}
</script>

<style scoped>
.preference-row { display: flex; align-items: center; gap: 28px; padding: 18px 0; border-bottom: 1px solid var(--border); }
.preference-row:last-child { border-bottom: 0; }
.preference-row__control { flex: none; width: 210px; }
.preference-row > input[type='checkbox'] { flex: none; margin: 0 4px; cursor: pointer; }
.preference-row__control input { width: 100%; }
@media (max-width: 600px) {
  .preference-row { flex-wrap: wrap; gap: 12px; }
  .preference-row__control { flex-basis: 100%; width: 100%; }
}
</style>
