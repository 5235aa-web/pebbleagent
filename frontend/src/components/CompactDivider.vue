<script setup lang="ts">
defineProps<{ count: number; tools: number; collapsed: boolean }>()
const emit = defineEmits<{ (e: 'toggle'): void }>()
</script>

<template>
  <div
    class="cdiv"
    :class="{ collapsed }"
    data-live="1"
    role="button"
    tabindex="0"
    :aria-expanded="!collapsed"
    @click="emit('toggle')"
    @keydown.enter.prevent="emit('toggle')"
    @keydown.space.prevent="emit('toggle')"
  >
    <span class="cdiv-badge">HIDDEN</span>
    <span class="cdiv-text"><b>{{ count }} earlier message{{ count === 1 ? '' : 's' }}</b> folded<template v-if="tools > 0"> · {{ tools }} tool burst{{ tools === 1 ? '' : 's' }}</template></span>
    <span class="cdiv-arrow"><svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="6 9 12 15 18 9" /></svg></span>
  </div>
</template>
