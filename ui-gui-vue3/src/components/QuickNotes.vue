<script setup lang="ts">
import { ref, computed, onMounted, onUnmounted, nextTick } from 'vue'
import Tooltip from './Tooltip.vue'
import { getGateway } from '../services/gatewayClient'

const STORAGE_KEY = 'pebble_quick_notes'
interface Note { id: string; text: string }

let _id = Date.now()
function nid(): string { return String(++_id) }

const gw = getGateway()
// 后端文件持久化（~/.pebble/quick_notes.json），与 usernote 机制一致。
// 历史版本用 localStorage，重启会因浏览器源/端口漂移而丢失；后端存储跨模式稳定。
let saveTimer: ReturnType<typeof setTimeout> | null = null
function scheduleSave() { if (saveTimer) clearTimeout(saveTimer); saveTimer = setTimeout(() => saveNotes(), 500) }
async function loadNotes(): Promise<void> {
  let backendNotes: Note[] | null = null
  try {
    const r = await gw.request<{ notes?: Note[] }>('quick-notes.read', {})
    if (Array.isArray(r.notes)) backendNotes = r.notes
  } catch { /* 后端不可用时保持空列表 */ }
  // 合并后端 + localStorage（按 id 去重），旧版 localStorage 残留数据不丢弃
  const merged: Note[] = backendNotes ? [...backendNotes] : []
  try {
    const old = localStorage.getItem(STORAGE_KEY)
    if (old) {
      const arr = JSON.parse(old)
      if (Array.isArray(arr)) {
        const ids = new Set(merged.map(n => n.id))
        let changed = false
        for (const n of arr) {
          if (n && n.id && !ids.has(n.id)) { merged.push(n); ids.add(n.id); changed = true }
        }
        if (changed) { notes.value = merged; await saveNotes() }
        localStorage.removeItem(STORAGE_KEY)
      }
    }
  } catch { /* 忽略迁移失败 */ }
  notes.value = merged
}
async function saveNotes(): Promise<void> {
  try { await gw.request('quick-notes.write', { notes: notes.value }) } catch { /* 保存失败静默 */ }
}

const open = ref(false)
const notes = ref<Note[]>([])
const search = ref('')
const adding = ref(false)
const addText = ref('')
const editId = ref<string | null>(null)
const editText = ref('')
const addRef = ref<HTMLInputElement | null>(null)
const editRef = ref<HTMLInputElement | null>(null)

const filtered = computed(() => {
  const q = search.value.trim().toLowerCase()
  if (!q) return notes.value
  return notes.value.filter(n => n.text.toLowerCase().includes(q))
})

function toggle() {
  open.value = !open.value
  if (open.value) { search.value = ''; cancelAll(); loadNotes() }
}

function closePanel(e: MouseEvent) {
  if (!open.value) return
  const t = e.target as HTMLElement
  if (!t.closest('.qn-panel') && !t.closest('.qn-btn')) open.value = false
}

onMounted(() => {
  document.addEventListener('mousedown', closePanel)
  // WS 连接建立后自动加载（页面刷新瞬间 WS 未就绪，直接 loadNotes 会失败；onStatus 兜底）
  offStatus = gw.onStatus(c => { if (c) loadNotes() })
})
let offStatus: (() => void) | null = null
onUnmounted(() => { document.removeEventListener('mousedown', closePanel); if (offStatus) offStatus(); if (saveTimer) clearTimeout(saveTimer) })

function cancelAll() { adding.value = false; addText.value = ''; editId.value = null; editText.value = '' }

function startAdd() {
  cancelAll()
  adding.value = true
  addText.value = ''
  nextTick(() => addRef.value?.focus())
}

function confirmAdd() {
  const t = addText.value.trim()
  if (!t) { adding.value = false; return }
  notes.value.push({ id: nid(), text: t })
  scheduleSave()
  adding.value = false
  addText.value = ''
}
// 输入框失焦时自动确认保存（点面板外/刷新都不会丢）
function onAddBlur() {
  if (adding.value && addText.value.trim()) confirmAdd()
}

function startEdit(note: Note) {
  cancelAll()
  editId.value = note.id
  editText.value = note.text
  nextTick(() => editRef.value?.focus())
}

function confirmEdit() {
  const t = editText.value.trim()
  if (!t || !editId.value) { editId.value = null; return }
  const i = notes.value.findIndex(n => n.id === editId.value)
  if (i >= 0) { notes.value[i] = { ...notes.value[i], text: t }; scheduleSave() }
  editId.value = null
}
// 编辑时即时更新（输入即保存，与 usernote 一致）
function onEditInput(e: Event) {
  const v = (e.target as HTMLInputElement).value
  editText.value = v
  if (!editId.value) return
  const i = notes.value.findIndex(n => n.id === editId.value)
  if (i >= 0) { notes.value[i] = { ...notes.value[i], text: v }; scheduleSave() }
}
function onEditBlur() {
  if (editId.value && editText.value.trim()) confirmEdit()
}

function del(id: string) {
  notes.value = notes.value.filter(n => n.id !== id)
  saveNotes()
  if (editId.value === id) editId.value = null
}

function select(text: string) {
  window.dispatchEvent(new CustomEvent('gateway:insert-quick-note', { detail: { text } }))
  open.value = false
}
</script>
<template>
  <div class="qn">
    <Tooltip text="Quick Notes">
      <button class="qn-btn" :class="{ active: open }" @click="toggle">
        <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><path d="M12 20h9"/><path d="M16.5 3.5a2.121 2.121 0 0 1 3 3L7 19l-4 1 1-4L16.5 3.5z"/></svg>
        <span v-if="notes.length" class="qn-badge">{{ notes.length }}</span>
      </button>
    </Tooltip>
    <span class="qn-label">quick note</span>

    <div v-if="open" class="qn-panel">
      <!-- header -->
      <div class="qn-hdr">
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><path d="M12 20h9"/><path d="M16.5 3.5a2.121 2.121 0 0 1 3 3L7 19l-4 1 1-4L16.5 3.5z"/></svg>
        <span class="qn-title">Quick Notes</span>
        <div class="qn-search-wrap">
          <svg class="qn-search-icon" width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><circle cx="11" cy="11" r="7"/><line x1="16" y1="16" x2="22" y2="22"/></svg>
          <input class="qn-search" v-model="search" placeholder="Search..." />
        </div>
        <Tooltip text="New"><button class="qn-add-btn" @click="startAdd"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/></svg></button></Tooltip>
      </div>

      <!-- list -->
      <div class="qn-list">
        <!-- add row -->
        <div v-if="adding" class="qn-row editing">
          <input ref="addRef" class="qn-edit-input" v-model="addText" placeholder="Enter note..." @keydown.enter="confirmAdd" @keydown.escape="adding=false" @blur="onAddBlur" />
          <Tooltip text="Confirm"><button class="qn-icon-btn save" @click="confirmAdd"><svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="20 6 9 17 4 12"/></svg></button></Tooltip>
          <Tooltip text="Cancel"><button class="qn-icon-btn delete" @mousedown.prevent="adding=false"><svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg></button></Tooltip>
        </div>

        <!-- empty -->
        <div v-if="!adding && filtered.length===0" class="qn-empty">{{ search ? 'No matches' : 'Click + to add a note' }}</div>

        <!-- note rows -->
        <template v-for="note in filtered" :key="note.id">
          <div class="qn-row" :class="{ editing: editId===note.id }">
            <template v-if="editId===note.id">
              <input ref="editRef" class="qn-edit-input" v-model="editText" @input="onEditInput" @keydown.enter="confirmEdit" @keydown.escape="editId=null" @blur="onEditBlur" />
              <Tooltip text="Confirm"><button class="qn-icon-btn save" @click="confirmEdit"><svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="20 6 9 17 4 12"/></svg></button></Tooltip>
              <Tooltip text="Cancel"><button class="qn-icon-btn delete" @mousedown.prevent="editId=null"><svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg></button></Tooltip>
            </template>
            <template v-else>
              <span class="qn-text" @click="select(note.text)">{{ note.text }}</span>
              <div class="qn-row-actions">
                <Tooltip text="Edit"><button class="qn-icon-btn" @click="startEdit(note)"><svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7"/><path d="M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z"/></svg></button></Tooltip>
                <Tooltip text="Delete"><button class="qn-icon-btn delete" @click="del(note.id)"><svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><polyline points="3 6 5 6 21 6"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/></svg></button></Tooltip>
              </div>
            </template>
          </div>
        </template>
      </div>
    </div>
  </div>
</template>
