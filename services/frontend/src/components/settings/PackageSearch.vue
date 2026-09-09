<template>
  <section aria-label="Available Pi packages" class="package-search">
    <h3>Available Pi packages</h3>
    <p class="caption dim">Public npm catalog · third-party code, not a compatibility or security review.</p>
    <p v-if="loading" role="status" class="caption">Searching npm…</p>
    <p v-if="error" role="alert" class="caption field-hint--bad">{{ error }} <button type="button" class="btn btn--sm" @click="search(0)">Retry</button></p>
    <p v-if="!loading && !error && !items.length" class="caption dim">No matching packages. You can also install by npm name or public GitHub URL.</p>
    <article v-for="item in items" :key="item.name" class="package-search__item">
      <div class="grow"><strong>{{ item.name }}</strong> <span class="caption dim">{{ item.version }}</span><p class="caption dim">{{ item.description }}</p></div>
      <button type="button" class="btn btn--sm" @click="$emit('select', `npm:${item.name}@${item.version}`)">Choose</button>
    </article>
    <button v-if="nextOffset !== null" type="button" class="btn btn--sm" :disabled="loading" @click="search(nextOffset)">More packages</button>
  </section>
</template>
<script setup>
import { onUnmounted, ref, watch } from 'vue'
import { api } from '../../api'
const props = defineProps({ query: { type: String, default: '' } })
defineEmits(['select'])
const items = ref([]), loading = ref(false), error = ref(''), nextOffset = ref(null)
let controller, timer, generation = 0
async function search (offset = 0) {
  controller?.abort()
  controller = new AbortController()
  const current = ++generation
  loading.value = true; error.value = ''
  try {
    const data = await api.searchPackages(props.query, offset, controller.signal)
    if (current !== generation) return
    if (!Array.isArray(data.packages)) throw new Error('Package search returned an invalid response; retry after upgrading the backend.')
    items.value = offset ? [...items.value, ...data.packages] : data.packages
    nextOffset.value = data.next_offset
  } catch (failure) { if (current === generation && failure.name !== 'AbortError') error.value = failure.message }
  finally { if (current === generation) loading.value = false }
}
watch(() => props.query, () => {
  clearTimeout(timer); controller?.abort(); generation++; items.value = []; nextOffset.value = null
  timer = setTimeout(() => search(), 300)
}, { immediate: true })
onUnmounted(() => { clearTimeout(timer); controller?.abort(); generation++ })
</script>
<style scoped>
.package-search { margin-top: 20px; }
.package-search h3 { font-size: 14px; }
.package-search__item { display: flex; align-items: center; gap: 12px; border-bottom: 1px solid var(--border); padding: 12px 0; overflow-wrap: anywhere; }
.package-search__item strong { font-size: 13px; }
.package-search__item .grow { min-width: 0; }
.package-search__item p { margin: 5px 0; }
</style>
