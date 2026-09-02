<template>
  <label class="integration-field">
    <span class="caption">{{ field.label }}{{ field.optional ? ' (optional)' : '' }}</span>
    <input
      class="field mono" :class="{ 'field--bad': error }"
      :type="field.kind === 'secret' ? 'password' : 'text'"
      :autocomplete="field.kind === 'secret' ? 'new-password' : 'off'"
      :placeholder="secretPlaceholder(field, credential)"
      :disabled="disabled"
      :value="modelValue"
      @input="$emit('update:modelValue', $event.target.value)"
    />
    <span class="caption" :class="error ? 'field-hint--bad' : 'dim'">
      {{ error || field.help }}
    </span>
  </label>
</template>

<script setup>
import { computed } from 'vue'
import { fieldError, secretPlaceholder } from './fields'

const props = defineProps({
  field: { type: Object, required: true },
  modelValue: { type: String, default: '' },
  credential: { type: Object, default: null },
  disabled: Boolean
})
defineEmits(['update:modelValue'])

const error = computed(() => fieldError(props.field, { [props.field.key]: props.modelValue }))
</script>
