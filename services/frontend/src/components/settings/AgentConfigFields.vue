<template>
  <div class="config-fields">
    <section v-for="field in CONFIG_FIELDS" :id="`${prefix}-${field.id}`" :key="field.key" class="setting config-field">
      <div class="config-field__head">
        <label :for="`${prefix}-input-${field.id}`" class="setting__label grow">{{ label(field) }}</label>
        <template v-if="inheritable">
          <button v-if="Object.hasOwn(modelValue, field.key)" class="config-field__inherit" type="button" :disabled="disabled"
            :aria-label="`Use global ${field.label.toLowerCase()}`" @click="inherit(field.key)">
            <span class="material-icons" aria-hidden="true">restart_alt</span>Use global
          </button>
          <span v-else class="caption dim config-field__source">Inherited</span>
        </template>
      </div>
      <button v-if="field.key === 'model'" :id="`${prefix}-input-model`" class="field field--button" type="button" :disabled="disabled">
        <span class="grow truncate">{{ selectedModel?.name || effective.model || 'Choose a model' }}</span>
        <span class="material-icons" aria-hidden="true">expand_more</span>
        <ModelPicker :model-value="effective.model" @update:model-value="update('model', $event)" />
      </button>
      <button v-else-if="field.key === 'agent_set'" :id="`${prefix}-input-agent`" class="field field--button" type="button" :disabled="disabled">
        <span class="grow truncate">{{ effective.agent_set }}</span><span class="material-icons" aria-hidden="true">expand_more</span>
        <q-menu class="pick-menu">
          <button v-for="set in environments" :key="set.name" v-close-popup type="button" class="pick-menu__item" @click="update('agent_set', set.name)">
            <span class="grow truncate">{{ set.name }}</span><span v-if="set.ready === false" class="chip chip--warning">building</span>
            <span v-if="set.name === effective.agent_set" class="material-icons pick-menu__check" aria-hidden="true">check</span>
          </button>
        </q-menu>
      </button>
      <ReasoningPicker v-else-if="field.key === 'reasoning_effort'" :id="`${prefix}-input-reasoning`" class="field field--button config-field__reasoning"
        :aria-label="label(field)" :model-value="effective.reasoning_effort" :capabilities="selectedModel" :busy="disabled"
        @update:model-value="update('reasoning_effort', $event)" />
      <button v-else-if="field.key === 'tools'" :id="`${prefix}-input-tools`" class="field field--button" type="button" :disabled="disabled">
        <span class="grow">{{ toolLabel(effective.tools, store.catalog.tools?.length) }}</span>
        <span class="material-icons" aria-hidden="true">expand_more</span>
        <ToolPicker :model-value="effective.tools" @update:model-value="update('tools', $event)" />
      </button>
      <button v-else-if="field.key === 'project_ids'" :id="`${prefix}-input-projects`" class="field field--button" type="button" :disabled="disabled">
        <span class="grow">{{ projectLabel(effective.project_ids) }}</span>
        <span class="material-icons" aria-hidden="true">expand_more</span>
        <ProjectPicker :model-value="effective.project_ids" @update:model-value="update('project_ids', $event)" />
      </button>
      <p v-if="field.key === 'tools'" class="caption dim">MCP tools. Built-in file and shell tools remain available.</p>
      <p v-if="field.key === 'project_ids'" class="caption dim">Selected projects are writable.</p>
      <p v-if="field.key === 'reasoning_effort' && inheritable && !Object.hasOwn(modelValue, field.key) && effective.model !== defaults.model" class="caption dim">
        Provider default for this model.
      </p>
    </section>
  </div>
</template>

<script setup>
import { computed } from 'vue'
import ModelPicker from '../ModelPicker.vue'
import ReasoningPicker from '../ReasoningPicker.vue'
import ToolPicker from '../ToolPicker.vue'
import ProjectPicker from '../ProjectPicker.vue'
import { store } from '../../store'
import { CONFIG_FIELDS, resolveAgentConfig, toolLabel, projectLabel } from '../../agent-config'

const props = defineProps({
  modelValue: { type: Object, required: true }, defaults: { type: Object, required: true },
  inheritable: { type: Boolean, default: false }, disabled: { type: Boolean, default: false },
  prefix: { type: String, default: 'agent-config' }, global: { type: Boolean, default: false }
})
const emit = defineEmits(['update:modelValue'])
const effective = computed(() => resolveAgentConfig(props.modelValue, props.defaults))
const selectedModel = computed(() => store.catalog.models?.find(model => model.id === effective.value.model) || null)
const environments = computed(() => {
  const sets = store.catalog.agent_sets || []
  return sets.some(set => set.name === effective.value.agent_set) ? sets : [{ name: effective.value.agent_set }, ...sets]
})
function label (field) {
  return props.global ? `Default ${field.key === 'agent_set' ? 'agent set' : field.label.toLowerCase()}` : field.label
}
function update (key, value) {
  const next = { ...props.modelValue, [key]: value }
  if (key === 'model' && value !== effective.value.model) next.reasoning_effort = null
  emit('update:modelValue', next)
}
function inherit (key) {
  const next = { ...props.modelValue }
  delete next[key]
  if (key === 'model' && effective.value.model !== props.defaults.model) delete next.reasoning_effort
  emit('update:modelValue', next)
}
</script>

<style scoped>
.config-fields { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 22px 24px; margin: 24px 0; }
.config-fields .setting { margin: 0; padding: 0; border: 0; min-width: 0; }
.config-field__head { display: flex; align-items: center; gap: 8px; min-height: 26px; margin-bottom: 8px; }
.config-field__head .setting__label { margin: 0; }
.config-field .field { width: 100%; min-height: 42px; }
.config-field .material-icons { font-size: 1.125rem; }
.config-field > p { margin: 8px 0 0; }
.config-field__inherit { display: inline-flex; align-items: center; gap: 2px; flex: none; border: none; background: none; color: var(--accent); font: inherit; font-size: 0.6875rem; padding: 4px 2px; cursor: pointer; }
.config-field__inherit .material-icons { font-size: 0.9375rem; }
.config-field__source { font-size: 0.625rem; }
.config-field__reasoning :deep(.truncate) { flex: 1; text-align: left; }
@media (max-width: 640px) { .config-fields { grid-template-columns: minmax(0, 1fr); gap: 20px; } }
</style>
