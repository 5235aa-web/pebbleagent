<script setup lang="ts">
import { ref } from 'vue'
defineProps<{text:string;position?:'top'|'bottom'}>()
const show = ref(false)
let timer: ReturnType<typeof setTimeout> | null = null
function enter() { if (timer) clearTimeout(timer); show.value = true }
function leave() { timer = setTimeout(() => show.value = false, 80) }
</script>
<template>
  <span class="tooltip-wrap" @mouseenter="enter" @mouseleave="leave">
    <slot/>
    <span v-if="show && text" :class="['tooltip-box','tooltip-'+(position||'bottom')]">{{ text }}</span>
  </span>
</template>
