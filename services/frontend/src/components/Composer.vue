<template>
  <div class="composer" :class="[`composer--${variant}`, { 'composer--focus': focused, 'composer--running': running }]"
    @dragover.prevent @drop.prevent="drop" @paste="paste">
    <div class="composer__attachments">
      <div v-for="image in attachments" :key="image.id" class="composer__attachment">
        <ChatImage :image="image" />
        <button class="btn btn--icon" type="button" :disabled="busy" :aria-label="`Remove ${image.name}`" @click="removeImage(image)">
          <span class="material-icons">close</span>
        </button>
      </div>
    </div>
    <p v-if="imageError" class="composer__notice caption" role="alert">{{ imageError }}</p>
    <p v-if="imageSupport !== true" class="composer__notice caption" role="status">
      {{ imageNotice }}
    </p>
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

    <div class="composer__image-picker">
      <input ref="fileInput" type="file" :accept="IMAGE_TYPES.join(',')" :disabled="busy || imageSupport === false" multiple hidden @change="chooseFiles" />
      <button class="pick" type="button" :disabled="busy || imageSupport === false" :title="imageNotice" aria-label="Attach images" @click="fileInput?.click()">
        <span class="material-icons pick__icon">add_photo_alternate</span>Attach images
        <q-tooltip>{{ imageSupport === true ? 'Paste, drop or select images · up to 4, 5 MiB each' : imageNotice }}</q-tooltip>
      </button>
    </div>
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

      <ReasoningPicker :model-value="reasoningEffort" :capabilities="selectedModel" :busy="busy"
        @update:model-value="$emit('update:reasoningEffort', $event)" />

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
        :disabled="busy || (!modelValue.trim() && !attachments.length) || (attachments.length > 0 && imageSupport === false)" @click="submit"
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
import ReasoningPicker from './ReasoningPicker.vue'
import ChatImage from './ChatImage.vue'
import { addImages, IMAGE_TYPES } from '../attachments'
import { api } from '../api'
import ToolPicker from './ToolPicker.vue'
import ProjectPicker from './ProjectPicker.vue'
import { store } from '../store'
import { contextMeter, modelContextWindow } from '../context'

const props = defineProps({
  modelValue: { type: String, default: '' },
  attachments: { type: Array, default: () => [] },
  agentSet: { type: String, default: '' },
  model: { type: String, default: '' },
  reasoningEffort: { type: String, default: null },
  tools: { type: Array, default: null },
  projectIds: { type: Array, default: () => [] },
  busy: { type: Boolean, default: false },
  running: { type: Boolean, default: false },
  stopping: { type: Boolean, default: false },
  context: { type: Object, default: null },
  variant: { type: String, default: 'docked' },
  placeholder: { type: String, default: 'Message…' }
})

const emit = defineEmits(['update:attachments', 'update:modelValue', 'update:agentSet', 'update:model', 'update:reasoningEffort', 'update:tools', 'update:projectIds', 'send', 'stop'])

const input = ref(null)
const focused = ref(false)
const fileInput = ref(null)
const imageError = ref('')
const selectedModel = computed(() => store.catalog.models?.find((m) => m.id === (props.model || store.catalog.default_model)))
const imageSupport = computed(() => selectedModel.value?.supports_images)
const imageNotice = computed(() => {
  if (imageSupport.value === true) return selectedModel.value?.image_support_reason || 'Image input supported.'
  if (imageSupport.value === false) return `${selectedModel.value?.image_support_reason || 'Image input is unavailable for this model/API route.'} Choose another model${props.attachments.length ? ' or remove the images' : ''}.`
  return 'Image support unverified for this model/API route. Images may not be understood.'
})

function attach (files) {
  if (props.busy || imageSupport.value === false) return
  try {
    emit('update:attachments', addImages(props.attachments, files))
    imageError.value = ''
  } catch (error) { imageError.value = error.message }
}
function chooseFiles (event) {
  attach(event.target.files)
  event.target.value = ''
}
function drop (event) { attach(event.dataTransfer.files) }
function paste (event) {
  const files = Array.from(event.clipboardData?.items || []).filter((item) => item.kind === 'file').map((item) => item.getAsFile()).filter(Boolean)
  if (!files.length) return // Leave ordinary text paste untouched.
  event.preventDefault()
  attach(files)
}
function removeImage (image) {
  emit('update:attachments', props.attachments.filter((item) => item.id !== image.id))
  imageError.value = ''
  if (image.uploaded) api.discardImage(image.uploadedChat, image.uploaded.id).catch(() => {})
}

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
  if (props.busy || (!props.modelValue.trim() && !props.attachments.length) ||
      (props.attachments.length && imageSupport.value === false)) return
  emit('send')
  if (input.value) input.value.style.height = 'auto'
}

defineExpose({ focus: () => input.value?.focus() })
</script>

<style scoped>
.composer__attachments { display: flex; flex-wrap: wrap; gap: 10px; padding: 0 12px; }
.composer__attachment { position: relative; padding-top: 12px; max-width: 150px; }
.composer__attachment > button { position: absolute; right: 0; top: 8px; background: var(--surface-input); }
.composer__notice { margin: 8px 12px; color: var(--warning); }
.composer__image-picker { padding: 0 8px; }
.composer {
  position: relative;
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

.composer--running {
  border-color: var(--accent);
  box-shadow: 0 0 0 1px var(--accent-soft), 0 0 20px var(--accent-soft);
}

.composer--running.composer--focus {
  box-shadow: 0 0 0 3px var(--accent-soft), 0 0 24px var(--accent-soft);
}

/* Mask the light to the border so it never covers the input or intercepts clicks. */
.composer--running::before {
  content: '';
  position: absolute;
  inset: -1px;
  border-radius: inherit;
  padding: 2px;
  pointer-events: none;
  background: conic-gradient(
    from var(--composer-orbit-angle),
    transparent 0deg 240deg,
    var(--accent) 285deg,
    var(--accent-text) 315deg,
    var(--accent-hover) 330deg,
    transparent 360deg
  );
  -webkit-mask: linear-gradient(#fff 0 0) content-box, linear-gradient(#fff 0 0);
  -webkit-mask-composite: xor;
  mask: linear-gradient(#fff 0 0) content-box, linear-gradient(#fff 0 0);
  mask-composite: exclude;
  animation: composer-light-orbit 4s linear infinite;
}

@keyframes composer-light-orbit {
  to { --composer-orbit-angle: 360deg; }
}

@media (prefers-reduced-motion: reduce) {
  .composer--running::before {
    animation: none;
  }
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

.pick:disabled { opacity: 0.5; cursor: not-allowed; }

.pick:hover:not(:disabled) {
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
/* Registered globally so the angle interpolates smoothly instead of jumping. */
@property --composer-orbit-angle {
  syntax: '<angle>';
  initial-value: 0deg;
  inherits: false;
}

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
