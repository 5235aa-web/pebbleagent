<template>
      <div class="stream-head">
        <div class="sh-main">
          <div class="sh-title">{{ title }}</div>
          <div class="sh-sub">
            <template v-if="createdDate">
              <span>{{ createdDate }}</span>
              <span class="dotsep"></span>
            </template>
            <span>{{ messageCount }} messages</span>
          </div>
        </div>

        <div class="sh-meter">
          <div class="sh-acts">
            <button class="icon-btn tip tip-left" data-tip="Compact context" id="compactBtn">
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7"><polyline points="4 14 10 14 10 20"/><polyline points="20 10 14 10 14 4"/><line x1="14" y1="10" x2="21" y2="3"/><line x1="3" y1="21" x2="10" y2="14"/></svg>
            </button>
            <button class="icon-btn tip tip-left" data-tip="Search in messages" id="msgSearchBtn">
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7"><circle cx="11" cy="11" r="7"/><line x1="20" y1="20" x2="16" y2="16"/></svg>
            </button>
            <button class="icon-btn tip tip-left" data-tip="Session notes" id="notesBtn">
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7"><path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7"/><path d="M18.5 2.5a2.12 2.12 0 0 1 3 3L12 15l-4 1 1-4z"/></svg>
            </button>
            <button class="icon-btn tip tip-left" data-tip="Toggle dock" id="toggleRight">
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7"><rect x="3" y="3" width="18" height="18" rx="2"/><line x1="15" y1="3" x2="15" y2="21"/></svg>
            </button>
          </div>
        </div>
      </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { activeSession, sessionState } from '../data/store'

/* The header names the session that is actually open, not the prototype's
   placeholder. Falls back to a neutral label when nothing is selected yet, and
   before the first `session.list` response arrives. */
const title = computed(() => activeSession()?.title?.trim() || 'Untitled session')
const createdDate = computed(() => {
  const s = activeSession()
  const ts = s?.createdAt ?? s?.ts
  if (!ts || !Number.isFinite(ts) || ts <= 0) return ''
  const d = new Date(ts)
  if (Number.isNaN(d.getTime())) return ''
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${d.getFullYear()}/${pad(d.getMonth() + 1)}/${pad(d.getDate())}`
})
/* Every row the gateway holds for the session, not just the ones that render as
   a bubble: the header is a session counter, not a transcript row count. */
const messageCount = computed(() => sessionState.messageCount)
</script>
