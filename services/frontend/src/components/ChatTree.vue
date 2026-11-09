<template>
  <div class="chat-tree" :class="{ 'chat-tree--root': depth === 0, 'chat-tree--expanded': open, 'chat-tree--deep': depth >= 4 }">
    <ChatRow :chat="node.chat" :active-route-id="activeRouteId" :read-busy="readBusy" :tree-depth="depth"
      :tree-answering="node.summary.answering" :tree-unread="node.summary.unread" :tree-activity-at="node.summary.last_activity_at"
      @toggle-unread="(chat, unread) => $emit('toggle-unread', chat, unread)" />
    <button v-if="node.count" type="button" class="chat-tree__disclosure"
      :aria-label="`${open ? 'Collapse' : 'Expand'} agent chats for ${node.chat.title} (${node.count})`"
      :aria-expanded="open" :aria-controls="open ? childrenId : undefined"
      @click="$emit('toggle-tree', node.chat.id)">
      <span class="material-icons chat-tree__icon" aria-hidden="true">account_tree</span>
      <span>{{ node.count }} agent {{ node.count === 1 ? 'chat' : 'chats' }}</span>
      <span v-if="activeCount" class="chat-tree__activity">· {{ activeCount }} active</span>
      <span class="material-icons chat-tree__chevron" aria-hidden="true">{{ open ? 'expand_less' : 'expand_more' }}</span>
    </button>
    <div v-if="open" :id="childrenId" class="chat-tree__children" role="group" :aria-label="`Agent chats for ${node.chat.title}`">
      <ChatTree v-for="child in node.children" :key="child.chat.id" :node="child" :depth="depth + 1"
        :expanded="expanded" :active-route-id="activeRouteId" :read-busy="readBusy"
        @toggle-tree="$emit('toggle-tree', $event)" @toggle-unread="(chat, unread) => $emit('toggle-unread', chat, unread)" />
    </div>
  </div>
</template>

<script setup>
import { computed, useId } from 'vue'
import ChatRow from './ChatRow.vue'
import { chatTreeRows } from '../chat-tree'

const props = defineProps({
  node: { type: Object, required: true },
  expanded: { type: Set, required: true },
  depth: { type: Number, default: 0 },
  activeRouteId: { type: String, default: '' },
  readBusy: { type: String, default: '' }
})
defineEmits(['toggle-tree', 'toggle-unread'])
const childrenId = useId()
const open = computed(() => props.node.children.length > 0 && props.expanded.has(props.node.chat.id))
const activeCount = computed(() => chatTreeRows(props.node, new Set(), true).slice(1).filter(node => node.chat.answering).length)
</script>

<style scoped>
.chat-tree {
  --tree-indent: 20px;
  --tree-rail: color-mix(in srgb, var(--accent) 35%, var(--border-strong));
  --tree-row-mid: calc((2 * var(--list-padding) * .6 + 1.25rem) / 2);
  min-width: 0;
  position: relative;
}
.chat-tree--root.chat-tree--expanded {
  background: color-mix(in srgb, var(--accent) 4%, transparent);
  border-radius: var(--radius-md);
  margin-block-end: 8px;
  padding-block-end: 6px;
}
.chat-tree__disclosure {
  display: flex;
  align-items: center;
  gap: 5px;
  min-height: 28px;
  max-width: calc(100% - 25px);
  margin: -2px 6px 3px 25px;
  padding: 3px 7px;
  border: 0;
  border-radius: var(--radius-sm);
  background: transparent;
  color: var(--text-muted);
  font: inherit;
  font-size: .6875rem;
  cursor: pointer;
  text-align: start;
}
.chat-tree__disclosure:hover { color: var(--text); background: var(--surface-hover); }
.chat-tree__disclosure:focus-visible { outline: 2px solid var(--accent); outline-offset: -2px; }
.chat-tree__icon { font-size: 13px; }
.chat-tree__chevron { font-size: 16px; }
.chat-tree__activity { color: var(--agent-color); }
.chat-tree__children {
  margin-inline-start: var(--tree-indent);
}
/* Each child's rail stops at its final row, so the end of a family is visible.
   Nested rails continue through a child's own expanded subtree. */
.chat-tree__children > .chat-tree::before {
  content: '';
  position: absolute;
  inset-inline-start: -4px;
  top: 0;
  bottom: 0;
  border-inline-start: 1px solid var(--tree-rail);
  pointer-events: none;
}
.chat-tree__children > .chat-tree:last-child::before {
  bottom: auto;
  height: var(--tree-row-mid);
}
.chat-tree__children > .chat-tree::after {
  content: '';
  position: absolute;
  inset-inline-start: -4px;
  top: var(--tree-row-mid);
  width: 10px;
  border-block-start: 1px solid var(--tree-rail);
  pointer-events: none;
}
/* Very deep delegations retain their semantic grouping without consuming the
   entire width of narrow sidebars. */
.chat-tree--deep > .chat-tree__children { margin-inline-start: 4px; }
@media (max-width: 900px), (pointer: coarse) {
  .chat-tree { --tree-row-mid: 22px; }
  .chat-tree__disclosure { min-height: 44px; margin-block: 0 2px; }
}
</style>
