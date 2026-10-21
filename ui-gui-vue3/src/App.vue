<script setup lang="ts">
import { ref, onMounted, onUnmounted, watch, provide } from 'vue'
import { useGatewayStore } from './stores/gateway'
import { useChatStore } from './stores/chat'
import { useFileDrop } from './composables/useFileDrop'
import TitleBar from './components/TitleBar.vue'
import Sidebar from './components/Sidebar.vue'
import SegmentedMessageList from './components/SegmentedMessageList.vue'
import FileChangesPanel from './components/FileChangesPanel.vue'
import InputArea from './components/InputArea.vue'
import StatusBar from './components/StatusBar.vue'
import ModelConfigPanel from './components/ModelConfigPanel.vue'
import TimelineBar from './components/TimelineBar.vue'
import Backdrop from './components/Backdrop.vue'
import PermissionDialog from './components/PermissionDialog.vue'
import MemoryCenterPanel from './components/MemoryCenterPanel.vue'
import PreviewDock from './components/PreviewDock.vue'
import { usePreviewStore } from './stores/preview'
import { buildThemeColors, type ThemeColors } from './utils/themeColors'

const gateway = useGatewayStore()
const chatStore = useChatStore()
const preview = usePreviewStore()
const fileDrop = useFileDrop()
const sidebarCollapsed = ref(false)
const vlistRef = ref<InstanceType<typeof SegmentedMessageList> | null>(null)
const msgsContainerRef = ref<HTMLDivElement | null>(null)
const theme = ref('amber')
const customColors = ref<Record<string, ThemeColors>>({})

// expose fileDrop state to child components
provide('fileDrop', fileDrop)

function clearThemeVars(r: HTMLElement) {
  ;['--primary', '--accent', '--muted', '--border-gold', '--glow-start', '--glow-end'].forEach(v => r.style.removeProperty(v))
}

function applyColors(r: HTMLElement, c: ThemeColors) {
  clearThemeVars(r)
  r.setAttribute('data-theme', '')
  r.style.setProperty('--primary', c.primary)
  r.style.setProperty('--accent', c.accent)
  r.style.setProperty('--muted', c.muted)
  r.style.setProperty('--border-gold', c.borderGold)
  r.style.setProperty('--glow-start', c.glowStart)
  r.style.setProperty('--glow-end', c.glowEnd)
}

function changeTheme(id: string, hex?: string) {
  const r = document.getElementById('app')
  if (!r) return
  r.classList.add('theme-transitioning')
  if (id.startsWith('custom-')) {
    const c = hex ? buildThemeColors(hex) : customColors.value[id]
    if (hex && c) customColors.value[id] = c
    if (c) {
      applyColors(r, c)
      theme.value = id
    } else {
      theme.value = id
    }
  } else {
    clearThemeVars(r)
    r.setAttribute('data-theme', id)
    theme.value = id
  }
  setTimeout(() => r.classList.remove('theme-transitioning'), 350)
}

async function loadTheme() {
  try {
    const r = await gateway.request<{ theme: { current: string; custom: unknown[]; colors: Record<string, ThemeColors> } }>('theme.get')
    customColors.value = r?.theme?.colors && typeof r.theme.colors === 'object' ? r.theme.colors : {}
    changeTheme(r?.theme?.current || 'amber')
  } catch {
    changeTheme('amber')
  }
}

function toggleSidebar() { sidebarCollapsed.value = !sidebarCollapsed.value }
function handleScrollToMessageIndex(i: number) { vlistRef.value?.scrollToMessageIndex(i) }
function toMsg(e: Event) {
  const d = (e as CustomEvent).detail as { messageId?: number } | undefined
  if (!d?.messageId) return
  const i = gateway.messages.findIndex(m => m.messageId === d.messageId)
  if (i >= 0) vlistRef.value?.scrollToMessageIndex(i)
}

onMounted(() => {
  window.addEventListener('gateway:scroll-to-message', toMsg)
  gateway.setupConnection()
})
onUnmounted(() => { window.removeEventListener('gateway:scroll-to-message', toMsg) })
watch(() => gateway.connected, v => { if (v) { chatStore.initConversations().catch(console.error); loadTheme() } })
</script>
<template>
  <Backdrop />
  <div :class="['app', { 'sidebar-collapsed': sidebarCollapsed }]" :style="{ '--dock-w': (preview.dockOpen ? preview.dockWidth : 0) + 'px' }">
    <!-- drag overlay -->
    <Teleport to="body">
      <div v-if="fileDrop.state.value.over" class="drag-overlay">
        <div class="drag-overlay-inner">
          <svg width="48" height="48" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><path d="M21 15v4a2 2 0 01-2 2H5a2 2 0 01-2-2v-4"/><polyline points="17 8 12 3 7 8"/><line x1="12" y1="3" x2="12" y2="15"/></svg>
          <span>drop files to attach</span>
        </div>
      </div>
    </Teleport>

    <TitleBar :theme="theme" :on-theme-change="changeTheme" />
    <ModelConfigPanel />
    <Sidebar />

    <div class="sb-gutter" @click="toggleSidebar"><svg width="10" height="10" viewBox="0 0 10 10" fill="none" stroke="currentColor" stroke-width="1.5"><polyline points="3 1 7 5 3 9"/></svg></div>

    <div class="main">
      <TimelineBar :messages="gateway.messages" :container-ref="msgsContainerRef" :on-scroll-to-index="handleScrollToMessageIndex" />
      <SegmentedMessageList ref="vlistRef" :container-ref="msgsContainerRef" />

      <button v-if="gateway.messages.length > 0" class="scroll-bottom-btn" @click="vlistRef?.scrollToBottom()" title="Scroll to bottom">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="6 9 12 15 18 9"/></svg>
      </button>

      <div v-if="gateway.streaming && gateway.messages.length > 0" class="agent-loading">
        <span class="load-dot" /><span class="load-dot" /><span class="load-dot" /><span class="load-lbl">processing</span>
      </div>

      <FileChangesPanel />
      <InputArea :on-scroll-to-index="handleScrollToMessageIndex" />
    </div>

    <StatusBar />
    <PermissionDialog />
    <MemoryCenterPanel />
    <PreviewDock />

    <div class="resize-n" data-tauri-resize-edge="north" /><div class="resize-s" data-tauri-resize-edge="south" />
    <div class="resize-e" data-tauri-resize-edge="east" /><div class="resize-w" data-tauri-resize-edge="west" />
    <div class="resize-ne" data-tauri-resize-edge="ne" /><div class="resize-nw" data-tauri-resize-edge="nw" />
    <div class="resize-se" data-tauri-resize-edge="se" /><div class="resize-sw" data-tauri-resize-edge="sw" />
  </div>
</template>

<style>
.drag-overlay {
  position: fixed; inset: 0; z-index: 9999;
  background: rgba(10, 8, 14, 0.85); backdrop-filter: blur(8px);
  display: flex; align-items: center; justify-content: center;
  border: 3px dashed rgba(255, 160, 138, 0.5); border-radius: 12px; margin: 12px;
  animation: drag-in 0.15s ease;
}
.drag-overlay-inner {
  display: flex; flex-direction: column; align-items: center; gap: 16px;
  color: #ffa08a; font-size: 16px; font-weight: 500;
}
@keyframes drag-in { from { opacity: 0; } to { opacity: 1; } }
</style>
