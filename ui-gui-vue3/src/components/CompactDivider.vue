<script setup lang="ts">
defineProps<{ count: number; collapsed: boolean }>()
const emit = defineEmits<{ (e: 'toggle'): void }>()
</script>

<template>
  <div class="cb-divider">
    <span class="cb-line"></span>
    <button class="cb-pill" type="button" @click="emit('toggle')">
      <svg class="cb-chev" :class="{ open: !collapsed }" width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="6 9 12 15 18 9" /></svg>
      <span>{{ collapsed ? `${count} messages above compacted` : 'Collapse messages above' }}</span>
    </button>
    <span class="cb-line"></span>
  </div>
</template>

<style scoped>
.cb-divider { display: flex; align-items: center; gap: 10px; padding: 6px 0; user-select: none; }
.cb-line { flex: 1; height: 1px; background: var(--border-subtle); }
.cb-pill { flex-shrink: 0; display: inline-flex; align-items: center; gap: 5px; background: transparent; border: 1px solid var(--border-subtle); border-radius: 999px; padding: 3px 12px; font-size: 10px; font-family: var(--font-mono); letter-spacing: .3px; color: var(--muted); cursor: pointer; transition: border-color .15s, color .15s; }
.cb-pill:hover { border-color: var(--accent); color: var(--accent); }
.cb-chev { transition: transform .18s var(--spring); }
.cb-chev.open { transform: rotate(180deg); }
</style>
