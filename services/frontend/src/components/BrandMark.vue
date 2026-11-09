<template>
  <img class="brand-mark" :src="brand.logo" alt="" aria-hidden="true" width="40" height="40" />
</template>

<script setup>
import { brand } from '../brand'
</script>

<style scoped>
.brand-mark { display: block; flex: none; width: 40px; height: 40px; object-fit: contain; filter: var(--brand-mark-filter, none); }
</style>
