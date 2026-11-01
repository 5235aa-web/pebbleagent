<template>
  <div class="mmd svgc" :class="{ 'is-error': state === 'error', 'is-live': streaming }">
    <header class="mmd-bar">
      <span class="mmd-mark">
        <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8">
          <rect x="3" y="3" width="18" height="18" rx="2.5" />
          <circle cx="8.5" cy="8.5" r="1.6" />
          <path d="M21 15.5l-4.4-4.4a1.4 1.4 0 0 0-2 0L4.5 21" />
        </svg>
      </span>
      <span class="mmd-kind">svg</span>
      <span class="mmd-dot"></span>
      <span class="mmd-st" :class="state">{{ statusLabel }}</span>
      <span class="mmd-spacer"></span>
      <button class="mmd-act" type="button" @click="showSource = !showSource">{{ showSource ? 'Hide source' : 'Source' }}</button>
      <button class="mmd-act" type="button" @click="copySource">{{ copied ? 'Copied' : 'Copy' }}</button>
      <button class="mmd-act" type="button" :disabled="state !== 'success'" @click="savePng">PNG</button>
    </header>

    <div class="mmd-stage">
      <div v-if="streaming" class="mmd-wait">
        <span class="mmd-pulse"></span>
        <span>drawing the graphic</span>
      </div>
      <div v-else-if="state === 'error'" class="mmd-fail">
        <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8">
          <circle cx="12" cy="12" r="9" />
          <line x1="12" y1="8" x2="12" y2="13" />
          <line x1="12" y1="16.5" x2="12" y2="16.5" />
        </svg>
        <span>{{ errorText }}</span>
      </div>
      <div v-else-if="state === 'success'" class="mmd-svg">
        <img :src="dataUri" alt="SVG graphic" @error="onImageError" />
      </div>
    </div>

    <pre v-if="showSource" class="mmd-src">{{ code }}</pre>
  </div>
</template>

<script setup lang="ts">
import { computed, ref, watch } from 'vue'

const props = defineProps<{ code: string; streaming?: boolean }>()

/* The graphic is handed to the browser as an <img>, never as live DOM: an image
   document does not run scripts or resolve outside references, so a reply can
   draw anything it likes without gaining a way into the shell. The source is
   sanitised first anyway — img is the second lock, not the only one. */

const state = ref<'idle' | 'loading' | 'success' | 'error'>('idle')
const dataUri = ref('')
const errorText = ref('')
const showSource = ref(false)
const copied = ref(false)

const statusLabel = computed(() => {
  if (props.streaming) return 'streaming'
  if (state.value === 'loading') return 'preparing'
  if (state.value === 'error') return 'render failed'
  if (state.value === 'success') return 'ready'
  return 'idle'
})

const SVG_NS = 'http://www.w3.org/2000/svg'
/* Attributes that survive are the ones that only describe paint. Anything that
   can execute, phone home, or smuggle another document is dropped, and a
   fragment without a namespace is given one so `<img>` will recognise it. */
const DROP_TAGS = new Set(['script', 'foreignobject', 'iframe', 'audio', 'video', 'use'])
const REMOTE = /^(?:https?:|javascript:|data:text\/html)/i

function prepare(raw: string): { uri: string; error: string } {
  const source = raw.trim()
  if (!source) return { uri: '', error: 'the block is empty' }
  let doc: Document
  try {
    doc = new DOMParser().parseFromString(source, 'image/svg+xml')
  } catch {
    return { uri: '', error: 'the source is not valid XML' }
  }
  if (doc.getElementsByTagName('parsererror').length) {
    return { uri: '', error: 'the source is not valid XML' }
  }
  const root = doc.documentElement
  if (!root || root.nodeName.toLowerCase() !== 'svg') {
    return { uri: '', error: 'the block does not start with <svg>' }
  }

  for (const el of [...root.querySelectorAll('*')]) {
    if (DROP_TAGS.has(el.localName.toLowerCase())) el.remove()
  }
  for (const el of [root, ...root.querySelectorAll('*')]) {
    for (const attr of [...el.attributes]) {
      const name = attr.name.toLowerCase()
      const value = attr.value.trim()
      if (name.startsWith('on')) el.removeAttribute(attr.name)
      else if (name === 'href' || name === 'xlink:href' || name === 'src') {
        if (REMOTE.test(value)) el.removeAttribute(attr.name)
      }
    }
  }
  if (!root.getAttribute('xmlns')) root.setAttribute('xmlns', SVG_NS)

  const xml = new XMLSerializer().serializeToString(root)
  return { uri: 'data:image/svg+xml;charset=utf-8,' + encodeURIComponent(xml), error: '' }
}

watch(
  () => [props.code, props.streaming] as const,
  ([code, streaming]) => {
    if (streaming) {
      state.value = 'loading'
      errorText.value = ''
      return
    }
    const { uri, error } = prepare(code)
    if (error) {
      state.value = 'error'
      errorText.value = error
      dataUri.value = ''
      return
    }
    if (uri !== dataUri.value) dataUri.value = uri
    state.value = 'success'
  },
  { immediate: true }
)

/* An image document can still fail after a clean parse — an unsupported filter,
   a broken transform. The browser is the last word on whether it draws. */
function onImageError() {
  state.value = 'error'
  errorText.value = 'the browser could not draw this SVG'
}

function copySource() {
  void navigator.clipboard.writeText(props.code)
    .then(() => {
      copied.value = true
      window.setTimeout(() => { copied.value = false }, 1600)
    })
    .catch(() => { /* the source stays selectable when the clipboard refuses */ })
}

async function savePng() {
  const uri = dataUri.value
  if (!uri) return
  try {
    const img = new Image()
    await new Promise<void>((resolve, reject) => {
      img.onload = () => resolve()
      img.onerror = () => reject(new Error('image load failed'))
      img.src = uri
    })
    const w = img.naturalWidth || 1024
    const h = img.naturalHeight || 1024
    const scale = Math.min(2, 2048 / Math.max(w, h))
    const cv = document.createElement('canvas')
    cv.width = Math.round(w * scale)
    cv.height = Math.round(h * scale)
    const ctx = cv.getContext('2d')
    if (!ctx) return
    ctx.drawImage(img, 0, 0, cv.width, cv.height)
    const a = document.createElement('a')
    a.download = 'graphic-' + Date.now() + '.png'
    a.href = cv.toDataURL('image/png')
    a.click()
  } catch { /* keep the card as it is */ }
}
</script>
