<script setup lang="ts">
import { ref, computed, onMounted, onUnmounted, watch, nextTick } from 'vue'
import { useGatewayStore } from '../stores/gateway'
import MessageBubble from './MessageBubble.vue'
import CompactDivider from './CompactDivider.vue'
import type { Message } from '../types/agentChat'
const g = useGatewayStore()
const cr = ref<HTMLDivElement|null>(null); let as = true

// 压缩段展开状态：默认全折叠（空集合=全折叠）；key 为 boundary 锚点 id（后端 uuid，稳定）
const expanded = ref<Set<string>>(new Set())

interface Segment { bid: string; start: number; end: number; anchorIdx: number; collapsible: boolean }
interface DividerItem { key: string; kind: 'divider'; bid: string; count: number; collapsed: boolean }
interface MsgItem { key: string; kind: 'msg'; msg: Message; idx: number }
type RenderItem = DividerItem | MsgItem

const all = computed<Message[]>(() => g.streamingMessage ? [...g.messages, { ...g.streamingMessage, id: g.streamingMessage.id || 'streaming' }] : g.messages)

// 按压缩边界锚点分段：段 = 相邻锚点之间的消息；可折叠条件 = 段非空且全部 isCompacted
// （防御：异常数据不全标记则不折叠，照常全显、不显示胶囊；基于 g.messages 计算，流式期间不重算）
const segments = computed<Segment[]>(() => {
  const arr = g.messages
  const anchors: number[] = []
  for (let i = 0; i < arr.length; i++) if (arr[i].isCompactBoundary) anchors.push(i)
  const segs: Segment[] = []
  for (let k = 0; k < anchors.length; k++) {
    const a = anchors[k]
    const start = k === 0 ? 0 : anchors[k - 1] + 1
    let collapsible = a > start
    for (let i = start; i < a && collapsible; i++) if (!arr[i].isCompacted) collapsible = false
    segs.push({ bid: arr[a].id, start, end: a, anchorIdx: a, collapsible })
  }
  return segs
})

// 索引辅助：byMsg（消息→可折叠段，跳转自动展开用）、byAnchor（锚点→段）、segOf（消息→段，渲染过滤用）
const segIndex = computed(() => {
  const byMsg = new Map<number, Segment>()
  const byAnchor = new Map<number, Segment>()
  const segOf: (Segment | null)[] = new Array(g.messages.length).fill(null)
  for (const s of segments.value) {
    byAnchor.set(s.anchorIdx, s)
    for (let i = s.start; i < s.end; i++) { segOf[i] = s; if (s.collapsible) byMsg.set(i, s) }
  }
  return { byMsg, byAnchor, segOf }
})

// 渲染列表：折叠段内消息跳过（真正不渲染）；可折叠锚点位置渲染分割线胶囊
const renderItems = computed<RenderItem[]>(() => {
  const arr = all.value
  const { byAnchor, segOf } = segIndex.value
  const out: RenderItem[] = []
  for (let i = 0; i < arr.length; i++) {
    const aSeg = byAnchor.get(i)
    if (aSeg) { if (aSeg.collapsible) out.push({ key: aSeg.bid, kind: 'divider', bid: aSeg.bid, count: aSeg.end - aSeg.start, collapsed: !expanded.value.has(aSeg.bid) }); continue }
    const s = segOf[i]
    if (s && s.collapsible && !expanded.value.has(s.bid)) continue
    out.push({ key: arr[i].id, kind: 'msg', msg: arr[i], idx: i })
  }
  return out
})

// 展开/收起：记录胶囊视口位置，DOM 更新后补偿滚动，保持胶囊在视口原地
async function toggleSegment(bid: string) {
  const el = cr.value?.querySelector(`[data-cb-id="${bid}"]`) as HTMLElement | null
  const beforeTop = el ? el.getBoundingClientRect().top : null
  const next = new Set(expanded.value)
  if (next.has(bid)) next.delete(bid); else next.add(bid)
  expanded.value = next
  await nextTick()
  if (el && beforeTop !== null && cr.value) cr.value.scrollTop += el.getBoundingClientRect().top - beforeTop
}

// 跳转：目标落在折叠段内且未展开 → 不展开，滚动到该段的折叠分割线胶囊（用户自行决定是否展开，避免误触展开增加渲染压力）
function scrollToMessageIndex(idx: number) {
  const seg = segIndex.value.byMsg.get(idx)
  if (seg && !expanded.value.has(seg.bid)) {
    const cb = cr.value?.querySelector(`[data-cb-id="${seg.bid}"]`) as HTMLElement | null
    cb?.scrollIntoView({ behavior: 'smooth', block: 'center' })
    return
  }
  const sel = `[data-msg-idx="${idx}"]`
  let e = cr.value?.querySelector(sel) as HTMLElement | undefined
  if (!e) e = document.querySelector(sel) as HTMLElement | undefined
  if (e) { e.scrollIntoView({ behavior: 'smooth', block: 'center' }); return }
  const msgs = cr.value || document.querySelector('.msgs') as HTMLElement | null
  if (!msgs) return
  const row = msgs.children[idx + 1] as HTMLElement | undefined
  row?.scrollIntoView({ behavior: 'smooth', block: 'center' })
}

defineExpose({ containerElement: cr, getAutoScroll: () => as, scrollToBottom: (b?: ScrollBehavior) => { if (cr.value) cr.value.scrollTo({ top: cr.value.scrollHeight, behavior: b || 'smooth' }) }, scrollToMessageIndex })

function os() { const c = cr.value; if (!c) return; as = c.scrollHeight - c.scrollTop - c.clientHeight < 100 }
watch(() => g.streaming, (v) => { if (v) as = true })
// 会话切换 → 重置折叠状态（默认全折叠；同一会话内压缩/流式不影响已展开的段）
watch(() => g.sessionId, () => { expanded.value = new Set() })
function scrollToBottom() { if (!as || !cr.value) return; cr.value.scrollTop = cr.value.scrollHeight }
watch(() => g.messages.length, () => { const last = g.messages[g.messages.length - 1]; if (last?.role === 'user') as = true; nextTick(() => requestAnimationFrame(scrollToBottom)) })
watch(() => g.streamingMessage, () => { if (g.streaming) requestAnimationFrame(scrollToBottom) })
onMounted(() => cr.value?.addEventListener('scroll', os, { passive: true }))
onUnmounted(() => cr.value?.removeEventListener('scroll', os))
</script>
<template><div ref="cr" class="msgs" style="flex:1;display:block;overflow-y:auto;overflow-x:hidden;padding:14px 20px;position:relative"><div v-if="all.length===0" class="tdiv" style="margin-top:40px">Send a message to start</div><template v-for="item in renderItems" :key="item.key"><div v-if="item.kind==='divider'" :data-cb-id="item.bid"><CompactDivider :count="item.count" :collapsed="item.collapsed" @toggle="toggleSegment(item.bid)"/></div><div v-else :data-msg-idx="item.idx"><MessageBubble :message="item.msg" :data-msg-idx="item.idx"/></div></template></div></template>
