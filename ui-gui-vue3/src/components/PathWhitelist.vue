<script setup lang="ts">
import { ref, onMounted, onUnmounted } from 'vue'
import { getGateway } from '../services/gatewayClient'
import Tooltip from './Tooltip.vue'
const open = ref(false); const paths = ref<string[]>([]); const loading = ref(false)
const newPath = ref(''); const editingIdx = ref<number | null>(null); const editingVal = ref('')
const editRef = ref<HTMLInputElement | null>(null)
const gw = getGateway()
async function load() { loading.value = true; try { const res = await gw.request<{ paths: string[] }>('path_whitelist.read', {}); paths.value = res.paths || [] } catch {} finally { loading.value = false } }
function toggle() { open.value = !open.value; if (open.value) load(); else { editingIdx.value = null; editingVal.value = ''; newPath.value = '' } }
function hmousedown(e: MouseEvent) { if (!open.value) return; const t = e.target as HTMLElement; if (!t.closest('.pw-panel') && !t.closest('.pw-btn')) { open.value = false; editingIdx.value = null; editingVal.value = ''; newPath.value = '' } }
onMounted(() => { load(); document.addEventListener('mousedown', hmousedown) })
onUnmounted(() => document.removeEventListener('mousedown', hmousedown))
import { watch } from 'vue'
watch(editingIdx, v => { if (v !== null) editRef.value?.focus() })
async function addPath() { const p = newPath.value.trim(); if (!p || paths.value.length >= 5 || paths.value.includes(p)) { newPath.value = ''; return }; const next = [...paths.value, p]; paths.value = next; newPath.value = ''; try { await gw.request('path_whitelist.save', { paths: next }) } catch { paths.value = paths.value.filter((_, i) => i !== next.length - 1) } }
async function removePath(idx: number) { const prev = paths.value; const next = prev.filter((_, i) => i !== idx); paths.value = next; try { await gw.request('path_whitelist.save', { paths: next }) } catch { paths.value = prev }; if (editingIdx.value === idx) { editingIdx.value = null; editingVal.value = '' } }
function startEdit(idx: number) { editingIdx.value = idx; editingVal.value = paths.value[idx] }
function cancelEdit() { editingIdx.value = null; editingVal.value = '' }
async function saveEdit(idx: number) { const val = editingVal.value.trim(); if (!val || val === paths.value[idx]) { cancelEdit(); return }; const prev = paths.value; const next = [...prev]; next[idx] = val; paths.value = next; editingIdx.value = null; editingVal.value = ''; try { await gw.request('path_whitelist.save', { paths: next }) } catch { paths.value = prev } }
</script>
<template>
  <div class="pw">
    <Tooltip text="Whitelist Paths"><button class="pw-btn" :class="{active:open}" @click="toggle"><svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z"/><circle cx="12" cy="13" r="2"/><path d="M10 17h4"/></svg><span v-if="paths.length>0" class="pw-badge">{{paths.length}}</span></button></Tooltip>
    <span class="pw-label">white list</span>
    <div v-if="open" class="pw-panel">
      <div class="pw-hdr"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z"/></svg><span class="pw-title">Whitelist Paths</span><span class="pw-limit">{{paths.length}}/5</span><Tooltip text="Refresh" position="bottom"><button class="pw-refresh" @click="load"><svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><polyline points="23 4 23 10 17 10"/><path d="M20.49 15a9 9 0 1 1-2.12-9.36L23 10"/></svg></button></Tooltip></div>
      <div class="pw-body">
        <div v-if="loading&&paths.length===0" class="pw-empty">Loading...</div>
        <div v-if="!loading&&paths.length===0" class="pw-empty">No whitelisted paths</div>
        <div v-for="(p,i) in paths" :key="i" class="pw-item">
          <input v-if="editingIdx===i" ref="editRef" class="pw-edit-input" v-model="editingVal" @blur="saveEdit(i)" @keydown.enter="saveEdit(i)" @keydown.escape="cancelEdit()"/>
          <span v-else class="pw-text" :title="p" @dblclick="startEdit(i)">{{p}}</span>
          <Tooltip text="Edit" position="bottom"><button class="pw-edit-btn" @click="startEdit(i)"><svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7"/><path d="M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z"/></svg></button></Tooltip>
          <Tooltip text="Delete" position="bottom"><button class="pw-del-btn" @click="removePath(i)"><svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg></button></Tooltip>
        </div>
      </div>
      <div v-if="paths.length<5" class="pw-add"><input class="pw-input" v-model="newPath" placeholder="Enter absolute path..." @keydown.enter="addPath()"/><button class="pw-add-btn" :disabled="!newPath.trim()" @click="addPath"><svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/></svg></button></div>
    </div>
  </div>
</template>
