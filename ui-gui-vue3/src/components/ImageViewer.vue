<script setup lang="ts">
import { watch } from 'vue'

const p = defineProps<{ src: string; alt?: string }>()
const emit = defineEmits<{ (e: 'close'): void }>()

function onKey(e: KeyboardEvent) { if (e.key === 'Escape') emit('close') }

watch(() => p.src, (v) => {
  if (v) document.addEventListener('keydown', onKey)
  else document.removeEventListener('keydown', onKey)
})
</script>

<template>
  <Teleport to="body">
    <div v-if="src" class="iv-overlay" @click.self="emit('close')">
      <button class="iv-close" @click="emit('close')" title="Close">
        <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>
      </button>
      <img :src="src" :alt="alt || 'preview'" class="iv-img" />
    </div>
  </Teleport>
</template>
