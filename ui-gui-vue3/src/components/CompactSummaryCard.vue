<script setup lang="ts">
import { computed } from 'vue'
const p = defineProps<{ summary: string }>()

function bullets(text: string): string[] {
  return text.split('\n').map(l => l.replace(/^-\s*/, '').trim()).filter(l => l.length > 0 && l !== '(none)')
}

function section(text: string, title: string): string {
  const r = new RegExp(`##(?!#)\\s*${title}\\s*\\n([\\s\\S]*?)(?=\\n##(?!#)|$)`, 'i')
  const m = text.match(r)
  return m ? m[1] : ''
}

const parsed = computed(() => {
  let raw = p.summary.replace(/^Summary:\s*\n/, '')
  raw = raw.replace(/<\/?summary>/g, '').replace(/<analysis>[\s\S]*?<\/analysis>/g, '')
  const task = bullets(section(raw, '任务')).join(' ') || '(none)'
  const status = bullets(section(raw, '现状'))
  const pick = (prefix: string) => status
    .filter(l => l.startsWith(prefix))
    .map(l => l.replace(new RegExp(`^${prefix}[：:]\\s*`), ''))
  const done = pick('已完成')
  const doing = pick('进行中')
  const blocked = pick('阻塞')
  const decisions = bullets(section(raw, '决定'))
  const next = bullets(section(raw, '待办'))
  const clues = bullets(section(raw, '线索'))
  return { task, done, doing, blocked, decisions, next, clues }
})
</script>
<template><div class="cs-card"><div class="cs-bar"/><div class="cs-inner">
<div class="cs-sec static"><div class="cs-hdr static"><span class="cs-icon"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round"><circle cx="12" cy="12" r="10"/><circle cx="12" cy="12" r="6"/><circle cx="12" cy="12" r="2"/></svg></span><span class="cs-title">任务</span></div><div class="cs-body-wrap static"><div class="cs-body"><div class="cs-goal-area"><div class="cs-goal-text">{{parsed.task}}</div></div></div></div></div>
<div class="cs-sec static"><div class="cs-hdr static"><span class="cs-icon"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><path d="M22 12a10 10 0 11-20 0 10 10 0 0120 0z"/><path d="M12 6v6l4 2"/></svg></span><span class="cs-title">现状</span></div><div class="cs-body-wrap static"><div class="cs-body"><div class="cs-progress-grid"><div class="cs-progress-item"><div><div class="cs-progress-label"><span class="cs-dot done"/> 已完成</div><div class="cs-progress-detail"><span v-for="(d,i) in parsed.done" :key="i">{{d}}</span></div></div><div class="cs-progress-count">{{parsed.done.length}}</div></div><div class="cs-progress-item"><div><div class="cs-progress-label"><span class="cs-dot ip"/> 进行中</div><div class="cs-progress-detail"><span v-for="(d,i) in parsed.doing" :key="i">{{d}}</span></div></div><div class="cs-progress-count">{{parsed.doing.length}}</div></div><div class="cs-progress-item"><div><div class="cs-progress-label"><span class="cs-dot blk"/> 阻塞</div><div class="cs-progress-detail"><span v-for="(d,i) in parsed.blocked" :key="i">{{d}}</span></div></div><div class="cs-progress-count">{{parsed.blocked.length}}</div></div></div></div></div></div>
<div class="cs-sec is-open"><button class="cs-hdr" @click=""><span class="cs-icon"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><path d="M9 11l3 3L22 4"/><path d="M21 12v7a2 2 0 01-2 2H5a2 2 0 01-2-2V5a2 2 0 012-2h11"/></svg></span><span class="cs-title">决定</span><span v-if="parsed.decisions.length" class="cs-meta">{{parsed.decisions.length}} items</span></button><div class="cs-body-wrap"><div class="cs-body"><template v-if="parsed.decisions.length>0"><div v-for="(item,i) in parsed.decisions" :key="i" class="cs-item cs-decision">{{item}}</div></template><div v-else class="cs-empty">(none)</div></div></div></div>
<div class="cs-sec is-open"><button class="cs-hdr" @click=""><span class="cs-icon"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><line x1="5" y1="12" x2="19" y2="12"/><polyline points="12 5 19 12 12 19"/></svg></span><span class="cs-title">待办</span><span v-if="parsed.next.length" class="cs-meta">{{parsed.next.length}} items</span></button><div class="cs-body-wrap"><div class="cs-body"><template v-if="parsed.next.length>0"><div v-for="(item,i) in parsed.next" :key="i" class="cs-item cs-next"><span class="cs-num">{{i+1}}</span>{{item}}</div></template><div v-else class="cs-empty">(none)</div></div></div></div>
<div class="cs-sec is-open"><button class="cs-hdr" @click=""><span class="cs-icon"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/></svg></span><span class="cs-title">线索</span><span v-if="parsed.clues.length" class="cs-meta">{{parsed.clues.length}} items</span></button><div class="cs-body-wrap"><div class="cs-body"><template v-if="parsed.clues.length>0"><div v-for="(item,i) in parsed.clues" :key="i" class="cs-item cs-context-text">{{item}}</div></template><div v-else class="cs-empty">(none)</div></div></div></div>
</div></div></template>
