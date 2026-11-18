<template>
<div class="stage" id="previewStage" aria-hidden="true">
  <section class="stage-window" id="stageWindow" role="dialog" aria-label="HTML preview">
    <header class="stage-bar" id="stageDrag">
      <span class="stage-grip" aria-hidden="true"><i></i><i></i><i></i><i></i><i></i><i></i></span>
      <div class="stage-files">
        <button
          v-for="entry in previewState.entries"
          :key="entry.path"
          class="stage-file"
          :class="{ on: entry.path === previewState.activePath, stale: entry.stale }"
          @click="pick(entry.path)"
        >{{ entry.title }}<span v-if="entry.stale" class="sf-pip"></span></button>
        <span v-if="!previewState.entries.length" class="stage-file" aria-disabled="true">no files</span>
      </div>
      <div class="stage-acts">
        <div class="stage-view" id="stageView">
          <button class="on" data-view="preview">Preview</button>
          <button data-view="code">Code</button>
        </div>
        <div class="stage-mode" role="group" aria-label="Preview interaction mode">
          <button
            type="button"
            class="tip"
            :class="{ on: previewState.mode === 'interact' }"
            data-tip="Interact with page"
            @click="setPreviewMode('interact')"
          >Interact</button>
          <button
            type="button"
            class="tip"
            :class="{ on: previewState.mode === 'annotate' }"
            data-tip="Click elements to comment"
            @click="setPreviewMode('annotate')"
          >
            Annotate<span v-if="previewState.comments.length" class="sm-cnt">{{ previewState.comments.length }}</span>
          </button>
        </div>
        <div class="stage-devices" id="stageDevices">
          <button :class="{ on: device === 'desktop' }" data-dev="desktop" data-tip="Desktop" @click="setDevice('desktop')">
            <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><rect x="2.5" y="4" width="19" height="13" rx="2"/><line x1="8" y1="21" x2="16" y2="21"/><line x1="12" y1="17" x2="12" y2="21"/></svg>
          </button>
          <button :class="{ on: device === 'tablet' }" data-dev="tablet" data-tip="Tablet" @click="setDevice('tablet')">
            <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><rect x="5" y="2.5" width="14" height="19" rx="2.5"/><line x1="11" y1="18.5" x2="13" y2="18.5"/></svg>
          </button>
          <button :class="{ on: device === 'phone' }" data-dev="phone" data-tip="Phone" @click="setDevice('phone')">
            <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><rect x="7" y="2" width="10" height="20" rx="2.5"/><line x1="11" y1="18" x2="13" y2="18"/></svg>
          </button>
        </div>
        <span class="stage-zoom" id="stageZoom">{{ viewport.zoom }}%</span>
        <button class="stage-btn tip" data-tip="Reload" id="stageReload">
          <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9"><path d="M21 12a9 9 0 1 1-3-6.7"/><polyline points="21 3 21 9 15 9"/></svg>
        </button>
        <button class="stage-btn tip" data-tip="Fullscreen" id="stageFull">
          <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9"><polyline points="4 9 4 4 9 4"/><polyline points="20 9 20 4 15 4"/><polyline points="4 15 4 20 9 20"/><polyline points="20 15 20 20 15 20"/></svg>
        </button>
        <button class="stage-btn tip" data-tip="Open in browser" id="stageOpenBrowser" @click="openInBrowser">
          <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9"><path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6"/><polyline points="15 3 21 3 21 9"/><line x1="10" y1="14" x2="21" y2="3"/></svg>
        </button>
        <button class="stage-x" data-stage-close aria-label="Close preview">&#215;</button>
      </div>
    </header>

    <div class="stage-stage">
      <div class="stage-paper" id="stagePaper" :class="device === 'desktop' ? '' : 'dev-' + device">
        <iframe
          v-if="previewState.srcdoc"
          id="stageFrame"
          title="preview"
          sandbox="allow-scripts"
          :srcdoc="previewState.srcdoc"
        ></iframe>
        <div v-else class="stage-empty" id="stageEmpty">
          <b>{{ previewState.loading ? 'Reading the file…' : (previewState.error || 'No preview yet') }}</b>
          <span v-if="previewState.loading">file.read_text</span>
          <span v-else-if="previewState.error">The path may sit outside the project root.</span>
          <span v-else>Nothing in this conversation has produced an HTML file.</span>
        </div>
      </div>
      <pre class="stage-code" id="stageCode">{{ previewState.code }}</pre>
    </div>

    <footer class="stage-foot">
      <span class="sf-live"><i></i>{{ previewState.mode === 'annotate' ? 'annotate mode' : 'interact mode' }}</span>
      <span v-if="previewState.mode === 'annotate'" class="sf-hint">click element to comment</span>
      <template v-if="previewState.comments.length">
        <span class="sf-notes">{{ previewState.comments.length }} {{ previewState.comments.length === 1 ? 'note' : 'notes' }}</span>
        <button type="button" class="sf-clear" @click="clearComments">Clear</button>
      </template>
      <span id="stageSize">{{ viewport.width }} × {{ viewport.height }}</span>
      <span>{{ sizeLabel }}</span>
      <span class="stage-spacer"></span>
      <button class="stage-tool tip" data-tip="Copy the generated HTML" id="stageCopy" @click="copySource">
        <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9"><rect x="9" y="9" width="12" height="12" rx="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/></svg>
        Copy HTML
      </button>
      <button class="stage-tool tip" data-tip="Save a PNG snapshot of the render" id="stagePng">
        <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9"><rect x="3" y="3" width="18" height="18" rx="2.5"/><circle cx="8.5" cy="8.5" r="1.6"/><polyline points="21 15 16 10 5 21"/></svg>
        Save PNG
      </button>
      <span class="stage-path">{{ previewState.activePath || 'no file selected' }}</span>
    </footer>
  </section>
</div>
</template>

<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { previewState, setActive, setPreviewMode, clearComments } from '../data/preview'
import { openFile, toast } from '../data/wire'

let openObserver: MutationObserver | null = null

function resetStageGeometry(): void {
  const stage = document.getElementById('previewStage')
  const win = document.getElementById('stageWindow')
  document.documentElement.scrollTop = 0
  document.body.scrollTop = 0
  if (stage) stage.scrollTop = 0
  if (!win) return
  win.scrollTop = 0
  if (win.classList.contains('full')) return
  const rawY = win.style.getPropertyValue('--stage-y').trim()
  if (rawY) {
    const parsed = Number.parseFloat(rawY)
    const maxTop = Math.max(12, window.innerHeight - win.offsetHeight - 12)
    if (!Number.isFinite(parsed) || parsed < 12 || parsed > maxTop) {
      win.style.setProperty('--stage-y', `${Math.min(Math.max(12, parsed || 92), maxTop)}px`)
    }
  }
}

onMounted(() => {
  const stage = document.getElementById('previewStage')
  if (!stage) return
  openObserver = new MutationObserver(() => {
    if (stage.classList.contains('open')) resetStageGeometry()
  })
  openObserver.observe(stage, { attributes: true, attributeFilter: ['class'] })
})

onBeforeUnmount(() => {
  openObserver?.disconnect()
  openObserver = null
})

const DEVICES: Record<string, { width: number; height: number; zoom: number }> = {
  desktop: { width: 1440, height: 900, zoom: 100 },
  tablet: { width: 768, height: 1024, zoom: 82 },
  phone: { width: 390, height: 844, zoom: 64 }
}

const device = ref('desktop')
const viewport = computed(() => DEVICES[device.value] ?? DEVICES.desktop)

const sizeLabel = computed(() => {
  const chars = previewState.code.length
  if (!chars) return 'empty'
  return chars < 1024 ? `${chars} chars` : `${(chars / 1024).toFixed(1)} KB`
})

function pick(path: string): void {
  void setActive(path)
}

function setDevice(name: string): void {
  device.value = name
}

function copySource(): void {
  const body = previewState.code
  if (!body) return
  if (navigator.clipboard?.writeText) {
    void navigator.clipboard.writeText(body).then(
      () => toast('HTML copied', `${body.length.toLocaleString()} characters on the clipboard.`, 'ok'),
      () => toast('Copy blocked', 'Clipboard needs a secure context.', 'warn')
    )
    return
  }
  toast('Copy blocked', 'Clipboard API is unavailable.', 'warn')
}

function openInBrowser(): void {
  const target = previewState.resolvedPath || previewState.activePath
  const isTauri = Boolean((window as unknown as { __TAURI_INTERNALS__?: unknown }).__TAURI_INTERNALS__)
  if (isTauri && target) {
    openFile(target)
    return
  }
  const html = previewState.code || previewState.srcdoc
  if (!html) return
  const blob = new Blob([html], { type: 'text/html' })
  const url = URL.createObjectURL(blob)
  window.open(url, '_blank', 'noopener')
  window.setTimeout(() => URL.revokeObjectURL(url), 60000)
}
</script>
