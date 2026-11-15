<template>
  <div v-if="payload" :class="item.className + ' msg-forwarded'" v-bind="item.attrs">
    <div class="fwd-card" :class="{ collapsed }">
      <div class="fwd-hdr">
        <div class="fwd-hdr-main">
          <span class="fwd-badge">
            <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
              <polyline points="15 14 20 9 15 4" />
              <path d="M4 20v-7a4 4 0 0 1 4-4h12" />
            </svg>
            Forwarded
          </span>
          <span class="fwd-src" :title="payload.sourceTitle">from "{{ payload.sourceTitle }}"</span>
          <span class="fwd-count">{{ payload.count }} {{ payload.count === 1 ? 'msg' : 'msgs' }}</span>
        </div>
        <div class="fwd-hdr-right">
          <span v-if="clockLabel" class="fwd-clock">{{ clockLabel }}</span>
          <button
            type="button"
            class="fwd-toggle-btn"
            @click.stop="collapsed = !collapsed"
          >{{ collapsed ? 'Expand' : 'Collapse' }}</button>
        </div>
      </div>

      <div v-if="!collapsed" class="fwd-sections">
        <div
          v-for="sec in payload.sections"
          :key="sec.index"
          class="fwd-sec"
          :class="'is-' + sec.roleKind"
        >
          <div class="fwd-sec-head">
            <span class="fwd-role-pill" :class="'is-' + sec.roleKind">
              {{ sec.index }}. {{ sec.roleLabel }}
            </span>
            <span v-if="sec.time" class="fwd-sec-time">{{ sec.time }}</span>
          </div>

          <div v-if="sec.bodyMarkdown" class="fwd-sec-body md" v-html="renderSectionBody(sec.bodyMarkdown)"></div>

          <div v-if="sec.tools.length" class="fwd-tools">
            <div class="fwd-tools-cap">Edit / Exec tool records ({{ sec.tools.length }})</div>
            <div
              v-for="(tool, tIdx) in sec.tools"
              :key="tIdx"
              class="fwd-tool"
              :class="{
                open: isToolOpen(sec.index, tIdx),
                'is-failed': tool.status === 'failed' || tool.status === 'cancelled'
              }"
            >
              <button
                type="button"
                class="fwd-tool-head"
                :class="{ clickable: !!tool.detail }"
                @click.stop="toggleTool(sec.index, tIdx, !!tool.detail)"
              >
                <span class="fwd-tool-ico" v-html="toolIcon(tool.name)"></span>
                <span class="fwd-tool-name">{{ tool.name }}</span>
                <span v-if="tool.argHint" class="fwd-tool-arg" :title="tool.argHint">{{ tool.argHint }}</span>
                <span class="fwd-tool-status" :class="'st-' + tool.status">{{ statusLabel(tool.status) }}</span>
                <svg
                  v-if="tool.detail"
                  class="fwd-tool-chev"
                  width="11"
                  height="11"
                  viewBox="0 0 24 24"
                  fill="none"
                  stroke="currentColor"
                  stroke-width="2"
                ><polyline points="6 9 12 15 18 9" /></svg>
              </button>
              <div v-if="tool.detail && isToolOpen(sec.index, tIdx)" class="fwd-tool-detail-wrap">
                <pre class="fwd-tool-detail">{{ tool.detail }}</pre>
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>

    <div class="msg-actions ma-user">
      <button class="ma-btn" type="button" data-msg-act="copy" :data-item-id="item.id">
        <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6"><rect x="9" y="9" width="12" height="12" rx="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/></svg>
        Copy
      </button>
      <button class="ma-btn" type="button" data-msg-act="del" :data-item-id="item.id">
        <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6"><polyline points="3 6 5 6 21 6"/><path d="M19 6l-1 14H6L5 6"/><path d="M10 11v6M14 11v6"/></svg>
        Delete
      </button>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, reactive, ref } from 'vue'
import type { StreamItem } from '../data/stream'
import { clockOf, escapeHtml, renderMarkdown, toolIcon } from '../data/render'

const props = defineProps<{ item: StreamItem }>()

const payload = computed(() => props.item.forwarded)
const collapsed = ref(false)
const openTools = reactive<Record<string, boolean>>({})

const clockLabel = computed(() => {
  if (props.item.time) return props.item.time
  if (props.item.stamp) return clockOf(props.item.stamp)
  return ''
})

function toolKey(secIndex: number, toolIndex: number): string {
  return `${secIndex}:${toolIndex}`
}

function isToolOpen(secIndex: number, toolIndex: number): boolean {
  return Boolean(openTools[toolKey(secIndex, toolIndex)])
}

function toggleTool(secIndex: number, toolIndex: number, hasDetail: boolean): void {
  if (!hasDetail) return
  const key = toolKey(secIndex, toolIndex)
  openTools[key] = !openTools[key]
}

function statusLabel(status: string): string {
  if (status === 'completed') return 'done'
  if (status === 'failed') return 'failed'
  if (status === 'cancelled') return 'cancelled'
  return status || 'done'
}

/** 将正文里的 fenced code block 转为独立代码块，其余段落走 renderMarkdown。 */
function renderSectionBody(text: string): string {
  const src = text.replace(/\r\n/g, '\n')
  const parts: string[] = []
  const fenceRe = /```([^\n`]*)\n([\s\S]*?)```/g
  let last = 0
  let match: RegExpExecArray | null
  while ((match = fenceRe.exec(src)) !== null) {
    if (match.index > last) {
      const prose = src.slice(last, match.index).trim()
      if (prose) parts.push(renderMarkdown(prose, false))
    }
    const lang = match[1].trim()
    const code = match[2].replace(/\n$/, '')
    const langTag = lang ? `<span class="fwd-code-lang">${escapeHtml(lang)}</span>` : ''
    parts.push(`<div class="fwd-code">${langTag}<pre><code>${escapeHtml(code)}</code></pre></div>`)
    last = fenceRe.lastIndex
  }
  if (last < src.length) {
    const tail = src.slice(last).trim()
    if (tail) parts.push(renderMarkdown(tail, false))
  }
  return parts.join('')
}
</script>
