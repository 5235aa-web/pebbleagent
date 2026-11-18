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
    <div class="agent-body">
      <div v-if="processSegments.length > 1" class="agent-who">
        <button
          class="proc-all-btn"
          type="button"
          @click="toggleAllProcesses"
        >{{ allProcessesOpen ? 'Collapse all' : `Expand all (${processSegments.length})` }}</button>
      </div>

      <template v-for="seg in segments" :key="seg.id">
        <div v-if="seg.kind === 'process'" class="proc-wrap">
          <button
            class="proc-bar"
            :class="{ open: isProcessOpen(seg.id), 'is-live': seg.summary.isLive, 'is-failed': seg.summary.failedCount > 0 }"
            type="button"
            :aria-expanded="isProcessOpen(seg.id) ? 'true' : 'false'"
            @click="toggleProcess(seg.id)"
          >
            <span class="proc-dot" :class="{ live: seg.summary.isLive, err: !seg.summary.isLive && seg.summary.failedCount > 0 }"><i></i></span>
            <span class="proc-label">{{ seg.summary.label }}</span>
            <span
              v-if="seg.summary.detail"
              class="proc-detail"
              :class="{ err: !seg.summary.isLive && seg.summary.failedCount > 0 }"
              :title="seg.summary.detail"
            >{{ seg.summary.detail }}</span>
            <svg class="proc-caret" width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2"><polyline points="9 6 15 12 9 18"/></svg>
          </button>

          <div v-if="isProcessOpen(seg.id)" class="proc-panel">
            <template v-for="pb in seg.blocks" :key="pb.id">
              <div
                v-if="pb.kind === 'thinking'"
                class="thinking"
                :class="{ open: isOpen(pb.id), 'th-static': !pb.live }"
              >
                <span class="th-rail"><i></i></span>
                <span class="th-label">thinking</span>
                <span class="th-text">{{ pb.text }}</span>
                <button
                  v-if="pb.text.length > TOGGLE_AT"
                  class="th-toggle"
                  type="button"
                  @click="toggle(pb.id)"
                >{{ isOpen(pb.id) ? 'less' : 'more' }}</button>
              </div>
              <StreamToolCard v-else-if="pb.kind === 'tool'" :call="pb.call" />
            </template>
          </div>
        </div>

        <template v-else>
          <div v-if="seg.block.kind === 'raw'" class="md">
            <p v-for="(line, i) in seg.block.paras" :key="i">{{ line }}<span v-if="seg.block.caret && streaming && seg.isLastProse && i === seg.block.paras.length - 1" class="caret"></span></p>
          </div>
          <div v-else-if="seg.block.kind === 'text'" class="md" v-html="seg.block.html"></div>
          <StreamCodeBlock v-else-if="seg.block.kind === 'code'" :lang="seg.block.lang" :code="seg.block.code" />
          <MermaidCard v-else-if="seg.block.kind === 'mermaid'" :code="seg.block.code" :id="seg.block.id" :streaming="seg.block.streaming" />
          <SvgCard v-else-if="seg.block.kind === 'svg'" :code="seg.block.code" :streaming="seg.block.streaming" />
        </template>
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
import { computed, reactive } from 'vue'
import type { StreamItem } from '../data/stream'
import { groupMessageSegments, type MessageSegment } from '../data/blocks'
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
const segments = computed(() => groupMessageSegments(blocks.value, streaming.value))
const processSegments = computed(() =>
  segments.value.filter((s): s is Extract<MessageSegment, { kind: 'process' }> => s.kind === 'process')
)

const processOpen = reactive<Record<string, boolean>>({})
function isProcessOpen(id: string): boolean {
  return !!processOpen[id]
}
function toggleProcess(id: string) {
  processOpen[id] = !processOpen[id]
}
const allProcessesOpen = computed(
  () => processSegments.value.length > 0 && processSegments.value.every(s => !!processOpen[s.id])
)
function toggleAllProcesses() {
  const next = !allProcessesOpen.value
  for (const s of processSegments.value) {
    processOpen[s.id] = next
  }
}
const selectable = computed(() => forwardState.active && isSelectableItem(props.item))
const selected = computed(() => selectable.value && isMessageSelected(props.item.id))

function onCaptureClick(ev: MouseEvent) {
  if (!selectable.value) return
  ev.preventDefault()
  ev.stopPropagation()
  toggleSelectMessage(props.item.id)
}

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
