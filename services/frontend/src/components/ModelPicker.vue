<template>
  <q-menu anchor="top left" self="bottom left" class="picker" @show="onShow">
    <div class="picker__search">
      <span class="material-icons">search</span>
      <input
        ref="search" v-model="query" class="picker__search-input"
        placeholder="Search models" @keydown.enter="chooseFirst"
      />
      <button v-if="query" class="btn btn--icon btn--sm" @click="query = ''">
        <span class="material-icons" style="font-size: 15px">close</span>
      </button>
    </div>

    <div class="picker__scroll scroll-y">
      <template v-if="!query && quick.length">
        <div class="picker__label section-label">Quick select</div>
        <button
          v-for="option in quick" :key="`quick-${option.id}`" class="picker__row"
          :class="{ 'picker__row--on': option.id === modelValue }" @click="choose(option.id)"
        >
          <span class="material-icons picker__tick">
            {{ option.id === modelValue ? 'radio_button_checked' : 'radio_button_unchecked' }}
          </span>
          <span class="grow truncate">{{ option.name }}</span>
          <span v-if="option.alias" class="picker__meta">latest</span>
        </button>
      </template>

      <template v-for="gateway in groups" :key="gateway.name">
        <div class="picker__label section-label">
          <span class="material-icons" style="font-size: 13px">cloud</span>
          via {{ gateway.name }}
        </div>

        <template v-for="provider in gateway.providers" :key="`${gateway.name}/${provider.name}`">
          <button class="picker__group" @click="toggle(gateway.name, provider.name)">
            <span class="material-icons picker__chevron">
              {{ isOpen(gateway.name, provider.name) ? 'expand_more' : 'chevron_right' }}
            </span>
            <span class="grow truncate">{{ provider.label }}</span>
            <span v-if="provider.holdsCurrent" class="dot dot--ok" />
            <span class="picker__meta">{{ provider.models.length }}</span>
          </button>
          <button
            v-for="option in isOpen(gateway.name, provider.name) ? provider.models : []"
            :key="option.id" class="picker__row picker__row--nested"
            :class="{ 'picker__row--on': option.id === modelValue }" @click="choose(option.id)"
          >
            <span class="material-icons picker__tick">
              {{ option.id === modelValue ? 'radio_button_checked' : 'radio_button_unchecked' }}
            </span>
            <span class="grow truncate">{{ option.short }}</span>
            <span v-if="option.alias" class="picker__meta">latest</span>
            <span v-else-if="option.context_length" class="picker__meta">{{ contextLabel(option) }}</span>
          </button>
        </template>
      </template>

      <p v-if="!groups.length" class="picker__note caption">
        {{ query ? 'No model matches that.' : 'The gateway offered no models.' }}
      </p>
    </div>
  </q-menu>
</template>

<script setup>
import { computed, nextTick, ref } from 'vue'
import { store } from '../store'

const RECENT_KEY = 'nautionette.recentModels'
const QUICK_COUNT = 5

const props = defineProps({ modelValue: { type: String, default: '' } })
const emit = defineEmits(['update:modelValue'])

const query = ref('')
const search = ref(null)
const opened = ref(new Set())
const recent = ref(JSON.parse(localStorage.getItem(RECENT_KEY) || '[]'))

const models = computed(() => store.catalog.models || [])

const filtered = computed(() => {
  const needle = query.value.trim().toLowerCase()
  if (!needle) return models.value
  return models.value.filter((model) =>
    `${model.id} ${model.name}`.toLowerCase().includes(needle))
})

/** Two levels: the gateway that fronts the model, then whoever built it. */
const groups = computed(() => {
  const byGateway = new Map()
  for (const model of filtered.value) {
    const gateway = model.gateway || 'gateway'
    if (!byGateway.has(gateway)) byGateway.set(gateway, new Map())
    const byProvider = byGateway.get(gateway)
    const provider = model.provider || 'other'
    if (!byProvider.has(provider)) byProvider.set(provider, [])
    byProvider.get(provider).push({ ...model, short: shortName(model) })
  }
  return [...byGateway].map(([name, byProvider]) => ({
    name,
    providers: [...byProvider]
      .map(([provider, list]) => ({
        name: provider,
        label: label(provider, list),
        models: list.slice().sort((a, b) => Number(b.alias) - Number(a.alias) || a.short.localeCompare(b.short)),
        holdsCurrent: list.some((model) => model.id === props.modelValue)
      }))
      .sort((a, b) => b.models.length - a.models.length || a.label.localeCompare(b.label))
  }))
})

const quick = computed(() => {
  const preferred = [...recent.value, store.catalog.default_model, props.modelValue]
  // Top up with the always-latest aliases, which is what most people want anyway.
  const fallback = models.value
    .filter((model) => model.alias)
    .map((model) => model.id)
  return [...new Set([...preferred, ...fallback])]
    .map((id) => models.value.find((model) => model.id === id))
    .filter(Boolean)
    .slice(0, QUICK_COUNT)
})

function shortName (model) {
  // "Anthropic: Claude Sonnet 4" reads as "Claude Sonnet 4" under its own heading.
  const name = model.name || model.id
  return name.includes(': ') ? name.split(': ').slice(1).join(': ') : name
}

/** Vendors spell themselves properly in the model name; the slug is a fallback. */
function label (provider, list) {
  const branded = list.find((model) => (model.name || '').includes(': '))
  if (branded) return branded.name.split(': ')[0]
  return provider.replace(/[-_]/g, ' ').replace(/\b\w/g, (letter) => letter.toUpperCase())
}

function contextLabel (model) {
  const thousands = model.context_length / 1000
  return thousands >= 1000 ? `${Math.round(thousands / 1000)}M` : `${Math.round(thousands)}k`
}

const key = (gateway, provider) => `${gateway}/${provider}`
const isOpen = (gateway, provider) =>
  Boolean(query.value) || opened.value.has(key(gateway, provider))

function toggle (gateway, provider) {
  const set = new Set(opened.value)
  const id = key(gateway, provider)
  if (set.has(id)) set.delete(id)
  else set.add(id)
  opened.value = set
}

function choose (id) {
  recent.value = [id, ...recent.value.filter((item) => item !== id)].slice(0, QUICK_COUNT)
  localStorage.setItem(RECENT_KEY, JSON.stringify(recent.value))
  emit('update:modelValue', id)
}

function chooseFirst () {
  const first = groups.value[0]?.providers[0]?.models[0]
  if (first) choose(first.id)
}

function onShow () {
  query.value = ''
  const current = models.value.find((model) => model.id === props.modelValue)
  opened.value = new Set(current ? [key(current.gateway, current.provider)] : [])
  nextTick(() => search.value?.focus())
}
</script>

<style scoped>
.picker__search {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 4px 6px 4px 10px;
  margin-bottom: 4px;
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
</style>
