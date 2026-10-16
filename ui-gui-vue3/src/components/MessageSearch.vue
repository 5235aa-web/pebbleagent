<script setup lang="ts">
import { ref, onMounted, onUnmounted, computed } from 'vue'
import { useGatewayStore } from '../stores/gateway'
import Tooltip from './Tooltip.vue'
const p = defineProps<{ onScrollToIndex?: (idx: number) => void }>()
const emit = defineEmits<{ (e: 'scrollToIndex', idx: number): void }>()
const g = useGatewayStore(); const open = ref(false); const query = ref(''); const activeIdx = ref(-1)
const inputRef = ref<HTMLInputElement | null>(null); const wrapRef = ref<HTMLDivElement | null>(null)
interface SearchMatch { idx: number; role: 'user' | 'agent'; preview: string }
const results = computed(() => {
  const q = query.value.trim().toLowerCase(); if (!q || q.length < 1) return []
  return g.messages.map((msg, idx) => {
    const text = msg.content || ''; const pos = text.toLowerCase().indexOf(q)
    if (pos === -1) return null
    const start = Math.max(0, pos - 20); const end = Math.min(text.length, pos + q.length + 40)
    let preview = text.slice(start, end)
    if (start > 0) preview = '...' + preview
    if (end < text.length) preview = preview + '...'
    return { idx, role: msg.role, preview } as SearchMatch
  }).filter((r): r is SearchMatch => r !== null)
})
function scrollTo(msgIdx: number) {
  if (p.onScrollToIndex) {
    // 统一入口已处理一切（含折叠段目标 → 定位到折叠分割线，不展开）
    p.onScrollToIndex(msgIdx)
  } else {
    // 降级路径（无统一入口时）：直接 DOM 定位
    const el = document.querySelector(`[data-msg-idx="${msgIdx}"]`) as HTMLElement | null
    if (el) el.scrollIntoView({ behavior: 'smooth', block: 'center' })
    else {
      const msgs = document.querySelector('.msgs') as HTMLElement | null
      const row = msgs?.children[msgIdx + 1] as HTMLElement | undefined
      row?.scrollIntoView({ behavior: 'smooth', block: 'center' })
    }
  }
  open.value = false; query.value = ''
}
function handleKeyDown(e: KeyboardEvent) {
  if (e.key === 'ArrowDown') { e.preventDefault(); activeIdx.value = activeIdx.value < results.value.length - 1 ? activeIdx.value + 1 : 0 }
  else if (e.key === 'ArrowUp') { e.preventDefault(); activeIdx.value = activeIdx.value > 0 ? activeIdx.value - 1 : results.value.length - 1 }
  else if (e.key === 'Enter' && activeIdx.value >= 0 && activeIdx.value < results.value.length) { e.preventDefault(); scrollTo(results.value[activeIdx.value].idx) }
  else if (e.key === 'Escape') { open.value = false; query.value = '' }
}
import { watch } from 'vue'
watch(open, v => { if (v) inputRef.value?.focus() })
function hmousedown(e: MouseEvent) { if (!open.value) return; if (wrapRef.value && !wrapRef.value.contains(e.target as Node)) { open.value = false; query.value = '' } }
onMounted(() => document.addEventListener('mousedown', hmousedown))
onUnmounted(() => document.removeEventListener('mousedown', hmousedown))
const hasResults = computed(() => results.value.length > 0)
</script>
<template>
  <div class="ms" ref="wrapRef">
    <template v-if="!open">
      <Tooltip text="Search messages" position="bottom"><button class="ms-btn" @click="open=true"><svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><circle cx="11" cy="11" r="7"/><line x1="16" y1="16" x2="22" y2="22"/></svg></button></Tooltip>
    </template>
    <template v-else>
      <div class="ms-wrap">
        <svg class="ms-icon" width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><circle cx="11" cy="11" r="7"/><line x1="16" y1="16" x2="22" y2="22"/></svg>
        <input ref="inputRef" class="ms-input" v-model="query" @keydown="handleKeyDown" placeholder="Search messages..."/>
        <Tooltip text="Close" position="bottom"><button class="ms-close" @click="open=false;query=''"><svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg></button></Tooltip>
        <div v-if="query.trim().length>0" class="ms-popup">
          <div v-if="!hasResults" class="ms-empty">No results</div>
          <div v-for="(r,i) in results" :key="r.idx" class="ms-item" :class="{active:i===activeIdx}" @mousedown="scrollTo(r.idx)" @mouseenter="activeIdx=i"><span class="ms-item-role" :class="r.role">{{r.role==='user'?'you':'agent'}}</span><span class="ms-item-text">{{r.preview}}</span></div>
          <div v-if="hasResults" class="ms-info">{{results.length}} match{{results.length>1?'es':''}}</div>
        </div>
      </div>
    </template>
  </div>
</template>
