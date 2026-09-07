<template>
  <dl v-if="isObject(value)" class="health-details">
    <div v-for="(entry, key) in value" :key="key" class="health-details__field" :class="{ 'health-details__field--group': isCollection(entry), 'health-details__field--error': key === 'error' && entry }">
      <dt>{{ label(key) }}</dt>
      <dd><HealthDetails :value="entry" /></dd>
    </div>
    <div v-if="!Object.keys(value).length" class="health-details__empty">None reported</div>
  </dl>
  <ul v-else-if="Array.isArray(value) && value.length" class="health-details__list">
    <li v-for="(entry, index) in value" :key="index"><HealthDetails :value="entry" /></li>
  </ul>
  <span v-else class="health-details__value" :class="{ 'health-details__empty': value == null || value === '' || Array.isArray(value) }">{{ display(value) }}</span>
</template>

<script setup>
import { computed } from 'vue'

const props = defineProps({ value: { default: null } })
const value = computed(() => decode(props.value))
function decode (value) {
  if (typeof value !== 'string') return value
  try {
    const parsed = JSON.parse(value)
    return parsed !== null && typeof parsed === 'object' ? parsed : value
  } catch {
    return value
  }
}
const isObject = value => value !== null && typeof value === 'object' && !Array.isArray(value)
function isCollection (value) {
  const decoded = decode(value)
  return decoded !== null && typeof decoded === 'object' && Object.keys(decoded).length > 0
}
const labels = { docker: 'Docker reachable', code: 'HTTP status', image_status: 'Image status', missing_images: 'Missing images', task_queue: 'Task queue' }
function label (key) {
  if (labels[key]) return labels[key]
  const text = key.replace(/[_-]/g, ' ')
  return text.charAt(0).toUpperCase() + text.slice(1)
}
function display (value) {
  if (Array.isArray(value)) return 'None'
  if (value === null || value === undefined || value === '') return 'Not reported'
  if (typeof value === 'boolean') return value ? 'Yes' : 'No'
  return String(value)
}
</script>

<style scoped>
.health-details { margin: 0; font-size: 12px; min-width: 0; }
.health-details__field { display: grid; grid-template-columns: minmax(90px, 30%) minmax(0, 1fr); gap: 12px; padding: 5px 0; }
.health-details__field > dt { color: var(--text-muted); overflow-wrap: anywhere; }
.health-details__field > dd { margin: 0; min-width: 0; }
.health-details__field--error { color: var(--danger); }
.health-details__field--group { display: block; padding-top: 12px; }
.health-details__field--group > dt { font-weight: 600; padding-bottom: 6px; border-bottom: 1px solid var(--border); margin-bottom: 4px; }
.health-details__list { margin: 0; padding-left: 16px; }
.health-details__list > li { padding: 3px 0; }
.health-details__value { font-size: 12px; white-space: pre-wrap; overflow-wrap: anywhere; }
.health-details__empty { color: var(--text-muted); }
@media (max-width: 420px) {
  .health-details__field { grid-template-columns: minmax(72px, 36%) minmax(0, 1fr); gap: 8px; }
  .health-details__field--error { display: block; }
  .health-details__field--error > dt { margin-bottom: 4px; }
}
</style>