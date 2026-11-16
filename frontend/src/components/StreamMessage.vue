<template>
  <div
    class="msg agent"
    :class="{ 'is-selectable': selectable, 'is-selected': selected }"
    @click.capture="onCaptureClick"
  >
    <span
      v-if="selectable"
      class="fwd-check"
      :class="{ checked: selected }"
      aria-hidden="true"
    >
      <svg v-if="selected" width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3"><polyline points="20 6 9 17 4 12"/></svg>
    </span>
    <div class="avatar" :style="streaming ? 'border-color: var(--accent);' : ''">
      <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6"><rect x="3" y="3" width="18" height="14" rx="2"/><line x1="8" y1="21" x2="16" y2="21"/><line x1="12" y1="17" x2="12" y2="21"/></svg>
    </div>
    <div class="agent-body">
      <div class="agent-who"><b>Pebble</b><span class="aw-model">{{ model }}</span><span>·</span><span>{{ time }}</span><span v-if="streaming" style="color: var(--accent);">streaming</span></div>

      <div v-if="process.hasProcess" class="proc-wrap">
        <button
          class="proc-bar"
          :class="{ open: processOpen, 'is-live': process.isLive, 'is-failed': process.failedCount > 0 }"
          type="button"
          :aria-expanded="processOpen ? 'true' : 'false'"
          @click="toggleProcess"
        >
          <span class="proc-dot" :class="{ live: process.isLive, err: !process.isLive && process.failedCount > 0 }"><i></i></span>
          <span class="proc-label">{{ process.label }}</span>
          <span
            v-if="process.detail"
            class="proc-detail"
            :class="{ err: !process.isLive && process.failedCount > 0 }"
            :title="process.detail"
          >{{ process.detail }}</span>
          <svg class="proc-caret" width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2"><polyline points="9 6 15 12 9 18"/></svg>
        </button>

        <div v-if="processOpen" class="proc-panel">
          <template v-for="entry in processEntries" :key="entry.id">
            <div v-if="entry.kind === 'anchor'" class="proc-anchor" aria-hidden="true">
              <span class="pa-rule"></span>
              <span class="pa-tag">reply</span>
              <span class="pa-text" :title="entry.preview">{{ entry.preview }}</span>
              <span class="pa-rule"></span>
            </div>
            <div
              v-else-if="entry.block.kind === 'thinking'"
              class="thinking"
              :class="{ open: isOpen(entry.block.id), 'th-static': !entry.block.live }"
            >
              <span class="th-rail"><i></i></span>
              <span class="th-label">thinking</span>
              <span class="th-text">{{ entry.block.text }}</span>
              <button
                v-if="entry.block.text.length > TOGGLE_AT"
                class="th-toggle"
                type="button"
                @click="toggle(entry.block.id)"
              >{{ isOpen(entry.block.id) ? 'less' : 'more' }}</button>
            </div>
            <StreamToolCard v-else-if="entry.block.kind === 'tool'" :call="entry.block.call" />
          </template>
        </div>
      </div>

      <template v-for="b in proseBlocks" :key="b.id">
        <div v-if="b.kind === 'raw'" class="md">
          <p v-for="(line, i) in b.paras" :key="i">{{ line }}<span v-if="b.caret && i === b.paras.length - 1" class="caret"></span></p>
        </div>
        <div v-else-if="b.kind === 'text'" class="md" v-html="b.html"></div>
        <StreamCodeBlock v-else-if="b.kind === 'code'" :lang="b.lang" :code="b.code" />
        <MermaidCard v-else-if="b.kind === 'mermaid'" :code="b.code" :id="b.id" :streaming="b.streaming" />
        <SvgCard v-else-if="b.kind === 'svg'" :code="b.code" :streaming="b.streaming" />
      </template>

      <!-- A stopped turn says so where it stops: a dashed rule trailing off and
           a small label. Without it the row reads as a finished answer that
           simply happened to end mid-sentence. -->
      <div v-if="item.interrupted" class="msg-cut">
        <span class="cut-rule"></span>
        <span class="cut-label">interrupted</span>
      </div>

      <div class="msg-actions">
        <button class="ma-btn" type="button" @click="copy">
          <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6"><rect x="9" y="9" width="12" height="12" rx="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/></svg>
          Copy
        </button>
        <button class="ma-btn" type="button" @click="remove">
          <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6"><polyline points="3 6 5 6 21 6"/><path d="M19 6l-1 14H6L5 6"/><path d="M10 11v6M14 11v6"/></svg>
          Delete
        </button>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, reactive, ref } from 'vue'
import type { StreamItem } from '../data/stream'
import { buildProcessTimeline, proseOnlyBlocks, summarizeAgentProcess } from '../data/blocks'
import { copyMessage, deleteMessage } from '../data/wire'
import { forwardState, isSelectableItem, isMessageSelected, toggleSelectMessage } from '../data/forward'
import StreamCodeBlock from './StreamCodeBlock.vue'
import StreamToolCard from './StreamToolCard.vue'
import MermaidCard from './MermaidCard.vue'
import SvgCard from './SvgCard.vue'

const props = defineProps<{ item: StreamItem }>()

/* Below this many characters a reasoning line already fits on one row. */
const TOGGLE_AT = 64

const blocks = computed(() => props.item.blocks || [])
const streaming = computed(() => !!props.item.streaming)
const processOpen = ref(false)
const process = computed(() => summarizeAgentProcess(blocks.value, streaming.value))
const processEntries = computed(() => (processOpen.value ? buildProcessTimeline(blocks.value) : []))
const proseBlocks = computed(() => proseOnlyBlocks(blocks.value))

function toggleProcess() {
  processOpen.value = !processOpen.value
}
const selectable = computed(() => forwardState.active && isSelectableItem(props.item))
const selected = computed(() => selectable.value && isMessageSelected(props.item.id))

function onCaptureClick(ev: MouseEvent) {
  if (!selectable.value) return
  ev.preventDefault()
  ev.stopPropagation()
  toggleSelectMessage(props.item.id)
}
const model = computed(() => props.item.model || '')
const time = computed(() => props.item.time || '')

const open = reactive<Record<string, boolean>>({})
function isOpen(id: string): boolean {
  return !!open[id]
}
function toggle(id: string) {
  open[id] = !open[id]
}

function copy() {
  copyMessage(props.item.plain || '')
}

function remove() {
  deleteMessage(props.item.id)
}
</script>
