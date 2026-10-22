<script setup lang="ts">
import { ref, computed, inject, onMounted, onUnmounted } from 'vue'
import { useGatewayStore, type AttachmentItem } from '../stores/gateway'
import { useConfigStore } from '../stores/config'
import { fileIcon, fileSize, type FileItem } from '../composables/useFileDrop'
import Tooltip from './Tooltip.vue'
import TodoWidget from './TodoWidget.vue'
import PathWhitelist from './PathWhitelist.vue'
import QuickNotes from './QuickNotes.vue'
import PermissionMode from './PermissionMode.vue'
import UserNote from './UserNote.vue'
import MessageSearch from './MessageSearch.vue'
import SoundSelect from './SoundSelect.vue'

const p = defineProps<{ onScrollToIndex?: (idx: number) => void }>()
const g = useGatewayStore()
const cfg = useConfigStore()
const t = ref('')
const fi = ref<HTMLInputElement | null>(null)
import type { Ref } from 'vue'
const fileDrop = inject<{ state: Ref<{ over: boolean; processing: boolean; error: string | null; files: FileItem[] }>; removeFile: (i: number) => void; clearFiles: () => void; ingest: (items: (File | string)[]) => void }>('fileDrop')
// 图片细节级别（DeepSeek Vision：low=512x512 省 token / original=原图 / 空=auto 不传参）
const imgDetail = ref<'low' | 'original' | ''>('')
const hasImages = computed(() => (fileDrop?.state.value.files || []).some(f => f.isImage))

const send = () => {
  const x = t.value.trim()
  const files = fileDrop?.state.value.files?.length ? [...fileDrop.state.value.files] as unknown as AttachmentItem[] : undefined
  if (!x && (!files || files.length === 0)) return
  if (g.streaming) return
  // No project folder yet: open the setup guide instead of sending, so the
  // session never runs against the sidecar's working directory.
  if (!cfg.projectPath) { window.dispatchEvent(new CustomEvent('pebble:request-project-setup')); return }
  // 图片细节级别：仅对图片附件生效，'' = 不传（Auto/等价 original）
  if (files && imgDetail.value) {
    for (const f of files) { if ((f as { isImage?: boolean }).isImage) (f as AttachmentItem).detail = imgDetail.value }
  }
  g.sendMessage(x, files)
  t.value = ''
  fileDrop?.clearFiles()
}

const hk = (e: KeyboardEvent) => {
  if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); send() }
}

const hasContent = computed(() => t.value.trim().length > 0 || (fileDrop?.state.value.files?.length || 0) > 0)
const ph = computed(() => g.streaming ? 'Agent is responding...' : (!cfg.projectPath ? 'select a project folder first...' : 'enter message...'))

function onQuickNote(e: Event) {
  const d = (e as CustomEvent).detail as { text: string } | undefined
  if (d?.text) t.value = t.value ? t.value + ' ' + d.text : d.text
}

function triggerFilePicker() { fi.value?.click() }
function handleFilePickerChange(e: Event) {
  const el = e.target as HTMLInputElement
  if (el.files?.length) fileDrop?.ingest(Array.from(el.files))
  el.value = ''
}

// ===== 输入框上边缘拖拽拉伸（高度持久化到 localStorage） =====
const MIN_H = 38 // 与 .ih textarea 的 min-height 保持一致
const H_KEY = 'pebble:input-height'
const ta = ref<HTMLTextAreaElement | null>(null)
const inputH = ref<number | null>(null) // null = 默认高度（CSS 控制）
const resizing = ref(false)
let dragStartY = 0
let dragStartH = MIN_H

function maxH(): number {
  // 上限：视口高度 70%，且保证消息列表至少留有 180px（防小窗口挤爆）
  const mainH = (document.querySelector('.main') as HTMLElement | null)?.clientHeight ?? window.innerHeight
  return Math.max(MIN_H, Math.min(Math.floor(window.innerHeight * 0.7), mainH - 180))
}

function persistH() {
  if (inputH.value !== null && inputH.value > MIN_H + 1) localStorage.setItem(H_KEY, String(Math.round(inputH.value)))
  else localStorage.removeItem(H_KEY)
}

function onResizeDown(e: PointerEvent) {
  e.preventDefault()
  if (!ta.value) return
  dragStartY = e.clientY
  dragStartH = ta.value.getBoundingClientRect().height
  resizing.value = true
  document.body.classList.add('ia-resizing')
  ;(e.currentTarget as HTMLElement).setPointerCapture(e.pointerId)
}

function onResizeMove(e: PointerEvent) {
  if (!resizing.value) return
  const delta = dragStartY - e.clientY // 向上拖拽为正
  inputH.value = Math.max(MIN_H, Math.min(dragStartH + delta, maxH()))
}

function onResizeUp(e: PointerEvent) {
  if (!resizing.value) return
  resizing.value = false
  document.body.classList.remove('ia-resizing')
  const el = e.currentTarget as HTMLElement
  if (el.hasPointerCapture(e.pointerId)) el.releasePointerCapture(e.pointerId)
  if (inputH.value !== null && inputH.value <= MIN_H + 1) inputH.value = null // 拖回默认高度 = 复原
  persistH()
}

function onWinResize() {
  if (inputH.value !== null && inputH.value > maxH()) {
    inputH.value = maxH()
    persistH()
  }
}

onMounted(() => {
  window.addEventListener('gateway:insert-quick-note', onQuickNote)
  window.addEventListener('resize', onWinResize)
  // 恢复持久化的输入框高度（clamp 到安全范围）
  const saved = Number(localStorage.getItem(H_KEY))
  if (saved && saved > MIN_H + 1) inputH.value = Math.min(saved, maxH())
})
onUnmounted(() => {
  window.removeEventListener('gateway:insert-quick-note', onQuickNote)
  window.removeEventListener('resize', onWinResize)
  document.body.classList.remove('ia-resizing') // 防御：拖拽中卸载时清理全局类
})
</script>
<template>
  <div class="ia" style="position:relative">
    <TodoWidget />
    <!-- attached files -->
    <div v-if="fileDrop?.state.value.files?.length" class="ia-atts">
      <div v-for="(a, i) in fileDrop.state.value.files" :key="i" class="ia-att" :title="`${a.name}\n${fileSize(a.size)}${a.text_content ? '\ntext file' : ''}`">
        <template v-if="a.isImage && a.dataUrl">
          <img :src="a.dataUrl" :alt="a.name" class="ia-att-img" />
        </template>
        <template v-else>
          <span class="ia-att-icon">{{ fileIcon(a.name) }}</span>
        </template>
        <span class="ia-att-name">{{ a.name }}</span>
        <span class="ia-att-size">{{ fileSize(a.size) }}</span>
        <button class="ia-att-rm" @click.stop="fileDrop.removeFile(i)">&times;</button>
      </div>
      <div v-if="hasImages" class="ia-dl"><span class="ia-dl-lb">detail</span><Tooltip text="Auto (keep original)"><button class="ia-dl-btn" :class="{ on: imgDetail === '' }" @click="imgDetail = ''">Auto</button></Tooltip><Tooltip text="Low — resize to 512×512, faster &amp; fewer tokens"><button class="ia-dl-btn" :class="{ on: imgDetail === 'low' }" @click="imgDetail = 'low'">Low</button></Tooltip><Tooltip text="Original — keep full resolution"><button class="ia-dl-btn" :class="{ on: imgDetail === 'original' }" @click="imgDetail = 'original'">Orig</button></Tooltip></div>
    </div>
    <!-- toolbar -->
    <div class="ia-hint">
      <input ref="fi" type="file" multiple @change="handleFilePickerChange" style="display:none" />
      <Tooltip text="Attach file"><button class="fa-btn" @click="triggerFilePicker"><svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><path d="M22 19a2 2 0 01-2 2H4a2 2 0 01-2-2V5a2 2 0 01 2-2h5l2 3h9a2 2 0 01 2 2z"/></svg></button></Tooltip>
      <span class="fa-hint">update file</span>
      <PathWhitelist /><QuickNotes /><PermissionMode /><UserNote /><SoundSelect /><MessageSearch :on-scroll-to-index="p.onScrollToIndex" />
    </div>
    <!-- input -->
    <div class="ih">
      <div class="ih-resize" :class="{ active: resizing }" @pointerdown="onResizeDown" @pointermove="onResizeMove" @pointerup="onResizeUp" @pointercancel="onResizeUp" />
      <textarea ref="ta" :style="inputH ? { height: inputH + 'px' } : undefined" :placeholder="ph" rows="1" v-model="t" @keydown="hk" />
      <template v-if="g.streaming">
        <Tooltip text="Stop generation"><button class="btn-stop" @click="g.stopGeneration()"><svg width="14" height="14" viewBox="0 0 24 24" fill="currentColor"><rect x="6" y="6" width="12" height="12" rx="2"/></svg></button></Tooltip>
      </template>
      <template v-else>
        <button :class="['btn-s', { disabled: !hasContent }]" @click="send" :disabled="!hasContent"><svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="12" y1="19" x2="12" y2="5"/><polyline points="5 12 12 5 19 12"/></svg></button>
      </template>
    </div>
  </div>
</template>
