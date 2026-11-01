<template>
  <div class="msg agent">
    <div class="avatar" :style="streaming ? 'border-color: var(--accent);' : ''">
      <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6"><rect x="3" y="3" width="18" height="14" rx="2"/><line x1="8" y1="21" x2="16" y2="21"/><line x1="12" y1="17" x2="12" y2="21"/></svg>
    </div>
    <div class="agent-body">
      <div class="agent-who"><b>Pebble</b><span class="aw-model">{{ model }}</span><span>·</span><span>{{ time }}</span><span v-if="streaming" style="color: var(--accent);">streaming</span></div>
      <template v-for="b in blocks" :key="b.id">
        <div
          v-if="b.kind === 'thinking'"
          class="thinking"
          :class="{ open: isOpen(b.id), 'th-static': !b.live }"
        >
          <span class="th-rail"><i></i></span>
          <span class="th-label">thinking</span>
          <span class="th-text">{{ b.text }}</span>
          <button
            v-if="b.text.length > TOGGLE_AT"
            class="th-toggle"
            type="button"
            @click="toggle(b.id)"
          >{{ isOpen(b.id) ? 'less' : 'more' }}</button>
        </div>
        <div v-else-if="b.kind === 'raw'" class="md">
          <p v-for="(line, i) in b.paras" :key="i">{{ line }}<span v-if="b.caret && i === b.paras.length - 1" class="caret"></span></p>
        </div>
        <div v-else-if="b.kind === 'text'" class="md" v-html="b.html"></div>
        <StreamCodeBlock v-else-if="b.kind === 'code'" :lang="b.lang" :code="b.code" />
        <MermaidCard v-else-if="b.kind === 'mermaid'" :code="b.code" :id="b.id" :streaming="b.streaming" />
        <SvgCard v-else-if="b.kind === 'svg'" :code="b.code" :streaming="b.streaming" />
        <StreamToolCard v-else-if="b.kind === 'tool'" :call="b.call" />
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
import { copyMessage, deleteMessage } from '../data/wire'
import StreamCodeBlock from './StreamCodeBlock.vue'
import StreamToolCard from './StreamToolCard.vue'
import MermaidCard from './MermaidCard.vue'
import SvgCard from './SvgCard.vue'

const props = defineProps<{ item: StreamItem }>()

/* Below this many characters a reasoning line already fits on one row. */
const TOGGLE_AT = 64

const blocks = computed(() => props.item.blocks || [])
const streaming = computed(() => !!props.item.streaming)
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
