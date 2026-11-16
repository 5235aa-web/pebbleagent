/* Structured message blocks.

   The production shell renders a message as a component tree (bubble -> content
   -> one component per block) instead of assigning one big HTML string, so a
   streaming delta repaints only the block that grew.

   Streaming goes one step further here. Re-parsing the whole growing answer as
   markdown ten times a second is what made the surface unusable on long replies:
   every flush rebuilt tens of KB of HTML. LiveBuilder therefore commits finished
   paragraphs once (as markdown) and keeps the unfinished tail as plain text, so
   a delta only changes the last paragraph's text node. The settled reply is
   rendered as markdown once, on message.complete. */

import { reactive } from 'vue'
import { renderMarkdown, type ToolCallView } from './render'

export type Block =
  | { kind: 'text'; id: string; html: string }
  | { kind: 'raw'; id: string; paras: string[]; caret: boolean }
  | { kind: 'code'; id: string; lang: string; code: string; lines: number; streaming: boolean }
  | { kind: 'mermaid'; id: string; code: string; streaming: boolean }
  | { kind: 'svg'; id: string; code: string; streaming: boolean }
  | { kind: 'tool'; id: string; call: ToolCallView }
  | { kind: 'thinking'; id: string; text: string; live?: boolean }

export type ProcessBlock = Extract<Block, { kind: 'thinking' | 'tool' }>

export interface ProcessSummary {
  hasProcess: boolean
  isLive: boolean
  label: string
  detail: string
  thinkingCount: number
  toolCount: number
  failedCount: number
}

export type ProcessEntry =
  | { kind: 'block'; id: string; block: ProcessBlock }
  | { kind: 'anchor'; id: string; preview: string }

export function isProcessBlock(block: Block): block is ProcessBlock {
  return block.kind === 'thinking' || block.kind === 'tool'
}

export function proseOnlyBlocks(blocks: Block[]): Block[] {
  return blocks.filter(b => !isProcessBlock(b))
}

/** Summarizes thinking and tool activity for the single collapsible action bar. */
export function summarizeAgentProcess(blocks: Block[], streaming: boolean): ProcessSummary {
  let thinkingCount = 0
  let toolCount = 0
  let failedCount = 0
  let activeTool: Extract<Block, { kind: 'tool' }> | null = null

  for (const b of blocks) {
    if (b.kind === 'thinking') {
      thinkingCount += 1
    } else if (b.kind === 'tool') {
      toolCount += 1
      const st = b.call.status
      if (st === 'failed' || st === 'cancelled') failedCount += 1
      if (st === 'running' || st === 'streaming') activeTool = b
    }
  }

  const hasProcess = thinkingCount > 0 || toolCount > 0
  if (!hasProcess) {
    return {
      hasProcess: false,
      isLive: false,
      label: '',
      detail: '',
      thinkingCount: 0,
      toolCount: 0,
      failedCount: 0
    }
  }

  if (streaming) {
    const last = blocks[blocks.length - 1]
    if (activeTool) {
      return {
        hasProcess: true,
        isLive: true,
        label: `Calling ${activeTool.call.name}…`,
        detail: activeTool.call.arg || '',
        thinkingCount,
        toolCount,
        failedCount
      }
    }
    if (last && last.kind === 'thinking') {
      return {
        hasProcess: true,
        isLive: true,
        label: 'Thinking…',
        detail: toolCount > 0 ? `${toolCount} ${toolCount === 1 ? 'tool' : 'tools'}` : '',
        thinkingCount,
        toolCount,
        failedCount
      }
    }
    const parts: string[] = []
    if (thinkingCount > 0) parts.push('Thought')
    if (toolCount > 0) parts.push(`${toolCount} ${toolCount === 1 ? 'tool' : 'tools'}`)
    return {
      hasProcess: true,
      isLive: true,
      label: 'Responding…',
      detail: parts.join(' · '),
      thinkingCount,
      toolCount,
      failedCount
    }
  }

  const settledParts: string[] = []
  if (thinkingCount > 0) {
    settledParts.push(thinkingCount > 1 ? `Thought (${thinkingCount})` : 'Thought')
  }
  if (toolCount > 0) {
    settledParts.push(`${toolCount} ${toolCount === 1 ? 'tool' : 'tools'}`)
  }

  return {
    hasProcess: true,
    isLive: false,
    label: settledParts.join(' · '),
    detail: failedCount > 0 ? `${failedCount} failed` : '',
    thinkingCount,
    toolCount,
    failedCount
  }
}

function prosePreviewOf(block: Block): string {
  if (block.kind === 'raw') {
    return block.paras.join(' ').replace(/\s+/g, ' ').trim()
  }
  if (block.kind === 'text') {
    return block.html
      .replace(/<[^>]+>/g, ' ')
      .replace(/&amp;/g, '&')
      .replace(/&lt;/g, '<')
      .replace(/&gt;/g, '>')
      .replace(/&quot;/g, '"')
      .replace(/&#39;/g, "'")
      .replace(/\s+/g, ' ')
      .trim()
  }
  if (block.kind === 'code') return `${block.lang || 'code'} block`
  if (block.kind === 'mermaid') return 'mermaid diagram'
  if (block.kind === 'svg') return 'svg graphic'
  return ''
}

/** Builds the expanded process timeline, inserting lightweight reply anchors
    where prose appeared before subsequent thinking or tool calls. */
export function buildProcessTimeline(blocks: Block[]): ProcessEntry[] {
  let lastProcessIndex = -1
  for (let i = blocks.length - 1; i >= 0; i--) {
    if (isProcessBlock(blocks[i])) {
      lastProcessIndex = i
      break
    }
  }
  if (lastProcessIndex < 0) return []

  const entries: ProcessEntry[] = []
  let pendingProse: string[] = []
  let anchorSeq = 0

  const flushProseAnchor = () => {
    if (!pendingProse.length) return
    const combined = pendingProse.join(' ').replace(/\s+/g, ' ').trim()
    pendingProse = []
    if (!combined) return
    const clipped = combined.length > 56 ? combined.slice(0, 56).trimEnd() + '…' : combined
    entries.push({ kind: 'anchor', id: `pa-${anchorSeq++}`, preview: clipped })
  }

  for (let i = 0; i <= lastProcessIndex; i++) {
    const b = blocks[i]
    if (isProcessBlock(b)) {
      flushProseAnchor()
      entries.push({ kind: 'block', id: b.id, block: b })
    } else {
      const text = prosePreviewOf(b)
      if (text) pendingProse.push(text)
    }
  }

  return entries
}

/** Splits settled text into markdown runs and fenced code blocks. */
export function splitBlocks(text: string, streaming: boolean, base: string): Block[] {
  const out: Block[] = []
  const re = /```(\w*)\n([\s\S]*?)(```|$)/g
  let last = 0
  let n = 0
  let m: RegExpExecArray | null
  while ((m = re.exec(text)) !== null) {
    const before = text.slice(last, m.index)
    if (before.trim()) out.push({ kind: 'text', id: `${base}-t${n++}`, html: renderMarkdown(before, streaming && m[3] === '') })
    const lang = m[1] || 'code'
    if (lang.toLowerCase() === 'mermaid') {
      // Diagrams get their own block: a mermaid fence is a picture, not code to
      // be syntax-highlighted, and it needs the renderer's own error surface.
      out.push({ kind: 'mermaid', id: `${base}-m${n++}`, code: m[2], streaming: m[3] === '' })
      last = m.index + m[0].length
      continue
    }
    if (lang.toLowerCase() === 'svg') {
      // Same reasoning: an SVG fence is a graphic the browser can draw, so it
      // goes to the image card instead of the highlighter.
      out.push({ kind: 'svg', id: `${base}-s${n++}`, code: m[2], streaming: m[3] === '' })
      last = m.index + m[0].length
      continue
    }
    out.push({
      kind: 'code',
      id: `${base}-c${n++}`,
      lang,
      code: m[2],
      lines: m[2].replace(/\n$/, '').split('\n').length,
      streaming: m[3] === ''
    })
    last = m.index + m[0].length
  }
  const tail = text.slice(last)
  if (tail.trim() || out.length === 0) {
    out.push({ kind: 'text', id: `${base}-t${n++}`, html: renderMarkdown(tail, streaming) })
  }
  return out
}

function fences(text: string): number {
  let n = 0
  let i = text.indexOf('```')
  while (i !== -1) {
    n++
    i = text.indexOf('```', i + 3)
  }
  return n
}

/** Builds live blocks incrementally: finished paragraphs become markdown once,
    the open tail stays as plain paragraphs that Vue patches as text. */
export class LiveBuilder {
  /* Reactive: the row component reads this array directly, so a push has to
     notify Vue without replacing the array identity. */
  blocks = reactive<Block[]>([])
  private tailId: string
  private seq = 0

  constructor(private base: string) {
    this.tailId = `${base}-raw`
  }

  reset() {
    this.blocks.splice(0, this.blocks.length)
    this.seq = 0
  }

  /** Returns the block list; the same array instance is mutated in place so a
      repaint only touches the paragraphs that actually changed. */
  push(delta: string): Block[] {
    let buffer = this.tailText() + delta
    // Commit every finished paragraph. A paragraph that opens a fence is only
    // committed once the fence closes, otherwise it would render as two blocks.
    for (;;) {
      const cut = buffer.indexOf('\n\n')
      if (cut === -1) break
      const chunk = buffer.slice(0, cut)
      if (fences(chunk) % 2 === 1) break
      buffer = buffer.slice(cut + 2)
      if (chunk.trim()) this.commit(chunk)
    }
    this.setTail(buffer)
    return this.blocks
  }

  /** Settles the whole answer through the full markdown pass. */
  finish(fullText: string): Block[] {
    this.blocks.splice(0, this.blocks.length, ...splitBlocks(fullText, false, this.base))
    this.seq = 0
    return this.blocks
  }

  private tailText(): string {
    const tail = this.blocks[this.blocks.length - 1]
    return tail && tail.kind === 'raw' ? tail.paras.join('\n') : ''
  }

  private commit(chunk: string) {
    const tail = this.blocks[this.blocks.length - 1]
    if (tail && tail.kind === 'raw') this.blocks.pop()
    this.blocks.push(...splitBlocks(chunk, false, `${this.base}-m${this.seq++}`))
  }

  private setTail(text: string) {
    const tail = this.blocks[this.blocks.length - 1]
    const paras = text.split('\n')
    if (tail && tail.kind === 'raw') {
      // Mutate in place: the paragraph list identity is what the child component
      // watches, and replacing it would re-create every paragraph element.
      tail.paras.splice(0, tail.paras.length, ...paras)
      return
    }
    this.blocks.push({ kind: 'raw', id: this.tailId, paras, caret: true })
  }
}
