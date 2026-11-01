<template>
<div class="ms-pop" id="msPop" aria-hidden="true">
  <div class="ms-in">
    <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="11" cy="11" r="7"/><line x1="20" y1="20" x2="16" y2="16"/></svg>
    <input id="msInput" placeholder="Search messages..." autocomplete="off" v-model="query" />
    <span class="ms-count" id="msCount">{{ filtered.length }}</span>
    <button class="ms-close" id="msClose" @click="close">&#215;</button>
  </div>
  <div class="ms-filters" id="msFilters">
    <button :class="{ on: role === 'all' }" @click="role = 'all'">All</button>
    <button :class="{ on: role === 'user' }" @click="role = 'user'">You</button>
    <button :class="{ on: role === 'ai' }" @click="role = 'ai'">AI</button>
  </div>
  <div class="ms-list" id="msList">
    <button
      class="ms-item"
      v-for="(h, i) in filtered"
      :key="h.idx + '-' + i"
      :class="{ on: i === activeIdx }"
      @click="jump(h)"
      @mouseenter="activeIdx = i"
    >
      <span class="ms-badge" :class="h.role === 'user' ? 'user' : 'ai'">{{ h.role === 'user' ? 'You' : 'AI' }}</span>
      <span class="ms-body">
        <span class="ms-txt">{{ h.before }}<mark>{{ h.match }}</mark>{{ h.after }}</span>
        <span class="ms-meta" v-if="h.meta">{{ h.meta }}</span>
      </span>
    </button>
  </div>
  <div class="ms-empty" id="msEmpty" v-if="!filtered.length">{{ query.trim() ? 'no message matches' : 'type a keyword to search' }}</div>
  <div class="ms-foot"><kbd>&#8593;</kbd><kbd>&#8595;</kbd> navigate · <kbd>esc</kbd> close</div>
</div>
</template>

<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref } from 'vue'
import { streamState } from '../data/stream'

/* Search runs over the transcript already on screen, the way the old shell did
   it: substring match per row, previewed around the hit. It is not a gateway
   query — the server returned rows unrelated to the term, which is what made the
   panel look broken. */

interface LocalHit {
  idx: number
  role: 'user' | 'assistant'
  before: string
  match: string
  after: string
  meta: string
}

const query = ref('')
const role = ref<'all' | 'user' | 'ai'>('all')
const activeIdx = ref(0)

function plainOf(text: string): string {
  return text.replace(/<[^>]*>/g, ' ').replace(/\s+/g, ' ').trim()
}

/* The hit is split into three fields so the matched term can be marked instead of
   flattened into an unreadable slice: `…before <mark>mcp</mark> after…`. */
const hits = computed<LocalHit[]>(() => {
  const q = query.value.trim().toLowerCase()
  if (!q) return []
  const out: LocalHit[] = []
  streamState.items.forEach((item, idx) => {
    const isUser = item.className === 'msg user'
    if (!isUser && !item.blocks) return
    const body = plainOf(item.plain ?? item.innerHTML ?? '')
    const pos = body.toLowerCase().indexOf(q)
    if (pos === -1) return
    const start = Math.max(0, pos - 26)
    const end = Math.min(body.length, pos + q.length + 64)
    out.push({
      idx,
      role: isUser ? 'user' : 'assistant',
      before: (start > 0 ? '…' : '') + body.slice(start, pos),
      match: body.slice(pos, pos + q.length),
      after: body.slice(pos + q.length, end) + (end < body.length ? '…' : ''),
      meta: item.stamp ?? ''
    })
  })
  return out
})

const filtered = computed(() => {
  if (role.value === 'all') return hits.value
  return hits.value.filter(h => (role.value === 'user' ? h.role === 'user' : h.role === 'assistant'))
})

function open(): void {
  const el = document.getElementById('msPop')
  if (!el) return
  /* `.ms-pop` is fixed with no coordinates of its own, so without this it settles
     at the end of the document flow, i.e. below the viewport. */
  const btn = document.getElementById('msgSearchBtn')
  if (btn) {
    const r = btn.getBoundingClientRect()
    el.style.top = Math.round(Math.max(56, Math.min(r.bottom + 10, window.innerHeight - 140))) + 'px'
    el.style.right = Math.round(Math.max(8, window.innerWidth - r.right)) + 'px'
    el.style.left = 'auto'
  }
  el.classList.add('open')
  el.setAttribute('aria-hidden', 'false')
  window.setTimeout(() => document.getElementById('msInput')?.focus(), 60)
}

function close(): void {
  const el = document.getElementById('msPop')
  el?.classList.remove('open')
  el?.setAttribute('aria-hidden', 'true')
  query.value = ''
  role.value = 'all'
}

function toggle(): void {
  const el = document.getElementById('msPop')
  if (!el) return
  if (el.classList.contains('open')) close()
  else open()
}

function jump(hit: LocalHit): void {
  close()
  /* Rows render one root node per item in order, so the index resolves to an
     element without an anchor attribute on every row component. */
  void nextTick().then(() => {
    const row = document.getElementById('streamInner')?.children[hit.idx] as HTMLElement | undefined
    row?.scrollIntoView({ behavior: 'smooth', block: 'center' })
  })
}

function onKey(e: KeyboardEvent): void {
  if (e.key === 'Escape') close()
}

function onOutside(e: MouseEvent): void {
  const el = document.getElementById('msPop')
  if (!el?.classList.contains('open')) return
  const t = e.target as Node
  if (el.contains(t)) return
  if (document.getElementById('msgSearchBtn')?.contains(t)) return
  close()
}

onMounted(() => {
  document.getElementById('msgSearchBtn')?.addEventListener('click', toggle)
  document.addEventListener('keydown', onKey)
  document.addEventListener('click', onOutside)
})

onBeforeUnmount(() => {
  document.getElementById('msgSearchBtn')?.removeEventListener('click', toggle)
  document.removeEventListener('keydown', onKey)
  document.removeEventListener('click', onOutside)
})
</script>
