<template>
  <div class="composer" :class="[`composer--${variant}`, { 'composer--focus': focused }]">
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
        <q-menu anchor="top left" self="bottom left" class="pick-menu">
          <div class="pick-menu__label section-label">Model</div>
          <div class="pick-menu__scroll scroll-y">
            <button
              v-for="option in models" :key="option.id" class="pick-menu__item"
              @click="$emit('update:model', option.id)"
            >
              <span class="grow truncate">{{ option.id }}</span>
              <span v-if="option.id === model" class="material-icons pick-menu__check">check</span>
            </button>
          </div>
        </q-menu>
        <q-tooltip>Model used for this chat</q-tooltip>
      </button>

      <button class="pick" :class="{ 'pick--quiet': !tools.length }">
        <span class="material-icons pick__icon">handyman</span>
        <span>{{ tools.length }} tools</span>
        <q-menu anchor="top left" self="bottom left" class="pick-menu">
          <div class="pick-menu__label section-label">MCP tools available</div>
          <div v-if="!tools.length" class="pick-menu__note caption">
            No MCP server answered. Check the gateway in Settings.
          </div>
          <div v-else class="pick-menu__scroll scroll-y">
            <div v-for="tool in tools" :key="tool.name" class="pick-menu__tool">
              <span class="mono">{{ tool.name }}</span>
              <span v-if="tool.description" class="caption dim clamp-2">{{ tool.description }}</span>
            </div>
          </div>
          <div class="pick-menu__foot">
            <button class="btn btn--sm" @click="actions.openSettings('mcp')">Manage MCP servers</button>
          </div>
        </q-menu>
      </button>

      <div class="composer__context">
        <div class="meter" :title="`${contextUsed.toLocaleString()} of ${contextWindow.toLocaleString()} characters of history`">
          <div class="meter__fill" :style="{ width: `${contextPercent}%` }" :class="{ 'meter__fill--hot': contextPercent > 80 }" />
        </div>
        <span class="caption dim">{{ contextPercent }}% context</span>
      </div>

      <button
        class="composer__send" :class="{ 'composer__send--busy': busy }"
        :disabled="busy || !modelValue.trim()" @click="submit"
      >
        <span class="material-icons">{{ busy ? 'more_horiz' : 'arrow_upward' }}</span>
      </button>
    </div>
  </div>
</template>

<script setup>
import { computed, ref } from 'vue'
import { actions, store } from '../store'

const props = defineProps({
  modelValue: { type: String, default: '' },
  agentSet: { type: String, default: '' },
  model: { type: String, default: '' },
  busy: { type: Boolean, default: false },
  contextUsed: { type: Number, default: 0 },
  variant: { type: String, default: 'docked' },
  placeholder: { type: String, default: 'Ask anything. Say “every morning at 8” to make it recur.' }
})

const emit = defineEmits(['update:modelValue', 'update:agentSet', 'update:model', 'send'])

const input = ref(null)
const focused = ref(false)

const agentSets = computed(() => store.catalog.agent_sets || [])
const models = computed(() => store.catalog.models || [])
const tools = computed(() => store.catalog.tools || [])
const contextWindow = computed(() => store.catalog.context_window || 24000)
const contextPercent = computed(() =>
  Math.min(100, Math.round((props.contextUsed / contextWindow.value) * 100)))
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
  overflow-y: auto;
}

.composer__input::placeholder {
  color: var(--text-dim);
}

.composer__bar {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 6px 8px 8px 10px;
}

.pick {
  display: inline-flex;
  align-items: center;
  gap: 5px;
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

.composer__send {
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

.composer__send:disabled {
  background: var(--surface-active);
  color: var(--text-dim);
  cursor: default;
}

.composer__send .material-icons {
  font-size: 18px;
}

@media (max-width: 640px) {
  .composer__context {
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

.pick-menu__scroll {
  max-height: 300px;
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

.pick-menu__tool {
  display: flex;
  flex-direction: column;
  gap: 1px;
  padding: 6px 10px;
  border-radius: var(--radius-sm);
}

.pick-menu__tool:hover {
  background: var(--surface-hover);
}

.pick-menu__note {
  padding: 4px 10px 10px;
  color: var(--text-dim);
}

.pick-menu__foot {
  border-top: 1px solid var(--border);
  margin-top: 4px;
  padding: 6px 6px 2px;
}
</style>
