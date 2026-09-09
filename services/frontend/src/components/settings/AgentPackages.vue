<template>
  <section class="agent-packages" aria-label="Agent extensions">
    <label class="setting__label" :for="pickerId">Extensions</label>
    <p class="caption dim">Choose from the shared library for this agent. Install packages separately in <RouterLink to="/settings/packages">Extension library</RouterLink>.</p>
    <button :id="pickerId" type="button" class="field field--button" :disabled="disabled || busy" aria-label="Select agent extensions">
      <span class="grow">{{ modelValue.length ? `${modelValue.length} selected` : 'No extensions selected' }}</span><span class="material-icons" aria-hidden="true">expand_more</span>
      <PackagePicker :model-value="modelValue" :disabled="disabled || busy" @update:model-value="$emit('update:modelValue', $event)" />
    </button>
    <p v-if="error" role="alert" class="caption field-hint--bad">{{ error }}</p>
    <details v-if="modelValue.length" class="agent-packages__overrides">
      <summary>Agent-specific configuration</summary>
      <p class="caption dim">Optional overrides for this agent only. Save the agent to apply them. Existing chats stay unchanged.</p>
      <article v-for="id in modelValue" :key="id" class="agent-packages__card">
        <strong>{{ title(revisions[id]?.installation_id) }}</strong>
        <PackageConfiguration v-if="revisions[id]" :revision="revisions[id]" :disabled="disabled || busy" @replace="replace(id, $event)" @busy="busy = $event" />
      </article>
    </details>
  </section>
</template>
<script setup>
import { onUnmounted, ref, useId, watch } from 'vue'
import { api } from '../../api'
import PackagePicker from '../PackagePicker.vue'
import PackageConfiguration from './PackageConfiguration.vue'
const props = defineProps({ modelValue: { type: Array, default: () => [] }, disabled: Boolean })
const emit = defineEmits(['update:modelValue', 'busy'])
const pickerId = useId(), revisions = ref({}), installations = ref([]), error = ref(''), busy = ref(false)
let generation = 0
watch(busy, value => emit('busy', value), { flush: 'sync' })
watch(() => [...props.modelValue], async ids => {
  const current = ++generation
  try {
    const data = ids.length ? await api.packageInstallations() : { installations: [] }
    const values = await Promise.all(ids.map(id => revisions.value[id] || api.packageRevision(id)))
    if (current !== generation) return
    installations.value = data.installations
    for (const revision of values) revisions.value[revision.id] = revision
    error.value = ''
  } catch (failure) { if (current === generation) error.value = failure.message }
}, { immediate: true })
function title (id) { const item = installations.value.find(item => item.id === id); return item?.metadata?.resolved || item?.source || 'Selected package' }
function replace (id, revision) { emit('update:modelValue', props.modelValue.map(item => item === id ? revision.id : item)) }
onUnmounted(() => { generation++ })
</script>
<style scoped>
.agent-packages { margin: 24px 0; }
.agent-packages > .field { margin-top: 12px; width: 100%; }
.agent-packages__overrides { margin-top: 16px; font-size: 12px; }
.agent-packages__overrides > p { margin: 12px 0; }
.agent-packages__overrides summary { cursor: pointer; }
.agent-packages__card { margin-top: 12px; padding: 12px; border: 1px solid var(--border); border-radius: var(--radius-sm); overflow-wrap: anywhere; }
.agent-packages__card strong { font-size: 12px; }
</style>
