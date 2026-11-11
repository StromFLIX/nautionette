<template>
  <div class="chat-tree" :class="{ 'chat-tree--root': depth === 0, 'chat-tree--expanded': open, 'chat-tree--deep': depth >= 4 }">
    <ChatRow :chat="node.chat" :active-route-id="activeRouteId" :read-busy="readBusy" :tree-depth="depth"
      :tree-answering="node.summary.answering" :tree-unread="node.summary.unread" :tree-activity-at="node.summary.last_activity_at"
      :tree-count="node.count" :tree-open="open" :tree-children-id="childrenId"
      @toggle-tree="$emit('toggle-tree', $event)" @toggle-unread="(chat, unread) => $emit('toggle-unread', chat, unread)" />
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
}
</style>
