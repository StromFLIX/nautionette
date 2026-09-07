<template>
  <div class="composer" :class="[`composer--${variant}`, { 'composer--focus': focused, 'composer--running': running }]">
    <textarea
      ref="input"
      class="composer__input"
      :value="modelValue"
      :placeholder="placeholder"
      :rows="variant === 'welcome' ? 2 : 1"
      :disabled="busy"
      @input="onInput"
      @focus="focused = true"
      @blur="focused = false"
      @keydown.enter.exact.prevent="submit"
    />

    <div class="composer__bar">
      <button class="pick">
        <span class="material-icons pick__icon">smart_toy</span>
        <span class="truncate">{{ agentSet || 'default' }}</span>
        <span class="material-icons pick__caret">expand_more</span>
        <q-menu anchor="top left" self="bottom left" class="pick-menu">
          <div class="pick-menu__label section-label">Agent set</div>
          <button
            v-for="set in agentSets" :key="set.name" class="pick-menu__item"
            @click="$emit('update:agentSet', set.name)"
          >
            <span class="grow truncate">{{ set.name }}</span>
            <span v-if="!set.ready" class="chip chip--warning">building</span>
            <span v-if="set.name === agentSet" class="material-icons pick-menu__check">check</span>
          </button>
        </q-menu>
        <q-tooltip>Agent set: the container image this chat runs in</q-tooltip>
      </button>

      <button class="pick">
        <span class="material-icons pick__icon">memory</span>
        <span class="truncate">{{ shortModel }}</span>
        <span class="material-icons pick__caret">expand_more</span>
        <ModelPicker :model-value="model" @update:model-value="$emit('update:model', $event)" />
        <q-tooltip>Model used for this chat</q-tooltip>
      </button>

      <button class="pick" :class="{ 'pick--quiet': !toolCount }">
        <span class="material-icons pick__icon">handyman</span>
        <span>{{ toolLabel }}</span>
        <span class="material-icons pick__caret">expand_more</span>
        <ToolPicker :model-value="tools" @update:model-value="$emit('update:tools', $event)" />
        <q-tooltip>MCP tools this chat may call</q-tooltip>
      </button>

      <button class="pick" aria-label="Select projects" :class="{ 'pick--quiet': !projectIds.length }">
        <span class="material-icons pick__icon">folder_open</span>
        <span>{{ projectIds.length ? `${projectIds.length} project${projectIds.length === 1 ? '' : 's'}` : 'Projects' }}</span>
        <span class="material-icons pick__caret">expand_more</span>
        <ProjectPicker :model-value="projectIds" @update:model-value="$emit('update:projectIds', $event)" />
        <q-tooltip>Writable projects for the next message</q-tooltip>
      </button>

      <div class="composer__context" :title="meter.title" :aria-label="meter.title">
        <div class="meter">
          <div class="meter__fill" :style="{ width: `${meter.width}%` }" :class="{ 'meter__fill--hot': meter.percent > 80 }" />
        </div>
        <span class="caption dim">{{ meter.label }}</span>
        <q-tooltip>{{ meter.title }}</q-tooltip>
      </div>

      <button
        v-if="running"
        class="composer__stop"
        :disabled="stopping" aria-label="Stop response" @click="$emit('stop')"
      >
        <span class="material-icons" aria-hidden="true">stop</span>
        <q-tooltip>{{ stopping ? 'Stopping response' : 'Stop response' }}</q-tooltip>
      </button>
      <button
        class="composer__send" :class="{ 'composer__send--busy': busy }"
        :aria-label="running ? 'Queue message' : 'Send message'"
        :disabled="busy || !modelValue.trim()" @click="submit"
      >
        <span class="material-icons">{{ busy ? 'more_horiz' : running ? 'playlist_add' : 'arrow_upward' }}</span>
        <q-tooltip>{{ running ? 'Queue message' : 'Send message' }}</q-tooltip>
      </button>
    </div>
  </div>
</template>

<script setup>
import { computed, ref } from 'vue'
import ModelPicker from './ModelPicker.vue'
import ToolPicker from './ToolPicker.vue'
import ProjectPicker from './ProjectPicker.vue'
import { store } from '../store'
import { contextMeter, modelContextWindow } from '../context'

const props = defineProps({
  modelValue: { type: String, default: '' },
  agentSet: { type: String, default: '' },
  model: { type: String, default: '' },
  tools: { type: Array, default: null },
  projectIds: { type: Array, default: () => [] },
  busy: { type: Boolean, default: false },
  running: { type: Boolean, default: false },
  stopping: { type: Boolean, default: false },
  context: { type: Object, default: null },
  variant: { type: String, default: 'docked' },
  placeholder: { type: String, default: 'Message…' }
})

const emit = defineEmits(['update:modelValue', 'update:agentSet', 'update:model', 'update:tools', 'update:projectIds', 'send', 'stop'])

const input = ref(null)
const focused = ref(false)

const agentSets = computed(() => store.catalog.agent_sets || [])
const allTools = computed(() => store.catalog.tools || [])
const toolCount = computed(() => (props.tools === null ? allTools.value.length : props.tools.length))
const toolLabel = computed(() =>
  props.tools === null ? `${allTools.value.length} tools` : `${props.tools.length}/${allTools.value.length} tools`)
const meter = computed(() => contextMeter(props.context, modelContextWindow(store.catalog, props.model)))
const shortModel = computed(() => {
  const id = props.model || store.catalog.default_model || 'model'
  return id.includes('/') ? id.split('/').pop() : id
})

function onInput (event) {
  emit('update:modelValue', event.target.value)
  const el = event.target
  el.style.height = 'auto'
  el.style.height = `${Math.min(220, el.scrollHeight)}px`
}

function submit () {
  if (props.busy || !props.modelValue.trim()) return
  emit('send')
  if (input.value) input.value.style.height = 'auto'
}

defineExpose({ focus: () => input.value?.focus() })
</script>

<style scoped>
.composer {
  min-width: 0;
  border: 1px solid var(--border-strong);
  border-radius: var(--radius-lg);
  background: var(--surface-input);
  transition: border-color var(--transition), box-shadow var(--transition);
}

.composer--focus {
  border-color: var(--accent);
  box-shadow: 0 0 0 3px var(--accent-soft);
}

.composer--welcome {
  box-shadow: var(--shadow-md);
}

.composer__input {
  display: block;
  width: 100%;
  max-height: 220px;
  padding: 12px 14px 6px;
  border: none;
  background: none;
  color: var(--text);
  font: inherit;
  font-size: 14px;
  line-height: 1.55;
  resize: none;
  outline: none;
  overflow-y: hidden;
}

.composer__input::placeholder {
  color: var(--text-dim);
}

.composer__bar {
  display: flex;
  align-items: center;
  gap: 6px;
  min-width: 0;
  padding: 6px 8px 8px 10px;
}

.pick {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  min-width: 0;
  max-width: 190px;
  height: 26px;
  padding: 0 7px;
  border: 1px solid transparent;
  border-radius: var(--radius-sm);
  background: transparent;
  color: var(--text-muted);
  font: inherit;
  font-size: 12px;
  font-weight: 500;
  cursor: pointer;
  transition: background var(--transition), color var(--transition);
}

.pick:hover {
  background: var(--surface-active);
  color: var(--text);
}

.pick--quiet {
  color: var(--text-dim);
}

.pick__icon {
  font-size: 15px;
}

.pick__caret {
  font-size: 15px;
  opacity: 0.6;
}

.composer__context {
  display: flex;
  align-items: center;
  gap: 7px;
  margin-left: auto;
  padding-right: 4px;
}

.meter {
  width: 54px;
  height: 4px;
  border-radius: var(--radius-pill);
  background: var(--surface-active);
  overflow: hidden;
}

.meter__fill {
  height: 100%;
  background: var(--accent);
  transition: width 200ms var(--ease);
}

.meter__fill--hot {
  background: var(--warning);
}

.composer__send,
.composer__stop {
  display: grid;
  place-items: center;
  flex: none;
  width: 32px;
  height: 32px;
  border: none;
  border-radius: 50%;
  background: var(--accent);
  color: var(--accent-text);
  cursor: pointer;
  transition: background var(--transition), opacity var(--transition);
}

.composer__stop {
  background: var(--surface-active);
  color: var(--danger);
}

.composer__send:disabled,
.composer__stop:disabled {
  background: var(--surface-active);
  color: var(--text-dim);
  cursor: default;
}

.composer__send .material-icons,
.composer__stop .material-icons {
  font-size: 18px;
}

@media (max-width: 640px) {
  .composer__context {
    display: none;
  }

  .composer__bar {
    display: grid;
    grid-template-columns: repeat(2, minmax(0, 1fr)) 32px;
    gap: 4px;
    padding-right: 7px;
    padding-left: 7px;
  }

  .pick {
    width: 100%;
    max-width: none;
  }

  .pick:nth-of-type(3) { grid-column: 1; }
  .composer__send { grid-column: 3; grid-row: 1 / 3; align-self: end; }
  .composer__stop { grid-column: 3; grid-row: 1; }
  .composer--running .composer__send { grid-row: 2; }

  .pick > .truncate,
  .pick > span:not(.material-icons) {
    min-width: 0;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
}

@media (max-width: 380px) {
  .composer__input {
    padding-right: 11px;
    padding-left: 11px;
  }

  .composer__bar {
    gap: 2px;
    padding-right: 5px;
    padding-left: 5px;
  }

  .pick {
    justify-content: center;
    gap: 3px;
    padding: 0 4px;
  }

  .pick__caret {
    display: none;
  }
}
</style>

<style>
.pick-menu {
  min-width: 240px;
  max-width: 340px;
}

.pick-menu__label {
  padding: 8px 10px 6px;
}

.pick-menu__item {
  display: flex;
  align-items: center;
  gap: 8px;
  width: 100%;
  padding: 7px 10px;
  border: none;
  border-radius: var(--radius-sm);
  background: none;
  color: var(--text);
  font: inherit;
  font-size: 13px;
  text-align: left;
  cursor: pointer;
}

.pick-menu__item:hover {
  background: var(--surface-hover);
}

.pick-menu__check {
  font-size: 16px;
  color: var(--accent-hover);
}
</style>
