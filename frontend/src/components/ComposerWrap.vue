<template>
      <div class="composer-wrap">
        <!-- attachment tray — real files, same film styling as the prototype -->
        <div class="attach-tray" v-if="files.length">
          <div class="film-head">
            <span class="film-tag">
              <svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9"><path d="M21.44 11.05l-9.19 9.19a6 6 0 0 1-8.49-8.49l9.19-9.19a4 4 0 0 1 5.66 5.66l-9.2 9.19a2 2 0 0 1-2.83-2.83l8.49-8.48"/></svg>
              attached · {{ files.length }}
            </span>
            <div class="film-detail" v-if="hasImages">
              <span class="fd-lbl">detail</span>
              <button
                v-for="opt in DETAILS"
                :key="opt.id"
                :class="{ on: activeDetail === opt.id }"
                @click="setImageDetail(opt.id)"
              >{{ opt.label }}</button>
            </div>
            <span class="film-err" v-if="fileDrop.error">{{ fileDrop.error }}</span>
            <button class="film-clear" @click="clearFiles()">Clear</button>
          </div>

          <div class="film-strip">
            <figure
              class="film"
              v-for="(f, i) in files"
              :key="f.key"
              :style="{ '--i': i, '--rot': (i % 2 ? .6 : -.7) + 'deg' }"
            >
              <span class="film-tape"></span>
              <span class="film-shot" :class="f.isImage ? 'shot-img' : 'shot-code'">
                <img v-if="f.isImage && f.dataUrl" class="film-thumb" :src="f.dataUrl" :alt="f.name" />
                <svg v-else-if="f.isImage" width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6"><rect x="3" y="3" width="18" height="18" rx="3"/><circle cx="8.5" cy="8.5" r="1.6"/><polyline points="21 15 16 10 5 21"/></svg>
                <svg v-else width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7"><polyline points="16 18 22 12 16 6"/><polyline points="8 6 2 12 8 18"/></svg>
                <span class="fs-tag">{{ fileIcon(f.name) }}</span>
              </span>
              <figcaption class="film-meta">
                <span class="film-name">{{ f.name }}</span>
                <span class="film-size">{{ fileSize(f.size) }} · {{ f.isImage ? 'image' : (f.text_content ? 'text' : 'binary') }}</span>
              </figcaption>
              <button class="film-x" :data-tip="'Remove ' + f.name" @click="removeFile(i)">
                <svg width="9" height="9" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>
              </button>
              <span class="film-perf"></span>
            </figure>
            <span class="film-hint">drop files anywhere</span>
          </div>
        </div>

        <div class="composer">
          <div class="comp-ribbon">
            <span class="cr-label">context</span>
            <span class="cr-track"><i id="crFill"></i></span>
            <span class="cr-val" id="crVal">~48 tok</span>
          </div>
          <div class="composer-input">
            <span class="ta-pulse" id="taPulse"><i></i><i></i><i></i></span>

            <textarea id="composerTa" rows="1" placeholder="Tell Pebble what to build…  (⏎ send · ⇧⏎ newline)"></textarea>
            <button class="send-btn go" v-show="!streaming" @click="send">
              <span>Send</span>
              <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4"><line x1="4" y1="12" x2="19" y2="12"/><polyline points="13 6 19 12 13 18"/></svg>
            </button>
            <button class="send-btn stop tip tip-left" data-tip="Stop generation" v-show="streaming" @click="stop">
              <svg width="13" height="13" viewBox="0 0 24 24" fill="currentColor"><rect x="6" y="6" width="12" height="12" rx="2.5"/></svg>
            </button>
          </div>

          <div class="composer-tools">
            <button class="tool-btn tip" data-tip="Attach file" @click="pickFiles">
              <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7"><path d="M21.44 11.05l-9.19 9.19a6 6 0 0 1-8.49-8.49l9.19-9.19a4 4 0 0 1 5.66 5.66l-9.2 9.19a2 2 0 0 1-2.83-2.83l8.49-8.48"/></svg>
              Attach
            </button>
            <input ref="fileInput" class="attach-input" type="file" multiple @change="onPicked" />
            <div class="perm-wrap" id="permWrap" data-mode="manual">
            <button class="tool-btn on tip" data-tip="Permission mode" id="permModeBtn" aria-expanded="false">
              <svg class="pm-ico pmi-manual" width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7"><path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/><polyline points="9 12 11 14 15 10"/></svg>
              <svg class="pm-ico pmi-auto" width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7"><rect x="4" y="10.5" width="16" height="10" rx="2"/><path d="M7.5 10.5V7a4.5 4.5 0 0 1 9 0v3.5"/></svg>
              <svg class="pm-ico pmi-readonly" width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7"><path d="M2 12s3.6-6.5 10-6.5S22 12 22 12s-3.6 6.5-10 6.5S2 12 2 12z"/><circle cx="12" cy="12" r="2.6"/></svg>
              <span id="pmLabel">Ask first</span>
              <svg class="pm-caret" width="8" height="8" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2"><polyline points="6 15 12 9 18 15"/></svg>
            </button>
              <div class="pm-shell" id="pmShell" aria-hidden="true">
                <div class="pm-inner">
                  <div class="pm-card">
                    <div class="pm-hdr">
                      <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7"><path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/></svg>
                      <span class="pm-title">Permission mode</span>
                      <span class="pm-cur" id="pmCur">Manual</span>
                    </div>
                    <div class="pm-body">
                      <button class="pm-opt active" type="button" data-mode="manual">
                        <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7"><path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/><polyline points="9 12 11 14 15 10"/></svg>
                        <span class="pm-opt-info"><span class="pm-opt-label">Manual</span><span class="pm-opt-desc">Check rules; ask when needed</span></span>
                        <span class="pm-check"><svg width="9" height="9" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3.4"><polyline points="20 6 9 17 4 12"/></svg></span>
                      </button>
                      <button class="pm-opt" type="button" data-mode="auto">
                        <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7"><rect x="4" y="10.5" width="16" height="10" rx="2"/><path d="M7.5 10.5V7a4.5 4.5 0 0 1 9 0v3.5"/></svg>
                        <span class="pm-opt-info"><span class="pm-opt-label">Auto</span><span class="pm-opt-desc">Approve every tool call</span></span>
                        <span class="pm-check"><svg width="9" height="9" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3.4"><polyline points="20 6 9 17 4 12"/></svg></span>
                      </button>
                      <button class="pm-opt" type="button" data-mode="readonly">
                        <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7"><path d="M2 12s3.6-6.5 10-6.5S22 12 22 12s-3.6 6.5-10 6.5S2 12 2 12z"/><circle cx="12" cy="12" r="2.6"/></svg>
                        <span class="pm-opt-info"><span class="pm-opt-label">Read-only</span><span class="pm-opt-desc">Allow reads and lookups; reject writes, commands and secret files</span></span>
                        <span class="pm-check"><svg width="9" height="9" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3.4"><polyline points="20 6 9 17 4 12"/></svg></span>
                      </button>
                    </div>
                    <div class="pm-hint" id="pmHint">Risky tool calls pause for your approval.</div>
                  </div>
                </div>
              </div>
            </div>
            <span class="tools-spacer"></span>
            <span class="token-est">~ 48 tok · ⏎ send · ⇧⏎ newline</span>
          </div>
        </div>
      </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { streamState } from '../data/stream'
import { sendPrompt, interruptSession } from '../data/wire'
import {
  fileDrop,
  fileIcon,
  fileSize,
  ingest,
  removeFile,
  clearFiles,
  setImageDetail,
  installDropZone,
  wireAttachments,
  type ImageDetail
} from '../data/fileDrop'

const DETAILS: Array<{ id: ImageDetail; label: string }> = [
  { id: 'auto', label: 'Auto' },
  { id: 'low', label: 'Low' },
  { id: 'orig', label: 'Orig' }
]

const files = computed(() => fileDrop.files)
const hasImages = computed(() => fileDrop.files.some(f => f.isImage))
/* The tray is one batch, so the chip row reflects whichever detail the images
   currently carry; a mixed batch shows nothing selected rather than lying. */
const activeDetail = computed<ImageDetail | null>(() => {
  const imageDetails = fileDrop.files.filter(f => f.isImage).map(f => f.detail)
  if (!imageDetails.length) return null
  const first = imageDetails[0]
  return imageDetails.every(d => d === first) ? first : null
})

const fileInput = ref<HTMLInputElement | null>(null)

const streaming = computed(() => streamState.streaming)

/* The composer owns the two buttons now. The runtime script used to toggle
   `hidden` from a local `running` flag that started at true, which is why the
   stop icon showed, and its ring pulsed, before the agent had done anything. */
function send(): void {
  const ta = document.getElementById('composerTa') as HTMLTextAreaElement | null
  if (!ta) return
  const text = ta.value.trim()
  const atts = wireAttachments()
  if (!text && !atts.length) return
  if (!sendPrompt(text, atts)) return
  ta.value = ''
  ta.dispatchEvent(new Event('input'))
  clearFiles()
}

function stop(): void {
  interruptSession()
}

function pickFiles(): void {
  fileInput.value?.click()
}

function onPicked(e: Event): void {
  const input = e.target as HTMLInputElement
  const picked = Array.from(input.files ?? [])
  if (picked.length) void ingest(picked)
  // Reset so picking the same file twice still fires `change`.
  input.value = ''
}

onMounted(() => { installDropZone() })
</script>
