<script setup lang="ts">
import { ref } from 'vue'
import Tooltip from './Tooltip.vue'
import PathWhitelist from './PathWhitelist.vue'
import QuickNotes from './QuickNotes.vue'
import PermissionMode from './PermissionMode.vue'
import UserNote from './UserNote.vue'
import MessageSearch from './MessageSearch.vue'
import SoundSelect from './SoundSelect.vue'
const p = defineProps<{ onFiles?: (files: File[]) => void; onScrollToIndex?: (msgIdx: number) => void }>()
const emit = defineEmits<{ (e: 'files', files: File[]): void; (e: 'scrollToIndex', idx: number): void }>()
const ir = ref<HTMLInputElement | null>(null)
function click() { ir.value?.click() }
function change(e: Event) {
  const el = e.target as HTMLInputElement
  if (el.files?.length) {
    const files = Array.from(el.files)
    if (p.onFiles) p.onFiles(files); else emit('files', files)
  }
  el.value = ''
}
</script>
<template>
  <div class="fa">
    <input ref="ir" type="file" multiple @change="change" class="fa-input"/>
    <Tooltip text="Attach file"><button class="fa-btn" @click="click"><svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z"/></svg></button></Tooltip>
    <span class="fa-hint">update file</span>
    <PathWhitelist /><QuickNotes /><PermissionMode /><UserNote /><SoundSelect /><MessageSearch :on-scroll-to-index="p.onScrollToIndex" @scroll-to-index="(idx:number)=>emit('scrollToIndex',idx)"/>
  </div>
</template>
