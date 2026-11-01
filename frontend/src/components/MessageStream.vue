<template>
      <div class="stream-body" id="streamBody">
        <div class="stream-inner stagger" id="streamInner" @click="onStreamClick">
          <div v-if="!stream.length" class="stream-empty">
            <b>No transcript yet</b>
            <span>Send a message to start a session.</span>
          </div>
          <template v-for="item in stream" :key="item.id">
            <CompactDivider
              v-if="item.kind === 'compact'"
              v-bind="item.attrs"
              :count="item.compact?.count ?? 0"
              :tools="item.compact?.tools ?? 0"
              :collapsed="item.compact?.collapsed ?? true"
              @toggle="onToggle(item)"
            />
            <ClarifyCard
              v-else-if="item.kind === 'clarify'"
              :request-id="item.clarify?.requestId ?? ''"
              :questions="item.clarify?.questions ?? []"
              @submit="onClarifySubmit(item, $event)"
              @skip="onClarifySkip(item)"
            />
            <StreamMessage v-else-if="item.blocks" :item="item" />
            <div v-else :class="item.className" v-bind="item.attrs" v-html="item.innerHTML"></div>
          </template>
        </div>
      </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'
/* The jump button is its own component: it observes the scroll surface owned by
   this one, and keeping it separate stops every new row from re-rendering it. */
import { streamState } from '../data/stream'
import { toggleCompact, respondClarify, skipClarify, copyMessage, deleteMessage } from '../data/wire'
import StreamMessage from './StreamMessage.vue'
import CompactDivider from './CompactDivider.vue'
import ClarifyCard from './ClarifyCard.vue'
import type { StreamItem } from '../data/stream'

const stream = computed(() => streamState.items)

function onToggle(item: StreamItem) {
  const bid = item.compact?.bid
  if (bid) toggleCompact(bid)
}

function onClarifySubmit(item: StreamItem, answers: Record<string, string>) {
  const rid = item.clarify?.requestId
  if (rid) respondClarify(rid, answers)
}

/** User rows are rendered from an HTML string, so their action buttons are
    handled by one delegated listener instead of per-row bindings. */
function onStreamClick(ev: MouseEvent) {
  const el = (ev.target as HTMLElement | null)?.closest('[data-msg-act]') as HTMLElement | null
  if (!el) return
  const id = el.getAttribute('data-item-id') || ''
  const item = stream.value.find(i => i.id === id)
  if (!item) return
  if (el.getAttribute('data-msg-act') === 'copy') {
    copyMessage(item.plain || '')
    return
  }
  deleteMessage(id)
}

function onClarifySkip(item: StreamItem) {
  const rid = item.clarify?.requestId
  if (rid) skipClarify(rid)
}
</script>
