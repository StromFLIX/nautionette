<template>
  <div class="composer" :class="[`composer--${variant}`, { 'composer--focus': focused, 'composer--running': running }]"
    @dragover.prevent @drop.prevent="drop" @paste="paste">
    <div v-if="attachments.length" class="composer__attachments">
      <div v-for="image in attachments" :key="image.id" class="composer__attachment">
        <ChatImage :image="image" />
        <button class="btn btn--icon" type="button" :disabled="busy" :aria-label="`Remove ${image.name}`" @click="removeImage(image)">
          <span class="material-icons" aria-hidden="true">close</span>
        </button>
      </div>
    </div>
    <p v-if="!configurationReady" class="composer__notice caption" role="status">Loading chat defaults…</p>
    <p v-if="imageError" class="composer__notice caption" role="alert">{{ imageError }}</p>
    <p v-if="imageSupport !== true && (attachments.length || configurationOpen)" class="composer__notice caption" role="status">{{ imageNotice }}</p>
    <div v-if="commandSuggestions.length" class="composer__commands" aria-label="Pi command suggestions">
      <button v-for="command in commandSuggestions" :key="command.name" class="pick-menu__item" type="button" :disabled="busy" @click="chooseCommand(command.name)">
        <strong>/{{ command.name }}</strong><span class="caption dim truncate">{{ command.description }}</span>
      </button>
    </div>
    <textarea ref="input" class="composer__input" :value="modelValue" :placeholder="placeholder" aria-label="Message"
      :rows="variant === 'welcome' ? 3 : 1" :disabled="busy" @input="onInput"
      @focus="focused = true" @blur="focused = false" @keydown="onKeydown" />

    <input ref="fileInput" type="file" :accept="IMAGE_TYPES.join(',')" :disabled="busy || imageSupport === false" multiple hidden @change="chooseFiles" />
    <div class="composer__bar">
      <button class="pick pick--icon" type="button" :disabled="busy || imageSupport === false" :title="imageNotice" aria-label="Attach images" @click="fileInput?.click()">
        <span class="material-icons" aria-hidden="true">add_photo_alternate</span>
        <q-tooltip>{{ imageSupport === true ? 'Paste, drop or attach images · up to 4, 5 MiB each' : imageNotice }}</q-tooltip>
      </button>
      <button class="pick composer__model" type="button" :disabled="busy" aria-label="Select model">
        <span class="material-icons pick__icon" aria-hidden="true">memory</span>
        <span class="truncate">{{ shortModel }}</span>
        <span class="material-icons pick__caret" aria-hidden="true">expand_more</span>
        <ModelPicker :model-value="model" @update:model-value="$emit('update:model', $event)" />
        <q-tooltip>Model for this chat</q-tooltip>
      </button>
      <span class="grow" />
      <div class="composer__context" :title="meter.title" :aria-label="meter.title" role="img" tabindex="0">
        <div class="meter"><div class="meter__fill" :style="{ width: `${meter.width}%` }" :class="{ 'meter__fill--hot': meter.percent > 80 }" /></div>
        <span class="sr-only">{{ meter.label }}</span>
        <q-tooltip>{{ meter.title }}</q-tooltip>
      </div>
      <button class="pick pick--icon composer__config-toggle" :class="{ 'pick--active': configurationOpen }" type="button"
        aria-label="Chat configuration" :aria-expanded="configurationOpen" :aria-controls="configurationId"
        @click="configurationOpen = !configurationOpen">
        <span class="material-icons" aria-hidden="true">tune</span>
        <span v-if="customConfiguration && !configurationOpen" class="composer__configured" aria-label="Custom configuration" />
        <q-tooltip>Agent, reasoning, tools, projects & extensions{{ customConfiguration ? ' · customized' : '' }}</q-tooltip>
      </button>
      <button v-if="running" class="composer__stop" :disabled="stopping" aria-label="Stop response" @click="$emit('stop')">
        <span class="material-icons" aria-hidden="true">stop</span><q-tooltip>{{ stopping ? 'Stopping response' : 'Stop response' }}</q-tooltip>
      </button>
      <button class="composer__send" :class="{ 'composer__send--busy': busy }" :aria-label="running ? 'Queue message' : 'Send message'"
        :disabled="busy || !configurationReady || (!modelValue.trim() && !attachments.length) || (attachments.length > 0 && imageSupport === false)" @click="submit">
        <span class="material-icons" aria-hidden="true">{{ busy ? 'more_horiz' : running ? 'playlist_add' : 'arrow_upward' }}</span>
        <q-tooltip>{{ running ? 'Queue message' : 'Send message' }} · {{ preferences.sendShortcut === 'enter' ? 'Enter' : '⌘ / Ctrl + Enter' }}</q-tooltip>
      </button>
    </div>

    <div v-show="configurationOpen" :id="configurationId" class="composer__configuration scroll-y" aria-label="Chat configuration controls">
      <div class="composer__profile">
        <button class="pick composer__profile-picker" type="button" aria-label="Select agent" :disabled="busy">
          <span class="material-icons pick__icon" aria-hidden="true">smart_toy</span><span class="truncate">{{ profileLabel }}</span>
          <span class="material-icons pick__caret" aria-hidden="true">expand_more</span>
          <AgentPicker :model-value="agentId" @update:model-value="$emit('update:agentId', $event)" />
        </button>
        <span v-if="customConfiguration" class="caption dim">{{ profileDefaults ? 'Customized' : 'Agent removed' }}</span>
        <button v-if="customConfiguration && profileDefaults" class="pick pick--icon" type="button" aria-label="Reapply agent defaults" :disabled="busy" @click="$emit('update:agentId', agentId)">
          <span class="material-icons" aria-hidden="true">restart_alt</span><q-tooltip>Reapply this agent's current settings</q-tooltip>
        </button>
      </div>
      <div class="composer__options">
        <div class="composer__option">
          <span class="composer__option-label">Environment</span>
          <button class="pick" type="button" aria-label="Select agent set" :disabled="busy">
            <span class="material-icons pick__icon" aria-hidden="true">smart_toy</span>
            <span class="truncate">{{ agentSet || 'default' }}</span>
            <span class="material-icons pick__caret" aria-hidden="true">expand_more</span>
            <q-menu anchor="top left" self="bottom left" class="pick-menu">
              <div class="pick-menu__label section-label">Agent set</div>
              <button v-for="set in agentSets" :key="set.name" v-close-popup class="pick-menu__item" @click="$emit('update:agentSet', set.name)">
                <span class="grow truncate">{{ set.name }}</span><span v-if="!set.ready" class="chip chip--warning">building</span>
                <span v-if="set.name === agentSet" class="material-icons pick-menu__check" aria-hidden="true">check</span>
              </button>
            </q-menu>
            <q-tooltip>Container environment for this chat</q-tooltip>
          </button>
        </div>
        <div class="composer__option">
          <span class="composer__option-label">Reasoning</span>
          <ReasoningPicker :model-value="reasoningEffort" :capabilities="selectedModel" :busy="busy" @update:model-value="$emit('update:reasoningEffort', $event)" />
        </div>
        <div class="composer__option">
          <span class="composer__option-label">Tools</span>
          <button class="pick" :class="{ 'pick--quiet': !toolCount }" type="button" aria-label="Select tools" :disabled="busy">
            <span class="material-icons pick__icon" aria-hidden="true">extension</span><span class="truncate">{{ toolLabel }}</span>
            <span class="material-icons pick__caret" aria-hidden="true">expand_more</span>
            <ToolPicker :model-value="tools" @update:model-value="$emit('update:tools', $event)" />
            <q-tooltip>MCP tools this chat may call</q-tooltip>
          </button>
        </div>
        <div class="composer__option">
          <span class="composer__option-label">Projects</span>
          <button class="pick" type="button" aria-label="Select projects" :class="{ 'pick--quiet': !projectIds.length }" :disabled="busy">
            <span class="material-icons pick__icon" aria-hidden="true">folder_open</span>
            <span class="truncate">{{ projectIds.length ? `${projectIds.length} project${projectIds.length === 1 ? '' : 's'}` : 'Projects' }}</span>
            <span class="material-icons pick__caret" aria-hidden="true">expand_more</span>
            <ProjectPicker :model-value="projectIds" @update:model-value="$emit('update:projectIds', $event)" />
            <q-tooltip>Writable projects for the next message</q-tooltip>
          </button>
        </div>
        <div class="composer__option">
          <span class="composer__option-label">Extensions</span>
          <button class="pick" type="button" aria-label="Select extensions" :class="{ 'pick--quiet': !packages.length }" :disabled="busy || configurationSaving">
            <span class="material-icons pick__icon" aria-hidden="true">widgets</span>
            <span class="truncate">{{ packages.length ? `${packages.length} selected` : 'Extensions' }}</span>
            <span class="material-icons pick__caret" aria-hidden="true">expand_more</span>
            <PackagePicker :model-value="packages" :disabled="busy || configurationSaving" @update:model-value="$emit('update:packages', $event)" />
            <q-tooltip>Extensions, skills and prompts for the next message</q-tooltip>
          </button>
        </div>
      </div>
      <div class="composer__configuration-foot"><span>{{ meter.label }}</span><RouterLink to="/settings/workspace">Workspace preferences<span class="material-icons" aria-hidden="true">arrow_outward</span></RouterLink></div>
    </div>
  </div>
</template>

<script setup>
import { computed, ref, useId } from 'vue'
import ModelPicker from './ModelPicker.vue'
import AgentPicker from './AgentPicker.vue'
import { agentConfig, sameConfig } from '../agent-config'
import ReasoningPicker from './ReasoningPicker.vue'
import ChatImage from './ChatImage.vue'
import { addImages, IMAGE_TYPES } from '../attachments'
import { api } from '../api'
import ToolPicker from './ToolPicker.vue'
import ProjectPicker from './ProjectPicker.vue'
import PackagePicker from './PackagePicker.vue'
import { store } from '../store'
import { preferences } from '../preferences'
import { contextMeter, modelContextWindow } from '../context'

const props = defineProps({
  modelValue: { type: String, default: '' }, attachments: { type: Array, default: () => [] },
  agentId: { type: String, default: null }, agentName: { type: String, default: '' },
  agentSet: { type: String, default: '' }, model: { type: String, default: '' },
  reasoningEffort: { type: String, default: null }, tools: { type: Array, default: null },
  packages: { type: Array, default: () => [] }, commands: { type: Array, default: () => [] },
  projectIds: { type: Array, default: () => [] }, busy: { type: Boolean, default: false },
  running: { type: Boolean, default: false }, stopping: { type: Boolean, default: false },
  context: { type: Object, default: null }, variant: { type: String, default: 'docked' },
  placeholder: { type: String, default: 'Message…' }, configurationReady: { type: Boolean, default: true }, configurationSaving: Boolean
})
const emit = defineEmits(['update:attachments', 'update:modelValue', 'update:agentId', 'update:agentSet', 'update:model', 'update:reasoningEffort', 'update:tools', 'update:projectIds', 'update:packages', 'send', 'stop'])
const input = ref(null)
const focused = ref(false)
const fileInput = ref(null)
const imageError = ref('')
const configurationId = useId()
const configurationOpen = computed({ get: () => preferences.composerExpanded, set: value => { preferences.composerExpanded = value } })
const profileDefaults = computed(() => agentConfig(store.catalog, props.agentId))
const profileLabel = computed(() => props.agentId ? (profileDefaults.value?.agent_name || props.agentName || 'Removed agent') : 'Global defaults')
const customConfiguration = computed(() => !sameConfig(profileDefaults.value, {
  agent_set: props.agentSet || 'default', model: props.model || store.catalog.default_model,
  reasoning_effort: props.reasoningEffort, tools: props.tools, project_ids: props.projectIds, packages: props.packages
}))
const selectedModel = computed(() => store.catalog.models?.find(m => m.id === (props.model || store.catalog.default_model)))
const imageSupport = computed(() => selectedModel.value?.supports_images)
const imageNotice = computed(() => {
  if (imageSupport.value === true) return selectedModel.value?.image_support_reason || 'Image input supported.'
  if (imageSupport.value === false) return `${selectedModel.value?.image_support_reason || 'Image input is unavailable for this model/API route.'} Choose another model${props.attachments.length ? ' or remove the images' : ''}.`
  return 'Image support unverified for this model/API route. Images may not be understood.'
})
function attach (files) {
  if (props.busy || !files?.length) return
  if (imageSupport.value === false) { imageError.value = imageNotice.value; return }
  try { emit('update:attachments', addImages(props.attachments, files)); imageError.value = '' }
  catch (error) { imageError.value = error.message }
}
function chooseFiles (event) { attach(event.target.files); event.target.value = '' }
function drop (event) { attach(event.dataTransfer.files) }
function paste (event) {
  const files = Array.from(event.clipboardData?.items || []).filter(item => item.kind === 'file').map(item => item.getAsFile()).filter(Boolean)
  if (!files.length) return
  event.preventDefault()
  attach(files)
}
function removeImage (image) {
  emit('update:attachments', props.attachments.filter(item => item.id !== image.id))
  imageError.value = ''
  if (image.uploaded) api.discardImage(image.uploadedChat, image.uploaded.id).catch(() => {})
}
const agentSets = computed(() => store.catalog.agent_sets || [])
const allTools = computed(() => store.catalog.tools || [])
const toolCount = computed(() => props.tools === null ? allTools.value.length : props.tools.length)
const toolLabel = computed(() => props.tools === null ? `${allTools.value.length} tools` : `${props.tools.length}/${allTools.value.length} tools`)
const meter = computed(() => contextMeter(props.context, modelContextWindow(store.catalog, props.model)))
const shortModel = computed(() => selectedModel.value?.name || (props.model || store.catalog.default_model || 'Choose model').split('/').pop())
const commandSuggestions = computed(() => /^\/[^\s]*$/.test(props.modelValue)
  ? props.commands.filter(command => command.name.startsWith(props.modelValue.slice(1))).slice(0, 8) : [])
function chooseCommand (name) { emit('update:modelValue', `/${name} `); input.value?.focus() }
function onInput (event) {
  emit('update:modelValue', event.target.value)
  event.target.style.height = 'auto'
  event.target.style.height = `${Math.min(220, event.target.scrollHeight)}px`
}
function onKeydown (event) {
  if (event.key !== 'Enter' || event.isComposing || event.keyCode === 229 || event.shiftKey || event.altKey) return
  const modifier = event.ctrlKey || event.metaKey
  if (preferences.sendShortcut === 'enter' ? modifier : !modifier) return
  event.preventDefault()
  submit()
}
function submit () {
  if (props.busy || !props.configurationReady || (!props.modelValue.trim() && !props.attachments.length) || (props.attachments.length && imageSupport.value === false)) return
  emit('send')
  if (input.value) input.value.style.height = 'auto'
}
defineExpose({ focus: () => input.value?.focus() })
</script>

<style scoped>
.composer { position: relative; min-width: 0; border: 1px solid var(--border-strong); border-radius: var(--radius-lg); background: var(--surface-input); transition: border-color var(--transition), box-shadow var(--transition); }
/* On short viewports the draft and advanced controls scroll, not the primary actions. */
.composer--docked { display: flex; flex-direction: column; max-height: 100%; }
.composer--docked .composer__input { flex: 0 1 auto; min-height: calc(var(--chat-font-size) * 1.6 + 28px); }
.composer--focus { border-color: var(--accent); box-shadow: 0 0 0 3px var(--accent-soft); }
.composer--welcome { border-radius: var(--radius-xl); box-shadow: var(--shadow-md); }
.composer--running { border-color: var(--accent); box-shadow: 0 0 0 1px var(--accent-soft), 0 0 20px var(--accent-soft); }
.composer--running.composer--focus { box-shadow: 0 0 0 3px var(--accent-soft), 0 0 24px var(--accent-soft); }
/* The running indicator is a border, never an overlay on the text. */
.composer--running::before {
  content: ''; position: absolute; inset: -1px; border-radius: inherit; padding: 2px; pointer-events: none;
  background: conic-gradient(from var(--composer-orbit-angle), transparent 0deg 240deg, var(--accent) 285deg, var(--accent-text) 315deg, var(--accent-hover) 330deg, transparent 360deg);
  -webkit-mask: linear-gradient(#fff 0 0) content-box, linear-gradient(#fff 0 0); -webkit-mask-composite: xor;
  mask: linear-gradient(#fff 0 0) content-box, linear-gradient(#fff 0 0); mask-composite: exclude;
  animation: composer-light-orbit 4s linear infinite;
}
@keyframes composer-light-orbit { to { --composer-orbit-angle: 360deg; } }
.composer__commands { max-height: 180px; overflow: auto; border-bottom: 1px solid var(--border); padding: 6px; }
.composer__commands strong { white-space: nowrap; }
.composer__attachments { display: flex; flex-wrap: wrap; gap: 10px; padding: 0 14px; }
.composer__attachment { position: relative; padding-top: 12px; max-width: 150px; }
.composer__attachment > button { position: absolute; right: 0; top: 8px; background: var(--surface-input); }
.composer__notice { margin: 10px 16px; color: var(--warning); }
.composer__input { display: block; width: 100%; max-height: 220px; padding: 16px 18px 10px; border: none; background: none; color: var(--text); font: inherit; font-size: var(--chat-font-size); line-height: 1.6; resize: none; outline: none; overflow-y: auto; }
.composer__input::placeholder { color: var(--text-dim); }
.composer--welcome .composer__input { padding: 22px 22px 12px; }
.composer__bar { display: flex; flex: none; align-items: center; gap: 6px; min-width: 0; padding: 8px 12px 12px; }
.pick { display: inline-flex; align-items: center; gap: 6px; min-width: 0; max-width: 250px; height: 32px; padding: 0 8px; border: 1px solid transparent; border-radius: var(--radius-sm); background: transparent; color: var(--text-muted); font: inherit; font-size: 12px; font-weight: 500; cursor: pointer; transition: background var(--transition), color var(--transition); }
.pick:disabled { opacity: 0.5; cursor: not-allowed; }
.pick:hover:not(:disabled) { background: var(--surface-active); color: var(--text); }
.pick--quiet { color: var(--text-dim); }
.pick--icon { position: relative; flex: none; justify-content: center; width: 34px; padding: 0; }
.pick--icon > .material-icons { font-size: 19px; }
.pick--active { background: var(--accent-soft); color: var(--accent); }
.pick__icon, .pick__caret { flex: none; font-size: 16px; }
.pick__caret { margin-left: auto; opacity: 0.6; }
.composer__configured { position: absolute; top: 1px; right: 2px; width: 5px; height: 5px; border-radius: 50%; background: var(--accent); }
.composer__context { flex: none; padding: 10px 6px; }
.meter { width: 38px; height: 3px; border-radius: var(--radius-pill); background: var(--surface-active); overflow: hidden; }
.meter__fill { height: 100%; background: var(--accent); transition: width 200ms var(--ease); }
.meter__fill--hot { background: var(--warning); }
.composer__send, .composer__stop { position: relative; display: grid; place-items: center; flex: none; width: 36px; height: 36px; border: none; border-radius: var(--radius-sm); background: transparent; color: var(--accent-text); cursor: pointer; }
.composer__send::before { content: ''; position: absolute; inset: 0; background: var(--accent); clip-path: var(--octagon); transition: background var(--transition); }
.composer__send:not(:disabled):hover::before { background: var(--accent-hover); }
.composer__send > .material-icons { position: relative; }
.composer__stop { background: var(--danger-soft); color: var(--danger); }
.composer__send:disabled, .composer__stop:disabled { color: var(--text-dim); cursor: default; }
.composer__send:disabled::before { background: var(--surface-active); }
.composer__send .material-icons, .composer__stop .material-icons { font-size: 20px; }
.composer__configuration { min-height: 0; padding: 14px 16px 12px; border-top: 1px solid var(--border); }
.composer__profile { display: flex; align-items: center; flex-wrap: wrap; gap: 6px; margin: 0 0 14px; }
.composer__profile-picker { max-width: min(280px, 100%); color: var(--text); background: var(--surface-hover); }
.composer__profile > .caption { font-size: 10px; }
.composer__options { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 12px; }
.composer__option { min-width: 0; }
.composer__option-label { display: block; color: var(--text-dim); font-size: 10px; margin: 0 8px 4px; }
.composer__option > .pick { width: 100%; max-width: none; background: var(--surface-hover); }
.composer__configuration-foot { display: flex; align-items: center; justify-content: space-between; gap: 12px; margin: 14px 8px 0; color: var(--text-dim); font-size: 10px; }
.composer__configuration-foot a { display: flex; align-items: center; gap: 4px; color: var(--text-muted); text-decoration: none; }
.composer__configuration-foot .material-icons { font-size: 12px; }
@media (max-width: 1100px) { .composer__options { grid-template-columns: repeat(2, minmax(0, 1fr)); } }
@media (max-width: 640px) {
  .composer__context { display: none; }
  .composer__model { flex: 0 1 auto; max-width: 190px; }
  .composer__bar { padding: 6px 8px 10px; gap: 3px; }
  .composer__input, .composer--welcome .composer__input { padding: 14px 14px 8px; }
  .composer__configuration { padding: 12px 10px; }
  .composer__options { gap: 10px; }
}
@media (max-width: 380px) {
  .composer__model { padding: 0 4px; }
  .composer__model > .pick__icon { display: none; }
  .composer__send, .composer__stop, .pick--icon { width: 32px; }
}
@media (pointer: coarse) {
  .pick, .composer__send, .composer__stop { min-height: 40px; }
  .pick--icon, .composer__send, .composer__stop { min-width: 36px; }
}
</style>

<style>
@property --composer-orbit-angle { syntax: '<angle>'; initial-value: 0deg; inherits: false; }
.pick-menu { min-width: 240px; max-width: min(340px, calc(100vw - 24px)); }
.pick-menu__label { padding: 8px 10px 6px; }
.pick-menu__item { display: flex; align-items: center; gap: 8px; width: 100%; padding: 8px 10px; border: none; border-radius: var(--radius-sm); background: none; color: var(--text); font: inherit; font-size: 13px; text-align: left; cursor: pointer; }
.pick-menu__item:hover { background: var(--surface-hover); }
.pick-menu__check { font-size: 16px; color: var(--accent-hover); }
</style>
