<template>
      <div class="timeline" id="timeline" v-show="marks.length">
        <div class="tl-track"></div>
        <span class="tl-count">{{ marks.length }} {{ marks.length === 1 ? 'mark' : 'marks' }}</span>
        <button
          v-for="(mark, i) in marks"
          :key="mark.id"
          class="tl-node"
          :class="{ on: i === activeIndex }"
          data-kind="user"
          @mouseenter="showTip($event, mark)"
          @mouseleave="hideTip"
          @focus="showTip($event, mark)"
          @blur="hideTip"
          @click="jumpTo(mark)"
        ><i></i></button>
        <button class="tl-bottom" id="tlBottom" data-tip="Scroll to bottom" @click="scrollToBottom">
          <svg width="9" height="9" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4"><polyline points="6 9 12 15 18 9"/></svg>
        </button>
      </div>

      <!-- The hover card lives outside the rail: the rail scrolls once it holds
           more marks than fit, and a child of a scroll container gets clipped. -->
      <Teleport to="body">
        <div
          v-if="tip.show"
          class="tl-tipcard"
          :style="{ right: tip.right + 'px', top: tip.top + 'px' }"
          role="tooltip"
        >
          <p class="tl-tip-text">{{ tip.text }}</p>
          <span class="tl-tip-time" v-if="tip.time">{{ tip.time }}</span>
        </div>
      </Teleport>
</template>

<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { streamState } from '../data/stream'
import { dateOf } from '../data/render'
import type { StreamItem } from '../data/stream'

interface Mark {
  id: string
  index: number
  body: string
  time: string
}

/* The rail maps the questions you asked, not the work the agent did. Tool rows
   are an order of magnitude denser and would drown the marks that matter. */
const USER_ROW = 'msg user'
/* Matches the old shell's preview width: the widest card that still reads as a
   glance rather than a paragraph. */
const PREVIEW_MAX = 120

function flatPreview(text: string): string {
  const one = text.replace(/[ \t]+/g, ' ').replace(/\n{3,}/g, '\n\n').trim()
  return one.length > PREVIEW_MAX ? `${one.slice(0, PREVIEW_MAX)}…` : one
}

/* History carries `created_at` as an epoch float while live rows carry a
   formatted string, so normalise both to the same `YYYY-MM-DD HH:mm:ss`. The
   split between seconds and milliseconds lives in `dateOf`. */
function stampOf(raw: string): string {
  const d = dateOf(raw)
  if (!d) return String(raw ?? '').trim().replace('T', ' ').slice(0, 19)
  const p = (v: number) => String(v).padStart(2, '0')
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`
}

const marks = computed<Mark[]>(() => {
  const out: Mark[] = []
  streamState.items.forEach((item: StreamItem, index: number) => {
    if (item.className !== USER_ROW) return
    out.push({
      id: item.id,
      index,
      body: flatPreview(item.plain ?? '') || '(empty message)',
      time: stampOf(String(item.stamp ?? ''))
    })
  })
  return out
})

const activeIndex = ref(0)
const tip = ref({ show: false, right: 0, top: 0, text: '', time: '' })

function showTip(e: MouseEvent | FocusEvent, mark: Mark): void {
  const el = e.currentTarget as HTMLElement | null
  if (!el) return
  const r = el.getBoundingClientRect()
  tip.value = {
    show: true,
    right: window.innerWidth - r.left + 10,
    top: r.top + r.height / 2,
    text: mark.body,
    time: mark.time
  }
}

function hideTip(): void {
  tip.value = { ...tip.value, show: false }
}

function bodyEl(): HTMLElement | null {
  return document.getElementById('streamBody')
}

/* The row anchor resolves by position: every item renders exactly one root node
   inside `#streamInner` in item order, so the child at the item's index is the
   row without threading an attribute through three child components. */
function rowEl(index: number): HTMLElement | null {
  const inner = document.getElementById('streamInner')
  return (inner?.children[index] as HTMLElement | undefined) ?? null
}

function jumpTo(mark: Mark): void {
  const el = rowEl(mark.index)
  if (!el) return
  el.scrollIntoView({ behavior: 'smooth', block: 'center' })
}

function scrollToBottom(): void {
  const el = bodyEl()
  el?.scrollTo({ top: el.scrollHeight, behavior: 'smooth' })
}

/* The mark being read is the last row whose top has not passed the viewport
   top, so scrolling up leaves the earlier mark lit instead of clearing it. */
function syncActive(): void {
  const body = bodyEl()
  if (!body || !marks.value.length) return
  const line = body.getBoundingClientRect().top
  let current = marks.value[0].index
  for (const mark of marks.value) {
    const el = rowEl(mark.index)
    if (!el) continue
    if (el.getBoundingClientRect().bottom > line) { current = mark.index; break }
    current = mark.index
  }
  const next = marks.value.findIndex(m => m.index === current)
  if (next >= 0) activeIndex.value = next
}

let raf = 0
function onScroll(): void {
  if (raf) return
  raf = window.requestAnimationFrame(() => { raf = 0; syncActive() })
}

watch(() => marks.value.length, () => { void nextTick(syncActive) })

onMounted(() => {
  bodyEl()?.addEventListener('scroll', onScroll, { passive: true })
  void nextTick(syncActive)
})

onBeforeUnmount(() => {
  bodyEl()?.removeEventListener('scroll', onScroll)
  if (raf) window.cancelAnimationFrame(raf)
})
</script>
