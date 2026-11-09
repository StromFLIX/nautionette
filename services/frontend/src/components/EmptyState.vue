<template>
  <section class="empty workspace-empty" :class="{ 'workspace-empty--workflow': tone === 'workflow' }">
    <div class="workspace-empty__glyph"><span class="material-icons" aria-hidden="true">{{ icon }}</span></div>
    <h1>{{ title }}</h1>
    <p>{{ description }}</p>
    <RouterLink v-if="to" :to="to" class="btn btn--outline">{{ action }}</RouterLink>
    <slot />
  </section>
</template>

<script setup>
defineProps({ icon: String, title: String, description: String, to: String, action: String, tone: String })
</script>

<style scoped>
.workspace-empty { min-height: 0; flex: 1; gap: 14px; padding: 32px 24px; text-align: center; }
.workspace-empty__glyph { display: grid; place-items: center; width: 64px; height: 64px; border: 1px solid var(--border); border-radius: var(--radius-lg); background: var(--accent-soft); color: var(--accent); margin-bottom: 10px; }
.workspace-empty--workflow .workspace-empty__glyph { background: var(--warning-soft); color: var(--warning); }
.workspace-empty__glyph .material-icons { font-size: 1.75rem; }
.workspace-empty h1 { margin: 0; color: var(--text); font-size: clamp(1.375rem, 2vw, 1.75rem); font-weight: 600; letter-spacing: -0.035em; line-height: 1.3; }
.workspace-empty p { margin: 0 0 8px; max-width: 380px; color: var(--text-muted); font-size: 0.9375rem; line-height: 1.75; text-wrap: pretty; }
.workspace-empty .btn { text-decoration: none; }
@media (max-width: 560px) { .workspace-empty { padding-inline: 20px; } }
</style>
