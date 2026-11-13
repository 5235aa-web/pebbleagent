<template>
  <section class="pp-pv" aria-label="File preview">
    <header class="pp-pv-h">
      <span class="pp-pv-ico" v-html="icon"></span>
      <div class="pp-pv-t">
        <b>{{ name }}</b>
        <small>{{ subtitle }}</small>
      </div>
      <button
        class="pp-pv-act tip tip-right"
        type="button"
        aria-label="Open with the system app"
        data-tip="Open with the system app"
        @click="openExternally"
      >
        <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6"/><polyline points="15 3 21 3 21 9"/><line x1="10" y1="14" x2="21" y2="3"/></svg>
      </button>
      <button class="pp-pv-act" type="button" aria-label="Close preview" @click="emit('close')">
        <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>
      </button>
    </header>

    <div class="pp-pv-body">
      <div v-if="loading" class="pp-pv-note">loading…</div>
      <div v-else-if="error" class="pp-pv-note err">{{ error }}</div>

      <div v-else-if="isImage" class="pp-pv-img">
        <img v-if="dataUrl" :src="dataUrl" :alt="name" />
        <span v-else class="pp-pv-note err">{{ imageError || 'could not read this image' }}</span>
      </div>

      <div v-else-if="peek.binary" class="pp-pv-note">
        <span>binary file{{ sizeLabel ? ' · ' + sizeLabel : '' }}</span>
        <button class="pp-pv-link" type="button" @click="openExternally">open it outside</button>
      </div>

      <div v-else-if="!peek.text" class="pp-pv-note">empty file</div>

      <div v-else class="pp-pv-code" @click="onBodyClick" v-html="rendered"></div>
    </div>

    <footer v-if="peek.truncated" class="pp-pv-foot">
      showing the first {{ limitLabel }} of {{ sizeLabel }}
      <button class="pp-pv-link" type="button" @click="openExternally">open it outside</button>
    </footer>
  </section>
</template>

<script setup lang="ts">
import { computed, onUnmounted, ref, watch } from 'vue'
import {
  FILE_PEEK_BYTES, IMAGE_MIME, isImagePath, openFile, readFileBase64, readFileText, toast
} from '../data/wire'
import { fileGlyph } from '../data/fileicons'
import { fileSize, guessLang } from '../data/github'
import { renderCodeBlock } from '../data/render'

/* One file, read only when this component is on screen. The pane is mounted by
   a `v-if` in ProjectSwitcher, so closing the popover unmounts it and the text
   leaves memory with it — that is the whole "no cost until opened" contract. */

const props = defineProps<{ path: string }>()
const emit = defineEmits<{ (e: 'close'): void }>()

const name = computed(() => props.path.split(/[\\/]/).pop() || props.path)
const isImage = computed(() => isImagePath(props.path))
const icon = computed(() => fileGlyph(name.value, 'file'))

const loading = ref(false)
const error = ref('')
const peek = ref({ text: '', size: 0, binary: false, truncated: false })
const dataUrl = ref('')
const imageError = ref('')

const sizeLabel = computed(() => (peek.value.size ? fileSize(peek.value.size) : ''))
const limitLabel = computed(() => fileSize(FILE_PEEK_BYTES))
const subtitle = computed(() => {
  if (isImage.value) return 'image'
  if (peek.value.binary) return 'binary'
  return sizeLabel.value
})

/* Clicking down a list sends a read per row, and they can answer out of order.
   A late answer for a file that is no longer selected would replace the pane
   with the wrong content, so every load takes a ticket. */
let seq = 0

watch(() => props.path, path => { void load(path) }, { immediate: true })

async function load(path: string): Promise<void> {
  const mine = ++seq
  loading.value = true
  error.value = ''
  imageError.value = ''
  dataUrl.value = ''
  peek.value = { text: '', size: 0, binary: false, truncated: false }
  try {
    if (isImagePath(path)) {
      const res = await readFileBase64(path)
      if (mine !== seq) return
      if (!res.data) {
        imageError.value = res.error || 'could not read this image'
        return
      }
      const ext = path.split('.').pop()?.toLowerCase() ?? 'png'
      dataUrl.value = `data:${IMAGE_MIME[ext] || 'image/png'};base64,${res.data}`
      peek.value = { text: '', size: res.size, binary: false, truncated: false }
      return
    }
    const res = await readFileText(path)
    if (mine !== seq) return
    if (!res.ok) {
      error.value = res.error || 'could not read this file'
      return
    }
    peek.value = { text: res.text, size: res.size, binary: res.binary, truncated: res.truncated }
  } finally {
    if (mine === seq) loading.value = false
  }
}

const rendered = computed(() => {
  const body = peek.value.text
  if (!body || peek.value.binary) return ''
  return renderCodeBlock(guessLang(props.path), body)
})

/* The copy control is part of the renderer's markup, so the click is delegated
   exactly as GithubPane does it. */
function onBodyClick(e: MouseEvent): void {
  const target = e.target as HTMLElement | null
  if (!target?.closest('[data-copy]')) return
  const text = peek.value.text
  if (!text) return
  void navigator.clipboard.writeText(text)
  toast('Copied', 'file contents on the clipboard', 'ok')
}

function openExternally(): void {
  openFile(props.path)
}

/* A read still in flight when the pane goes away would otherwise resolve into
   a component that no longer exists. Bumping the ticket makes its answer land
   on nothing. */
onUnmounted(() => { seq += 1 })
</script>
