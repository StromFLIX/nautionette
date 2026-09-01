<template>
  <q-menu anchor="top left" self="bottom left" class="picker" @show="onShow">
    <div class="picker__search">
      <span class="material-icons">search</span>
      <input v-model="query" class="picker__search-input" placeholder="Search tools" />
      <button v-if="query" class="btn btn--icon btn--sm" @click="query = ''">
        <span class="material-icons" style="font-size: 15px">close</span>
      </button>
    </div>

    <div class="tools__head">
      <span class="section-label grow">{{ enabled.size }} of {{ all.length }} enabled</span>
      <button class="btn btn--sm" @click="setAll(true)">All</button>
      <button class="btn btn--sm" @click="setAll(false)">None</button>
    </div>

    <div class="picker__scroll scroll-y">
      <template v-for="group in groups" :key="group.name">
        <div class="tools__group">
          <button class="tools__chevron" @click="toggle(group.name)">
            <span class="material-icons picker__chevron">
              {{ isOpen(group.name) ? 'expand_more' : 'chevron_right' }}
            </span>
          </button>
          <button class="tools__row grow" @click="setGroup(group, group.state !== 'all')">
            <span class="material-icons tools__box" :class="`tools__box--${group.state}`">
              {{ boxIcon(group.state) }}
            </span>
            <span class="grow truncate">{{ group.name }}</span>
            <span class="picker__meta">{{ group.on }}/{{ group.tools.length }}</span>
          </button>
        </div>

        <button
          v-for="tool in isOpen(group.name) ? group.tools : []" :key="tool.name"
          class="tools__row tools__row--nested" @click="setTool(tool.name, !enabled.has(tool.name))"
        >
          <span class="material-icons tools__box" :class="{ 'tools__box--all': enabled.has(tool.name) }">
            {{ enabled.has(tool.name) ? 'check_box' : 'check_box_outline_blank' }}
          </span>
          <span class="grow">
            <span class="mono tools__name">{{ tool.name }}</span>
            <span v-if="tool.description" class="caption dim clamp-2">{{ tool.description }}</span>
          </span>
        </button>
      </template>

      <p v-if="!groups.length" class="picker__note caption">
        {{ query ? 'No tool matches that.' : 'No MCP server answered.' }}
      </p>
    </div>

    <div class="tools__foot">
      <button class="btn btn--sm" @click="actions.openSettings('mcp')">Manage MCP servers</button>
    </div>
  </q-menu>
</template>

<script setup>
import { computed, ref } from 'vue'
import { actions, store } from '../store'

const props = defineProps({
  // null means every tool, including ones added later; an array pins the choice.
  modelValue: { type: Array, default: null }
})
const emit = defineEmits(['update:modelValue'])

const query = ref('')
const opened = ref(new Set())

const all = computed(() => store.catalog.tools || [])
const enabled = computed(() =>
  new Set(props.modelValue === null ? all.value.map((tool) => tool.name) : props.modelValue))

const groups = computed(() => {
  const needle = query.value.trim().toLowerCase()
  const byServer = new Map()
  for (const tool of all.value) {
    if (needle && !`${tool.name} ${tool.description || ''}`.toLowerCase().includes(needle)) continue
    const server = tool.server || 'other'
    if (!byServer.has(server)) byServer.set(server, [])
    byServer.get(server).push(tool)
  }
  return [...byServer].map(([name, tools]) => {
    const on = tools.filter((tool) => enabled.value.has(tool.name)).length
    return { name, tools, on, state: on === 0 ? 'none' : on === tools.length ? 'all' : 'some' }
  })
})

function boxIcon (state) {
  return { all: 'check_box', some: 'indeterminate_check_box', none: 'check_box_outline_blank' }[state]
}

const isOpen = (name) => Boolean(query.value) || opened.value.has(name)

function toggle (name) {
  const set = new Set(opened.value)
  if (set.has(name)) set.delete(name)
  else set.add(name)
  opened.value = set
}

/** Everything on collapses back to null, so tools added later are picked up too. */
function commit (names) {
  const next = new Set(names)
  emit('update:modelValue', next.size === all.value.length ? null : [...next])
}

function setTool (name, on) {
  const next = new Set(enabled.value)
  if (on) next.add(name)
  else next.delete(name)
  commit(next)
}

function setGroup (group, on) {
  const next = new Set(enabled.value)
  group.tools.forEach((tool) => (on ? next.add(tool.name) : next.delete(tool.name)))
  commit(next)
}

function setAll (on) {
  commit(on ? all.value.map((tool) => tool.name) : [])
}

function onShow () {
  query.value = ''
  opened.value = new Set(groups.value.filter((group) => group.state !== 'none').map((group) => group.name))
}
</script>

<style scoped>
.picker__search {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 4px 6px 4px 10px;
  border-bottom: 1px solid var(--border);
}

.picker__search .material-icons {
  font-size: 16px;
  color: var(--text-dim);
}

.picker__search-input {
  flex: 1;
  min-width: 0;
  height: 30px;
  border: none;
  background: none;
  outline: none;
  color: var(--text);
  font: inherit;
  font-size: 13px;
}

.picker__search-input::placeholder {
  color: var(--text-dim);
}

.tools__head {
  display: flex;
  align-items: center;
  gap: 4px;
  padding: 7px 6px 7px 10px;
  border-bottom: 1px solid var(--border);
}

.tools__group {
  display: flex;
  align-items: center;
}

.tools__chevron {
  display: grid;
  place-items: center;
  width: 22px;
  height: 30px;
  border: none;
  background: none;
  cursor: pointer;
}

.tools__row {
  display: flex;
  align-items: flex-start;
  gap: 7px;
  width: 100%;
  padding: 6px 10px 6px 4px;
  border: none;
  border-radius: var(--radius-sm);
  background: none;
  color: var(--text);
  font: inherit;
  font-size: 13px;
  text-align: left;
  cursor: pointer;
}

.tools__row:hover {
  background: var(--surface-hover);
}

.tools__row--nested {
  padding-left: 30px;
}

.tools__row > .grow {
  display: flex;
  flex-direction: column;
  gap: 1px;
}

.tools__name {
  font-size: 12.5px;
}

.tools__box {
  flex: none;
  font-size: 17px;
  color: var(--text-dim);
}

.tools__box--all,
.tools__box--some {
  color: var(--accent-hover);
}

.tools__foot {
  border-top: 1px solid var(--border);
  padding: 6px;
}
</style>
