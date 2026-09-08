<template>
  <div v-if="staging" class="environment-banner" role="status">
    <strong>STAGING</strong> — independent copy of production data
  </div>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue'
import { api, endpoint } from '../api'
import { store } from '../store'

const environment = ref('')
const staging = computed(() => {
  // The hostname fallback also labels the login screen before API access works.
  const hostname = new URL(endpoint('/'), window.location.href).hostname
  return store.system.environment === 'staging' || environment.value === 'staging' ||
    /(^|\.)(stage|staging)(\.|$)/i.test(hostname)
})

onMounted(async () => {
  try { environment.value = (await api.health()).environment || '' } catch { /* offline */ }
})
</script>

<style scoped>
.environment-banner {
  flex: 0 0 auto;
  padding: 8px 12px;
  background: var(--warning);
  color: var(--warning-text);
  text-align: center;
  font-size: 13px;
  line-height: 1.4;
  border-bottom: 2px solid var(--warning-text);
}
</style>
