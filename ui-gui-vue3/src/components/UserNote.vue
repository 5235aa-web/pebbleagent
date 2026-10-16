<script setup lang="ts">
import { ref, onMounted, onUnmounted } from 'vue'
import { getGateway } from '../services/gatewayClient'
import Tooltip from './Tooltip.vue'
const open = ref(false); const content = ref(''); const loading = ref(false); const saved = ref(true)
const textareaRef = ref<HTMLTextAreaElement | null>(null)
let saveTimer: ReturnType<typeof setTimeout> | null = null
const gw = getGateway()
async function load() { loading.value = true; try { const res = await gw.request<{ content: string }>('usernote.read', {}); content.value = res.content || '' } catch {} finally { loading.value = false } }
async function save(text: string) { try { await gw.request('usernote.write', { content: text }); saved.value = true } catch {} }
function handleInput(e: Event) { const val = (e.target as HTMLTextAreaElement).value; content.value = val; saved.value = false; if (saveTimer) clearTimeout(saveTimer); saveTimer = setTimeout(() => save(val), 500) }
function toggle() { open.value = !open.value; if (open.value) { load(); setTimeout(() => textareaRef.value?.focus(), 50) } }
function hmousedown(e: MouseEvent) { if (!open.value) return; const t = e.target as HTMLElement; if (!t.closest('.un-panel') && !t.closest('.un-btn')) open.value = false }
onMounted(() => document.addEventListener('mousedown', hmousedown))
onUnmounted(() => { document.removeEventListener('mousedown', hmousedown); if (saveTimer) clearTimeout(saveTimer) })
async function refresh() { saved.value = false; await load(); saved.value = true }
</script>
<template>
  <div class="un">
    <Tooltip text="User Notes"><button class="un-btn" :class="{active:open}" @click="toggle"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><path d="M12 20h9"/><path d="M16.5 3.5a2.121 2.121 0 0 1 3 3L7 19l-4 1 1-4L16.5 3.5z"/></svg></button></Tooltip>
    <span class="un-label">user note</span>
    <div v-if="open" class="un-panel">
      <div class="un-hdr"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" width="14" height="14"><path d="M12 20h9"/><path d="M16.5 3.5a2.121 2.121 0 0 1 3 3L7 19l-4 1 1-4L16.5 3.5z"/></svg><span class="un-title">User Notes</span><span v-if="saved" class="un-saved">Saved</span><Tooltip text="Refresh" position="bottom"><button class="un-hdr-btn" @click="refresh"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" width="12" height="12"><path d="M23 4v6h-6"/><path d="M1 20v-6h6"/><path d="M3.51 9a9 9 0 0 1 14.85-3.36L23 10M1 14l4.64 4.36A9 9 0 0 0 20.49 15"/></svg></button></Tooltip></div>
      <div class="un-body">
        <div v-if="loading" class="un-empty">Loading...</div>
        <textarea v-else ref="textareaRef" class="un-textarea" :value="content" @input="handleInput" placeholder="Record your thoughts, decisions, and to-dos..." spellcheck="false"/>
      </div>
    </div>
  </div>
</template>
