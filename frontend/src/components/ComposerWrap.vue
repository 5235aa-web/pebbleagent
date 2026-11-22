<template>
      <div class="composer-wrap">
        <!-- Batch forward bar above composer -->
        <div class="forward-bar" v-if="forwardState.active">
          <div class="fb-left">
            <span class="fb-badge">Forward</span>
            <span class="fb-count"><b>{{ selectedCount }}</b> selected</span>
            <button
              v-if="selectableTotal > 0"
              class="fb-link"
              type="button"
              @click="toggleSelectAll"
            >{{ allSelected ? 'Clear' : 'Select all' }}</button>
          </div>
          <div class="fb-actions">
            <button class="btn btn-ghost btn-sm" type="button" @click="cancelForward">Cancel</button>
            <button
              class="btn btn-primary btn-sm"
              type="button"
              :disabled="selectedCount === 0 || forwardState.submitting"
              @click="openForwardDialog"
            >Forward</button>
          </div>
        </div>

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

            <textarea id="composerTa" rows="1" placeholder="Tell Pebble what to build…"></textarea>
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
            <button
              class="tool-btn tip"
              :class="{ on: forwardState.active }"
              data-tip="Select messages to forward to another session"
              type="button"
              :disabled="!canEnterForward"
              @click="onToggleForward"
            >
              <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7"><polyline points="15 14 20 9 15 4"/><path d="M4 20v-7a4 4 0 0 1 4-4h12"/></svg>
              Forward
            </button>
            <div class="model-wrap" ref="modelWrapEl" :class="{ open: modelMenuOpen }">
              <button
                class="tool-btn model-btn"
                :class="{ on: Boolean(activeModelName), tip: !modelMenuOpen }"
                :data-tip="modelBtnTip"
                type="button"
                :aria-expanded="modelMenuOpen ? 'true' : 'false'"
                @click="toggleModelMenu"
              >
                <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7"><rect x="4" y="4" width="16" height="16" rx="3"/><rect x="9" y="9" width="6" height="6" rx="1"/></svg>
                <span class="mm-btn-label">{{ activeModelName || 'Model' }}</span>
                <span v-if="activeModelName" class="mm-btn-effort" :class="{ off: activeThinkingLevel === 'off' }">{{ thinkingShortLabel(activeThinkingLevel) }}</span>
                <svg class="pm-caret" width="8" height="8" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2"><polyline points="6 15 12 9 18 15"/></svg>
              </button>

              <div ref="modelShellEl" class="mm-shell" :class="{ open: modelMenuOpen }" :aria-hidden="modelMenuOpen ? 'false' : 'true'">
                <div class="mm-inner">
                  <div class="mm-card">
                    <div class="mm-body" v-if="llm.providers.length" @scroll="clearHoverPopover">
                      <div
                        v-for="(p, idx) in llm.providers"
                        :key="p.slug || `${p.name}-${idx}`"
                        class="mm-group"
                        :class="{ current: idx === activeProviderIndex, unready: !isProviderReady(p) }"
                      >
                        <div class="mm-prov">
                          <span class="mm-prov-badge">{{ p.name.slice(0, 1).toUpperCase() }}</span>
                          <span class="mm-prov-name">{{ p.name }}</span>
                          <span v-if="!isProviderReady(p)" class="mm-prov-tag">Not ready</span>
                        </div>

                        <div v-if="p.models.length" class="mm-models">
                          <button
                            v-for="m in p.models"
                            :key="`${idx}-${m}`"
                            class="mm-opt"
                            :class="{
                              active: isCurrentModel(idx, m),
                              disabled: !isProviderReady(p),
                              hovered: hoveredTarget?.idx === idx && hoveredTarget?.model === m
                            }"
                            type="button"
                            :disabled="switchingModel"
                            @click="pickModel(idx, m)"
                            @mouseenter="onModelHover(idx, m, $event)"
                            @mouseleave="scheduleHoverClose"
                          >
                            <span class="mm-opt-dot"></span>
                            <span class="mm-opt-label">{{ m }}</span>
                            <span
                              class="mm-opt-effort"
                              :class="{ off: modelThinkingLevel(p, m) === 'off' }"
                              @click.stop="toggleHoverPopover(idx, m, $event)"
                            >
                              <span>{{ thinkingShortLabel(modelThinkingLevel(p, m)) }}</span>
                              <svg width="8" height="8" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2"><polyline points="9 6 15 12 9 18"/></svg>
                            </span>
                          </button>
                        </div>
                        <div v-else class="mm-empty-models">No models yet — add one in LLM Panel</div>
                      </div>
                    </div>
                    <div class="mm-empty" v-else>
                      <span>No model providers configured</span>
                      <button class="btn btn-primary btn-sm" type="button" @click="openLlmDock">Open LLM Panel</button>
                    </div>
                  </div>
                </div>

                <div
                  v-if="modelMenuOpen && hoveredTarget"
                  class="mm-sub"
                  :class="{ flip: hoveredTarget.flipLeft }"
                  :style="{ top: hoveredTarget.top + 'px' }"
                  @mouseenter="cancelHoverClose"
                  @mouseleave="scheduleHoverClose"
                >
                  <div class="mm-sub-card">
                    <div class="mm-sub-body" role="menu" :aria-label="`Thinking effort for ${hoveredTarget.model}`">
                      <button
                        v-for="lvl in THINKING_LEVELS"
                        :key="lvl.key"
                        class="mm-sub-opt"
                        :class="{ on: hoveredThinkingLevel === lvl.key, off: lvl.key === 'off' }"
                        type="button"
                        :disabled="switchingModel"
                        @click.stop="pickModelEffort(hoveredTarget.idx, hoveredTarget.model, lvl.key)"
                      >
                        <span class="mm-sub-label">{{ lvl.key === 'medium' ? 'medium' : lvl.key }}</span>
                      </button>
                    </div>
                  </div>
                </div>
              </div>
            </div>
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
          </div>
        </div>
      </div>
</template>

<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { streamState } from '../data/stream'
import { sessionState } from '../data/store'
import {
  llm,
  isProviderReady,
  THINKING_LEVELS,
  getModelThinkingLevel,
  thinkingShortLabel,
  type LlmProvider,
  type ThinkingLevel
} from '../data/panels'
import {
  sendPrompt,
  interruptSession,
  refreshSessionList,
  refreshLlm,
  switchModel,
  switchProvider,
  setModelThinking
} from '../data/wire'
import {
  forwardState,
  isSelectableItem,
  toggleForwardMode,
  exitForwardMode,
  selectAllSelectable,
  clearSelectedMessages
} from '../data/forward'
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
const modelWrapEl = ref<HTMLElement | null>(null)
const modelShellEl = ref<HTMLElement | null>(null)
const modelMenuOpen = ref(false)
const switchingModel = ref(false)
const hoveredTarget = ref<{ idx: number; model: string; top: number; flipLeft: boolean } | null>(null)
let hoverCloseTimer = 0

const streaming = computed(() => streamState.streaming)
const selectedCount = computed(() => forwardState.selectedIds.length)
const selectableTotal = computed(() => streamState.items.filter(isSelectableItem).length)
const canEnterForward = computed(() => Boolean(sessionState.activeId) && selectableTotal.value > 0)
const allSelected = computed(() => selectableTotal.value > 0 && selectedCount.value >= selectableTotal.value)

const activeProviderIndex = computed(() => {
  const i = llm.providers.findIndex(p => p.current)
  return i >= 0 ? i : 0
})
const activeProvider = computed(() => llm.providers[activeProviderIndex.value])
const activeModelName = computed(() => llm.model || '')
const activeThinkingLevel = computed<ThinkingLevel>(() => {
  if (!activeProvider.value || !activeModelName.value) return 'high'
  return getModelThinkingLevel(activeProvider.value, activeModelName.value)
})
const modelBtnTip = computed(() => {
  if (activeProvider.value && activeModelName.value) {
    return `Model: ${activeProvider.value.name} · ${activeModelName.value} · thinking: ${thinkingShortLabel(activeThinkingLevel.value)}`
  }
  return 'Switch model & thinking'
})

const hoveredThinkingLevel = computed<ThinkingLevel>(() => {
  if (!hoveredTarget.value) return 'high'
  const p = llm.providers[hoveredTarget.value.idx]
  return p ? getModelThinkingLevel(p, hoveredTarget.value.model) : 'high'
})

function modelThinkingLevel(provider: LlmProvider, model: string): ThinkingLevel {
  return getModelThinkingLevel(provider, model)
}

function isCurrentModel(idx: number, model: string): boolean {
  return idx === activeProviderIndex.value && model === activeModelName.value
}

function cancelHoverClose(): void {
  if (hoverCloseTimer) {
    window.clearTimeout(hoverCloseTimer)
    hoverCloseTimer = 0
  }
}

function clearHoverPopover(): void {
  cancelHoverClose()
  hoveredTarget.value = null
}

function scheduleHoverClose(): void {
  cancelHoverClose()
  hoverCloseTimer = window.setTimeout(() => {
    hoverCloseTimer = 0
    hoveredTarget.value = null
  }, 140)
}

function positionPopoverForRow(idx: number, model: string, rowEl: HTMLElement | null): void {
  const shellEl = modelShellEl.value
  if (!rowEl || !shellEl) return
  const rowRect = rowEl.getBoundingClientRect()
  const shellRect = shellEl.getBoundingClientRect()
  const estHeight = 148
  const rawTop = rowRect.top - shellRect.top - 4
  const maxByShell = Math.max(0, shellRect.height - estHeight)
  const maxByViewport = Math.max(0, window.innerHeight - shellRect.top - estHeight - 10)
  const top = Math.max(0, Math.min(rawTop, Math.min(maxByShell + 16, maxByViewport)))
  const flipLeft = shellRect.right + 108 > window.innerWidth && shellRect.left > 112
  hoveredTarget.value = { idx, model, top, flipLeft }
}

function onModelHover(idx: number, model: string, e: MouseEvent): void {
  if (!modelMenuOpen.value) return
  cancelHoverClose()
  positionPopoverForRow(idx, model, e.currentTarget as HTMLElement | null)
}

function toggleHoverPopover(idx: number, model: string, e: MouseEvent): void {
  if (!modelMenuOpen.value) return
  cancelHoverClose()
  if (hoveredTarget.value?.idx === idx && hoveredTarget.value?.model === model) {
    hoveredTarget.value = null
    return
  }
  const badgeEl = e.currentTarget as HTMLElement | null
  const rowEl = badgeEl?.closest('.mm-opt') as HTMLElement | null
  positionPopoverForRow(idx, model, rowEl)
}

async function pickModelEffort(idx: number, model: string, level: ThinkingLevel): Promise<void> {
  const provider = llm.providers[idx]
  if (!provider) return
  if (getModelThinkingLevel(provider, model) === level) return
  await setModelThinking(idx, model, level)
}

function toggleModelMenu(): void {
  const next = !modelMenuOpen.value
  modelMenuOpen.value = next
  if (!next) {
    clearHoverPopover()
  } else if (!llm.loaded || !llm.providers.length) {
    void refreshLlm()
  }
}

function openLlmDock(): void {
  clearHoverPopover()
  modelMenuOpen.value = false
  const nav = (window as unknown as { __pebbleNav?: { tab?: (name: string) => void } }).__pebbleNav
  nav?.tab?.('llm')
}

async function pickModel(idx: number, model: string): Promise<void> {
  const provider = llm.providers[idx]
  if (!provider) return
  if (!isProviderReady(provider)) {
    const toast = (window as unknown as { __pebbleToast?: (t: string, m: string, k?: string) => void }).__pebbleToast
    toast?.('Provider not ready', 'Add a Base URL and at least one model in the right-dock LLM panel first.', 'warn')
    return
  }
  if (isCurrentModel(idx, model)) {
    clearHoverPopover()
    modelMenuOpen.value = false
    return
  }
  clearHoverPopover()
  modelMenuOpen.value = false
  switchingModel.value = true
  try {
    if (idx === activeProviderIndex.value) {
      await switchModel(model)
    } else {
      await switchProvider(idx, model)
    }
  } finally {
    switchingModel.value = false
  }
}

function onDocClick(e: MouseEvent): void {
  if (!modelMenuOpen.value) return
  const target = e.target as Node | null
  if (target && modelWrapEl.value?.contains(target)) return
  clearHoverPopover()
  modelMenuOpen.value = false
}

function onDocKeydown(e: KeyboardEvent): void {
  if (e.key === 'Escape' && modelMenuOpen.value) {
    clearHoverPopover()
    modelMenuOpen.value = false
  }
}

function onToggleForward(): void {
  toggleForwardMode()
}

function cancelForward(): void {
  exitForwardMode()
}

function toggleSelectAll(): void {
  if (allSelected.value) {
    clearSelectedMessages()
  } else {
    selectAllSelectable(streamState.items)
  }
}

function openForwardDialog(): void {
  if (!forwardState.selectedIds.length) return
  forwardState.targetSessionId = ''
  forwardState.pickerOpen = true
  void refreshSessionList()
}

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

onMounted(() => {
  installDropZone()
  document.addEventListener('click', onDocClick, true)
  document.addEventListener('keydown', onDocKeydown)
})

onBeforeUnmount(() => {
  cancelHoverClose()
  document.removeEventListener('click', onDocClick, true)
  document.removeEventListener('keydown', onDocKeydown)
})
</script>
