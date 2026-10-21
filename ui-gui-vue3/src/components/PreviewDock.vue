<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import HtmlPreview from './HtmlPreview.vue'
import Tooltip from './Tooltip.vue'
import { useGatewayStore } from '../stores/gateway'
import { useCommentStore } from '../stores/comment'
import { normalizePreviewPath, usePreviewStore } from '../stores/preview'

const g = useGatewayStore()
const pv = usePreviewStore()
const cs = useCommentStore()

// Same directive the agent writes on first file creation; keep in sync with
// utils/contentParser.ts.
const PEBBLE_FILE_RE = /@pebble-file:\s*([\w.\-/]+)/g

/**
 * Signature of every previewable file mentioned in the conversation. Joining the
 * paths keeps the computed value stable while streaming, so the watcher only
 * fires when a genuinely new file appears instead of on every token.
 */
const discoveredKey = computed(() => {
  const seen = new Set<string>()
  const scan = (content?: string): void => {
    if (!content || !content.includes('@pebble-file')) return
    PEBBLE_FILE_RE.lastIndex = 0
    let m: RegExpExecArray | null
    while ((m = PEBBLE_FILE_RE.exec(content)) !== null) {
      const filePath = normalizePreviewPath(m[1])
      if (filePath) seen.add(filePath)
    }
  }
  for (const msg of g.messages) scan(msg.content)
  scan(g.streamingMessage?.content)
  return Array.from(seen).join('\n')
})

let hydrated = false
watch(
  discoveredKey,
  (key) => {
    const list = key ? key.split('\n').filter(Boolean) : []
    const fresh: string[] = []
    for (const filePath of list) if (pv.register(filePath)) fresh.push(filePath)

    if (!hydrated) {
      // An empty scan means history has not arrived yet; consuming the hydration
      // flag here would make the real first scan look like a brand new file.
      if (list.length === 0) return
      hydrated = true
      // Re-scan of existing history (page reload / conversation switch): never
      // pop the dock open on its own, only seed the active preview if unset.
      if (!pv.activePath) pv.setActive(list[list.length - 1])
      return
    }
    if (fresh.length > 0) pv.setActive(fresh[fresh.length - 1])
    // Switching conversations replaces the whole message list; keep the active
    // preview inside the files this conversation actually mentions.
    else if (pv.activePath && !list.includes(pv.activePath) && list.length > 0)
      pv.setActive(list[list.length - 1])
  },
  { immediate: true },
)

// Comments are anchored to element selectors of one specific document; keeping
// them across a file switch would submit A's remarks while looking at B.
watch(
  () => pv.activePath,
  (next, prev) => {
    if (prev && next !== prev) cs.clearComments()
  },
)

function onFileChange(e: Event): void {
  const detail = (e as CustomEvent).detail as { path?: string } | undefined
  if (!detail?.path) return
  pv.markChanged(detail.path)
}

onMounted(() => window.addEventListener('gateway:file-change', onFileChange as EventListener))
onUnmounted(() => window.removeEventListener('gateway:file-change', onFileChange as EventListener))

// Remount key: remounting the component is the only way to force a fresh read.
const reloadKey = ref(0)
function reload(): void {
  reloadKey.value += 1
}

const resizing = ref(false)
function startResize(e: MouseEvent): void {
  if (e.button !== 0) return
  e.preventDefault()
  const startX = e.clientX
  const startWidth = pv.dockWidth
  resizing.value = true
  const onMove = (ev: MouseEvent): void => {
    pv.setDockWidth(startWidth - (ev.clientX - startX))
  }
  const onUp = (): void => {
    resizing.value = false
    window.removeEventListener('mousemove', onMove)
    window.removeEventListener('mouseup', onUp)
    document.body.style.cursor = ''
    document.body.style.userSelect = ''
  }
  window.addEventListener('mousemove', onMove)
  window.addEventListener('mouseup', onUp)
  document.body.style.cursor = 'col-resize'
  document.body.style.userSelect = 'none'
}
</script>

<template>
  <aside
    v-if="pv.dockOpen"
    class="pvd"
    :class="{ 'is-resizing': resizing }"
    :style="{ width: pv.dockWidth + 'px' }"
  >
    <div class="pvd-resize" @mousedown="startResize" />

    <header class="pvd-hdr">
      <div class="pvd-tabs">
        <button
          v-for="entry in pv.entries"
          :key="entry.filePath"
          class="pvd-tab"
          :class="{ active: entry.filePath === pv.activePath }"
          @click="pv.setActive(entry.filePath)"
        >
          <span v-if="entry.stale" class="pvd-dot" />
          <span class="pvd-tab-title">{{ entry.title }}</span>
        </button>
      </div>
      <div class="pvd-acts">
        <Tooltip text="Reload" position="bottom">
          <button class="pvd-btn" @click="reload">
            <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><path d="M21 12a9 9 0 1 1-3-6.7"/><polyline points="21 3 21 9 15 9"/></svg>
          </button>
        </Tooltip>
        <Tooltip text="Collapse" position="bottom">
          <button class="pvd-btn" @click="pv.closeDock()">
            <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><polyline points="9 6 15 12 9 18"/></svg>
          </button>
        </Tooltip>
      </div>
    </header>

    <div class="pvd-body">
      <HtmlPreview
        v-if="pv.activePath"
        :key="reloadKey + '|' + pv.activePath"
        :file-path="pv.activePath"
        dock-mode
      />
      <div v-else class="pvd-empty">No preview yet</div>
    </div>
  </aside>

  <button v-else class="pvd-rail" @click="pv.openDock()">
    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6"><rect x="2" y="3" width="20" height="14" rx="2"/><line x1="8" y1="21" x2="16" y2="21"/><line x1="12" y1="17" x2="12" y2="21"/></svg>
    <span v-if="pv.staleCount > 0" class="pvd-rail-dot" />
  </button>
</template>

<style scoped>
.pvd {
  position: fixed;
  top: var(--titlebar-height);
  bottom: var(--statusbar-height);
  right: 0;
  z-index: 25;
  display: flex;
  flex-direction: column;
  background: var(--bg-surface);
  border-left: 1px solid var(--border-subtle);
  box-shadow: -12px 0 32px rgba(0, 0, 0, 0.28);
  min-width: 360px;
}
.pvd.is-resizing { transition: none; }

.pvd-resize {
  position: absolute;
  left: -3px;
  top: 0;
  bottom: 0;
  width: 6px;
  cursor: col-resize;
  z-index: 2;
}
.pvd-resize:hover { background: color-mix(in srgb, var(--accent) 35%, transparent); }

.pvd-hdr {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 6px 8px;
  border-bottom: 1px solid var(--border-subtle);
  background: var(--bg-card);
  flex-shrink: 0;
}
.pvd-tabs {
  display: flex;
  align-items: center;
  gap: 4px;
  overflow-x: auto;
  overflow-y: hidden;
  flex: 1;
  min-width: 0;
  scrollbar-width: none;
}
.pvd-tabs::-webkit-scrollbar { display: none; }
.pvd-tab {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  padding: 4px 9px;
  border: 1px solid transparent;
  border-radius: 6px;
  background: transparent;
  color: var(--text-body);
  font-family: var(--font-mono);
  font-size: 11px;
  white-space: nowrap;
  cursor: pointer;
  transition: background 0.15s, color 0.15s, border-color 0.15s;
  flex-shrink: 0;
}
.pvd-tab:hover { background: var(--bg-hover); color: var(--text); }
.pvd-tab.active {
  background: color-mix(in srgb, var(--accent) 14%, transparent);
  border-color: color-mix(in srgb, var(--accent) 45%, transparent);
  color: var(--accent);
}
.pvd-tab-title { max-width: 150px; overflow: hidden; text-overflow: ellipsis; }
.pvd-dot {
  width: 5px;
  height: 5px;
  border-radius: 50%;
  background: var(--accent);
  flex-shrink: 0;
}
.pvd-acts { display: flex; align-items: center; gap: 2px; flex-shrink: 0; }
.pvd-btn {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 22px;
  height: 22px;
  border: none;
  border-radius: 5px;
  background: transparent;
  color: var(--text-body);
  cursor: pointer;
  transition: background 0.15s, color 0.15s;
}
.pvd-btn:hover { background: var(--bg-hover); color: var(--accent); }

.pvd-body {
  flex: 1;
  min-height: 0;
  display: flex;
  flex-direction: column;
  overflow: hidden;
}
.pvd-empty {
  flex: 1;
  display: flex;
  align-items: center;
  justify-content: center;
  color: var(--text-body);
  font-family: var(--font-mono);
  font-size: 12px;
}

/* The preview card keeps its own chrome in the message flow; inside the dock it
   becomes a plain full-height surface. */
.pvd-body :deep(.ht-card) {
  margin-top: 0;
  padding: 0;
  height: 100%;
  display: flex;
  flex-direction: column;
  background: none;
  border-radius: 0;
}
.pvd-body :deep(.ht-card:hover) { background: none; }
.pvd-body :deep(.ht-inner) {
  flex: 1;
  min-height: 0;
  display: flex;
  flex-direction: column;
  border-radius: 0;
}
.pvd-body :deep(.ht-body) {
  flex: 1;
  min-height: 0;
  display: flex;
  flex-direction: column;
  border-top: none;
}
.pvd-body :deep(.ht-preview-wrapper) { flex: 1; height: 100%; min-height: 0; }
/* The preview bridge forces `body { min-width: 800px }` and `overflow: clip`
   inside the frame, so the iframe itself never scrolls — it clips. The frame
   must therefore be at least as wide as that floor and the scroll container
   owns both axes. */
.pvd-body :deep(.ht-frame) { min-width: 800px; min-height: 400px; height: auto; }
.pvd-body :deep(.ht-frame-scroll) { flex: 1; overflow: auto; }
.pvd-body :deep(.ht-frame-scroll)::-webkit-scrollbar { width: 9px; height: 9px; }
.pvd-body :deep(.ht-frame-scroll)::-webkit-scrollbar-thumb {
  background: var(--border-hover);
  border-radius: 5px;
}
.pvd-body :deep(.ht-code) { flex: 1; overflow: auto; min-height: 0; }

/* Collapsed state: a small fixed button on the right edge. It is a direct v-else
   sibling of the dock, so it must carry its own fixed positioning. */
.pvd-rail {
  position: fixed;
  right: 12px;
  top: 50%;
  transform: translateY(-50%);
  z-index: 26;
  display: flex;
  align-items: center;
  justify-content: center;
  width: 32px;
  height: 32px;
  padding: 0;
  border: 1px solid var(--border-subtle);
  border-radius: 8px;
  background: var(--bg-surface);
  color: var(--text-body);
  cursor: pointer;
  box-shadow: 0 2px 10px rgba(0, 0, 0, 0.25);
  transition: background 0.15s, color 0.15s, border-color 0.15s;
}
.pvd-rail:hover {
  background: var(--bg-hover);
  color: var(--accent);
  border-color: color-mix(in srgb, var(--accent) 45%, transparent);
}
.pvd-rail-dot {
  position: absolute;
  top: 4px;
  right: 4px;
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: var(--accent);
  box-shadow: 0 0 0 2px var(--bg-surface);
}
</style>
