/* Gateway events to store.

   The surface starts on the seeded prototype content; the first real session
   replaces it. Live rows go through render.ts so their markup carries the same
   class names the prototype uses. */

import { gateway, type GatewayEvent } from './gateway'
import { streamState, replaceStream, clearStream } from './stream'
import { notify as notifySound } from './notify'

/* Wall clock of the reply that just ran. The island reads it live while the
   agent works and keeps the final figure once it stops; the next turn resets it
   to zero the moment `streaming` flips back on. Watching the flag rather than
   the four call sites keeps every path (send, resume, interrupt, error) on the
   same clock. */
export const runTiming = reactive({
  startAt: 0,
  elapsedMs: 0,
  running: false,
  /* Output tokens produced by the run, sampled from `usage.tokensOut` at the
     start so the island can show a real rate instead of a constant. */
  startTokens: 0,
  tokensOut: 0
})

watch(
  () => streamState.streaming,
  (now, before) => {
    if (now && !before) {
      runTiming.startAt = Date.now()
      runTiming.elapsedMs = 0
      runTiming.startTokens = usage.tokensOut
      runTiming.tokensOut = 0
      runTiming.running = true
      return
    }
    if (!now && before) {
      runTiming.elapsedMs = runTiming.startAt ? Date.now() - runTiming.startAt : 0
      runTiming.tokensOut = Math.max(0, usage.tokensOut - runTiming.startTokens)
      runTiming.running = false
    }
  }
)
import { permissions, plan, sessions, sessionState, projects, setCatalog, bucketFor, type Session, type SessionGroup } from './store'
import { usage, files, mcp, ledger, context, llm, notes, pairing, search, image, type BillRow, type ClarifyQuestion } from './panels'
import type { StreamItem } from './stream'
import { renderUserBubble, nowClock, clockOf, escapeHtml, type AttachmentView, type ToolCallView, type ToolState } from './render'
import { markPreviewChanged, mergePreviewComments, clearComments } from './preview'
import { fileSize as byteLabel, type WireAttachment } from './fileDrop'
import { nextTick, reactive, watch } from 'vue'
import { LiveBuilder, splitBlocks, type Block } from './blocks'

let seq = 0
function nextId(prefix: string): string {
  seq += 1
  return prefix + '-' + seq
}

/* Full clock for a row the user just sent; history carries its own stamp. */
function stampNow(): string {
  const d = new Date()
  const p = (n: number) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`
}

let pendingUser: string | null = null
let bootstrapped = false

let live: {
  itemId: string
  time: string
  text: string
  /* Reasoning runs append at the tail like every other block, so each one needs
     its own id. A single shared instance was what stacked every later run inside
     the first card and pushed the rest of the answer below it. */
  thinkSeq: number
  builder: LiveBuilder
  blocks: Block[]
  tools: Map<string, ToolCallView>
} | null = null

function itemById(id: string): StreamItem | undefined {
  return streamState.items.find(i => i.id === id)
}

let paintTimer = 0
/** Coalesces delta bursts into one paint every 120ms, the same cadence the
    production shell flushes its streaming message with. Repainting per delta
    rebuilds a growing block, and every paint forces the blurred chrome (header,
    status bar, dock) to resample what is behind it. */
function schedulePaint() {
  if (paintTimer) return
  paintTimer = window.setTimeout(() => {
    paintTimer = 0
    paintAgent()
  }, 120)
}

function paintNow() {
  if (paintTimer) {
    clearTimeout(paintTimer)
    paintTimer = 0
  }
  paintAgent()
}

function paintAgent() {
  if (!live) return
  const item = itemById(live.itemId)
  if (!item) return
  // The block array is reactive and mutated in place, so identity only has to be
  // handed to the row once.
  if (item.blocks !== live.blocks) item.blocks = live.blocks
  /* Only the reasoning run still being written animates its rail. The moment
     anything else lands on top — prose, a tool card, or the next run — the
     previous one is history and its scan stops. A transcript can hold hundreds
     of these, and an infinite animation on every finished run is pure cost. */
  const tail = live.blocks[live.blocks.length - 1]
  for (const b of live.blocks) {
    if (b.kind === 'thinking' && b.live !== undefined) {
      b.live = streamState.streaming && b === tail
    }
  }
  item.streaming = streamState.streaming
  item.time = live.time
  item.model = streamState.model
  /* `plain` was only written when the turn settled, so everything that measures
     a running turn — the island's tok/s, the context split, Copy during a
     stream — read an empty string until the very end. */
  item.plain = live.text
}

/** Tool rows already settled in earlier turns. The status bar counts these plus
    whatever the live row has produced, so the number moves during a turn instead
    of freezing at the value captured when the session opened. */
function countStoredToolRows(): number {
  let n = 0
  for (const item of streamState.items) {
    if (!item.blocks || item.streaming) continue
    for (const b of item.blocks) if (b.kind === 'tool') n++
  }
  return n
}

/** Inserts or updates a tool card. Cards sit after the text block that was
    streaming when they started, ahead of the open tail. */
function upsertTool(id: string, call: ToolCallView) {
  if (!live) return
  live.tools.set(id, call)
  sessionState.toolCalls = countStoredToolRows() + live.tools.size
  const bid = live.itemId + '-tool-' + id
  const existing = live.blocks.findIndex(b => b.kind === 'tool' && b.id === bid)
  if (existing > -1) {
    const block = live.blocks[existing]
    if (block.kind === 'tool') block.call = call
    return
  }
  /* Appended at the tail, never spliced above the open text run: the transcript
     reads in arrival order, so a tool card belongs where the call happened. The
     old splice put every later call ahead of the answer that followed it. */
  const block: Block = { kind: 'tool', id: bid, call }
  live.blocks.push(block)
}

function ensureLive(): NonNullable<typeof live> {
  if (!live) {
    const id = nextId('live')
    const builder = new LiveBuilder(id)
    const blocks = builder.blocks
    live = { itemId: id, time: nowClock(), text: '', thinkSeq: 0, builder, blocks, tools: new Map() }
    streamState.items.push({ id, kind: 'message', className: 'msg agent', attrs: {}, innerHTML: '', blocks, streaming: true })
    streamState.streaming = true
  }
  return live
}

function finishLive(interrupted = false, messageId?: number) {
  if (!live) return
  live.time = nowClock()
  streamState.streaming = false
  // The settled text is the only place `@pebble-file:` directives surface, so
  // the row keeps it for the preview stage to scan.
  const settled = streamState.items.find(i => i.id === live?.itemId)
  if (settled) {
    settled.plain = live.text
    if (interrupted) settled.interrupted = true
    /* The row was drawn under a local id (`live-3`). Until it adopts the stored
       one, Delete cannot reach the gateway — the guard only fires for a numeric
       id — so the row would come back on the next resume. */
    if (messageId) settled.id = String(messageId)
  }
  /* Only the open tail needs settling: it holds plain paragraphs because a
     delta can patch text cheaply, and once the turn ends it becomes markdown
     like everything above it. The blocks around it are left exactly where they
     arrived — reasoning runs and tool cards keeps their place. The previous
     version rebuilt the array as reasoning + text + tools, which is precisely
     the scrambled transcript: every run folded into the first card, every tool
     hoisted to the bottom. */
  const blocks = live.blocks
  const last = blocks[blocks.length - 1]
  if (last && last.kind === 'raw') {
    const tail = last.paras.join('\n')
    blocks.splice(blocks.length - 1, 1, ...(tail.trim() ? splitBlocks(tail, false, `${live.itemId}-tail`) : []))
  }
  /* The turn is over, so nothing is mid-stream any more. Only the tail above is
     rewritten into markdown; the paragraph runs sandwiched between thinking and
     tool cards keep the flag they were opened with, which left a blinking caret
     sitting in the settled transcript until the session was reopened. */
  for (const b of blocks) {
    if (b.kind === 'raw') b.caret = false
  }
  /* A stopped turn leaves calls mid-flight, and the gateway never sends
     tool.complete for a call it killed. Without this they sit on "running"
     forever, so the flag is what settles them. */
  if (interrupted) {
    for (const b of blocks) {
      if (b.kind === 'tool' && (b.call.status === 'running' || b.call.status === 'streaming')) {
        b.call = { ...b.call, status: 'cancelled' }
      }
    }
  }
  paintNow()
  sessionState.messageCount += 1
  live = null
  /* Re-read the usage totals once the turn settles: `message.usage` only carries
     the latest call, so the session counters in the usage pane and the island
     stayed on whatever `refreshPanes` last saw. */
  void refreshPanes()
  // One sound per finished turn; `when` and the per-kind switch decide whether it
  // actually plays, so the call site stays a single line.
  notifySound('finished')
}

/* ---- event handlers ---- */

function onEvent(ev: GatewayEvent) {
  const p = ev.payload
  switch (ev.type) {
    case 'user_message.saved': {
      const text = String(p.text ?? '')
      if (!text) return
      // One exchange per saved prompt, whether this client drew it optimistically
      // or is seeing the row for the first time.
      sessionState.messageCount += 1
      // The row already exists from sendPrompt; adopt the server id.
      if (pendingUser === text) {
        pendingUser = null
        const idx = streamState.items.findIndex(i => i.id.indexOf('pending-') === 0)
        if (idx > -1) {
          streamState.items[idx].id = String(p.message_id ?? streamState.items[idx].id)
          return
        }
      }
      const attachments = Array.isArray(p.attachments)
        ? (p.attachments as Array<Record<string, unknown>>).map(a => ({
            name: String(a.name ?? 'file'),
            size: String(a.size_label ?? a.size ?? ''),
            kind: (a.kind === 'image' ? 'image' : 'file') as AttachmentView['kind']
          }))
        : []
      streamState.items.push({
        id: String(p.message_id ?? nextId('user')),
        kind: 'message',
        className: 'msg user',
        attrs: {},
        innerHTML: renderUserBubble(text, attachments, nowClock(), Number(p.tokens) || undefined),
        plain: text,
        stamp: stampNow()
      })
      return
    }

    case 'message.delta': {
      const cur = ensureLive()
      const t = String(p.text ?? '')
      cur.text += t
      // Commits finished paragraphs as markdown, keeps the open tail as text.
      cur.builder.push(t)
      schedulePaint()
      return
    }

    case 'thinking.delta': {
      const cur = ensureLive()
      const t = String(p.text ?? '')
      /* Same rule the settled transcript uses: merge into the last block when it
         is already reasoning, otherwise start a new run at the tail. The old
         code held one shared block and `unshift`-ed it, so every later run
         landed inside the first card and everything else piled underneath. */
      const last = cur.blocks[cur.blocks.length - 1]
      if (last && last.kind === 'thinking') {
        last.text += t
      } else {
        cur.blocks.push({ kind: 'thinking', id: `${cur.itemId}-th${cur.thinkSeq++}`, text: t, live: true })
      }
      schedulePaint()
      return
    }

    case 'tool.call_streaming':
    case 'tool.start':
    case 'tool.complete': {
      const cur = ensureLive()
      const id = String(p.tool_id ?? p.name ?? nextId('tool'))
      const existing = cur.tools.get(id)
      const name = String(p.name ?? existing?.name ?? 'tool')
      if (ev.type === 'tool.complete') {
        const error = p.error ? String(p.error) : undefined
        upsertTool(id, {
          name,
          arg: existing?.arg,
          status: (error ? 'failed' : 'completed') as ToolState,
          durationS: Number(p.duration_s) || existing?.durationS,
          output: p.result_text ? String(p.result_text) : existing?.output,
          error
        })
      } else {
        const args = String(p.args_text ?? existing?.arg ?? '')
        upsertTool(id, {
          name,
          arg: args.length > 90 ? args.slice(0, 90) + '…' : args,
          status: (ev.type === 'tool.start' ? 'running' : 'streaming') as ToolState,
          input: args,
          durationS: existing?.durationS
        })
      }
      schedulePaint()
      return
    }

    case 'message.complete': {
      finishLive(Boolean(p.interrupted), Number(p.message_id) || undefined)
      return
    }

    case 'todos.update': {
      const todos = Array.isArray(p.todos) ? (p.todos as Array<Record<string, unknown>>) : []
      if (!todos.length) {
        plan.open = false
        return
      }
      plan.open = true
      plan.title = String(p.title ?? plan.title)
      plan.sub = String(p.agent ?? plan.sub)
      plan.steps.splice(0, plan.steps.length,
        ...todos.map((t, i) => ({
          id: String(t.id ?? 'p' + (i + 1)),
          task: String(t.content ?? ''),
          note: String(t.status ?? 'pending'),
          state: (t.status === 'completed' ? 'done' : t.status === 'in_progress' ? 'now' : 'queued') as 'done' | 'now' | 'queued'
        }))
      )
      return
    }

    case 'agent.changed': {
      void refreshLlm()
      return
    }

    case 'model.changed': {
      const model = String(p.model ?? '')
      if (model) streamState.model = model
      // Pricing and the context ring both depend on the model, so the dock is
      // refreshed with the same event that changed the selection.
      void refreshLlm()
      void refreshPanes()
      return
    }

    case 'session.created': {
      const sid = String(p.session_id ?? p.id ?? '')
      if (!sid || sessions.some(s => s.id === sid)) return
      sessions.unshift({
        id: sid,
        index: null,
        title: String(p.title ?? 'New conversation'),
        msgs: 0,
        time: 'now',
        group: 'Today',
        pinned: false
      })
      return
    }

    case 'session.updated': {
      const sid = String(p.session_id ?? '')
      const title = String(p.title ?? '')
      const row = sessions.find(s => s.id === sid || s.id === sessionState.activeId)
      if (row && title) row.title = title
      return
    }

    case 'file.change': {
      const rel = String(p.relative_path ?? '')
      if (!rel) return
      // A preview of that file goes stale (or reloads when it is the one on
      // screen) before the changes list catches up.
      markPreviewChanged(rel)
      void refreshFileChanges()
      return
    }

    case 'files.accepted': {
      const sid = String(p.session_id ?? '')
      if (sid && sessionState.activeId && sid !== sessionState.activeId) return
      files.changes = []
      files.totalFiles = 0
      files.totalAdded = 0
      files.totalRemoved = 0
      files.totalVersions = 0
      return
    }

    case 'files.reverted': {
      const sid = String(p.session_id ?? '')
      if (sid && sessionState.activeId && sid !== sessionState.activeId) return
      void refreshFileChanges()
      return
    }

    case 'clarify.request': {
      const rid = String(p.request_id ?? '')
      let questions = normalizeClarifyQuestions(p.questions)
      if (!questions.length) {
        // Older agents only send the flat pair.
        const flat = String(p.question ?? '')
        if (!flat) return
        questions = [{
          title: 'QUESTION',
          prompt: flat,
          options: (Array.isArray(p.choices) ? p.choices : []).map(c => ({ label: String(c), detail: '' })),
          multiple: false
        }]
      }
      showClarify(rid || 'pending', questions)
      notifySound('question')
      return
    }

    case 'clarify.resolved': {
      const rid = String(p.request_id ?? '')
      if (!rid) return
      if (clarifyRowId === 'clarify-' + rid) dropClarifyRow()
      return
    }

    case 'permission.request': {
      const id = String(p.request_id ?? p.id ?? nextId('perm'))
      if (permissions.queue.some(r => r.id === id)) return
      const level = String(p.risk_level ?? p.risk ?? 'high')
      permissions.queue.push({
        id,
        level: (level === 'critical' || level === 'low' ? level : 'high') as 'critical' | 'high' | 'low',
        icon: /shell|exec|bash|command/.test(String(p.tool_name ?? '')) ? 'command'
          : /write|edit|create|delete|move/.test(String(p.tool_name ?? '')) ? 'write' : 'read',
        kind: String(p.tool_name ?? p.kind ?? 'Tool call'),
        target: String(p.target ?? p.arguments ?? p.args ?? ''),
        targetKey: /shell|exec|bash|command/.test(String(p.tool_name ?? '')) ? 'cmd' : 'path',
        reason: String(p.reason ?? p.description ?? ''),
        reqId: '#' + id.slice(0, 10),
        duration: 15,
        settled: false,
        verdict: null,
        leaving: false
      })
      permissions.open = true
      notifySound('permission')
      return
    }

    case 'permission.resolved': {
      const id = String(p.request_id ?? p.id ?? '')
      const i = permissions.queue.findIndex(r => r.id === id)
      if (i > -1) permissions.queue.splice(i, 1)
      if (!permissions.queue.length) permissions.open = false
      return
    }

    case 'prompt.rejected': {
      const reason = String(p.reason ?? p.message ?? 'prompt rejected')
      const cur = ensureLive()
      const chunk = '\n\n**Error:** ' + reason
      cur.text += chunk
      cur.builder.push(chunk)
      finishLive()
      pendingUser = null
      return
    }

    case 'message.usage': {
      syncUsage(p)
      return
    }

    case 'error': {
      const msg = String(p.message ?? 'Unknown error')
      const cur = ensureLive()
      /* The gateway persists the body it already streamed, so the live row has
         to end up holding that same text. A delta lost between the two sockets
         otherwise leaves the running row shorter than the stored one, and the
         next resume silently grows it. The server copy is authoritative and is
         always a superset of what arrived here, so only the missing tail is
         pushed; anything else (an early failure with no text at all) is left
         untouched rather than being overwritten. */
      const serverText = typeof p.content === 'string' ? p.content : ''
      if (serverText && serverText.startsWith(cur.text)) {
        const missing = serverText.slice(cur.text.length)
        if (missing) {
          cur.text += missing
          cur.builder.push(missing)
        }
      }
      /* A call killed mid-flight never receives its completion row. Restoring
         the turn marks such cards cancelled, so the live one has to say the
         same thing or the card spins here until the next reload. */
      for (const [toolId, call] of cur.tools) {
        if (call.status === 'running' || call.status === 'streaming') {
          upsertTool(toolId, { ...call, status: 'cancelled' })
        }
      }
      const chunk = '\n\n**Error:** ' + msg
      cur.text += chunk
      cur.builder.push(chunk)
      finishLive(true, Number(p.message_id) || undefined)
      return
    }

    case 'gateway.ready': {
      const model = String(p.model ?? '')
      if (model) streamState.model = model
      void refreshLlm()
      void pullOrbitTheme()
      if (!bootstrapped) {
        bootstrapped = true
        void bootstrap()
      } else {
        void refreshPanes()
      }
      window.dispatchEvent(new CustomEvent('pebble:gateway-ready'))
      return
    }

    default:
      return
  }
}

export function connectGateway() {
  gateway.onEvent(onEvent)
  gateway.onStatus(connected => { streamState.gatewayOnline = connected })
  bindThemeSync()
  gateway.connect()
}

const BOUNDARY_PREFIX = '[COMPACT_BOUNDARY:'

interface CompactSegment {
  bid: string
  /** Folded rows, [start, end). */
  start: number
  end: number
  /** Boundary row that renders the divider in their place. */
  anchor: number
  count: number
  tools: number
}

interface CompactionPlan {
  anchorAt: Map<number, CompactSegment>
  memberOf: Map<number, CompactSegment>
  byBid: Map<string, CompactSegment>
}

const emptyPlan: CompactionPlan = { anchorAt: new Map(), memberOf: new Map(), byBid: new Map() }

/** Transcript of the session on screen, kept so a fold toggle can rebuild the
    list without another resume round trip. */
let lastHistory: Array<Record<string, unknown>> = []
let lastPlan: CompactionPlan = emptyPlan
/** Rendered rows per boundary, filled the first time a run is opened and reused
    afterwards: a compacted run can hold hundreds of rows and re-rendering them
    on every toggle is what makes an expand feel laggy. */
const segmentCache = new Map<string, StreamItem[]>()
const segmentBuild = new Map<string, StreamItem[]>()
const expandedCompacts = new Set<string>()

function boundaryId(m: Record<string, unknown>): string | null {
  if (m.role !== 'system') return null
  const text = String(m.text ?? '')
  if (!text.startsWith(BOUNDARY_PREFIX)) return null
  const close = text.indexOf(']', BOUNDARY_PREFIX.length)
  return close > BOUNDARY_PREFIX.length ? text.slice(BOUNDARY_PREFIX.length, close) : null
}

function isFolded(m: Record<string, unknown>): boolean {
  const v = m.is_compacted
  return v === true || v === 1 || v === '1' || v === 'true' || v === 'True'
}

function isSummaryRow(m: Record<string, unknown>): boolean {
  return String(m.is_compact_summary ?? '') === 'True' || m.is_compact_summary === true
}

/** What the header counts: one entry per message the user or the model actually
    exchanged. Tool rows, compaction boundaries and the compressor's summary are
    transcript bookkeeping, and a single turn can add dozens of tool rows, so
    counting every stored row inflated the total by an order of magnitude. */
function isConversationRow(m: Record<string, unknown>): boolean {
  const role = String(m.role ?? '')
  if (role !== 'user' && role !== 'assistant') return false
  return !isSummaryRow(m)
}

/** Splits the transcript at each compaction boundary.

    Everything above a boundary that the backend marked `is_compacted` collapses
    into that boundary's divider. A run is foldable only when every rendered row
    in it is marked, so a half-marked transcript still renders in full instead of
    hiding rows the user never compacted. */
function planCompaction(messages: Array<Record<string, unknown>>): CompactionPlan {
  const anchorAt = new Map<number, CompactSegment>()
  const memberOf = new Map<number, CompactSegment>()
  const byBid = new Map<string, CompactSegment>()
  const anchors: number[] = []
  messages.forEach((m, i) => { if (boundaryId(m)) anchors.push(i) })

  for (let k = 0; k < anchors.length; k++) {
    const anchor = anchors[k]
    const start = k === 0 ? 0 : anchors[k - 1] + 1
    if (anchor <= start) continue
    let count = 0
    let tools = 0
    let foldable = true
    for (let i = start; i < anchor; i++) {
      const m = messages[i]
      if (m.role === 'tool') { tools += 1; continue }
      if (m.role !== 'user' && m.role !== 'assistant') continue
      if (m.role === 'user' && !String(m.text ?? '').trim()) continue
      count += 1
      if (!isFolded(m)) { foldable = false; break }
    }
    if (!foldable || count === 0) continue
    const bid = boundaryId(messages[anchor])
    if (!bid) continue
    const seg: CompactSegment = { bid, start, end: anchor, anchor, count, tools }
    anchorAt.set(anchor, seg)
    byBid.set(bid, seg)
    for (let i = start; i < anchor; i++) memberOf.set(i, seg)
  }
  return { anchorAt, memberOf, byBid }
}

/** Buffer a folded run's rows while they are being built for an opened run. */
function segmentSink(bid: string): StreamItem[] {
  let buf = segmentBuild.get(bid)
  if (!buf) { buf = []; segmentBuild.set(bid, buf) }
  return buf
}

/** The rows a boundary should render: none while folded, the cache when the run
    was opened before, freshly built rows otherwise. */
function flushSegment(seg: CompactSegment): StreamItem[] {
  if (!expandedCompacts.has(seg.bid)) { segmentBuild.delete(seg.bid); return [] }
  const cached = segmentCache.get(seg.bid)
  if (cached) { segmentBuild.delete(seg.bid); return cached }
  const built = segmentBuild.get(seg.bid) ?? []
  segmentBuild.delete(seg.bid)
  segmentCache.set(seg.bid, built)
  return built
}

/** Turns the stored transcript from session.resume into stream blocks.

    Message shapes come from gateway/session.py get_messages: assistant rows
    carry text, optional reasoning and an OpenAI-style tool_calls JSON string;
    tool rows carry the output keyed by tool_call_id. */
function buildHistory(messages: Array<Record<string, unknown>>, only?: CompactSegment): StreamItem[] {
  const blocks: StreamItem[] = []
  const outputs = new Map<string, { name: string; text: string }>()
  for (const m of messages) {
    if (m.role === 'tool') outputs.set(String(m.tool_call_id ?? ''), { name: String(m.name ?? 'tool'), text: String(m.text ?? '') })
  }

  // A one-run rebuild renders rows out of the transcript already on screen, so
  // it must reuse the cached plan instead of planning again.
  if (!only) {
    lastHistory = messages
    lastPlan = planCompaction(messages)
  }
  const plan = lastPlan

  messages.forEach((m, index) => {
    if (only && plan.memberOf.get(index) !== only) return
    const id = String(m.message_id ?? 'h' + index)
    const time = clockOf(m.created_at) || nowClock()

    // A boundary row renders the divider instead of anything of its own.
    const anchor = plan.anchorAt.get(index)
    if (anchor) {
      const rows = flushSegment(anchor)
      if (rows.length) blocks.push(...rows)
      blocks.push({
        id: 'cd-' + anchor.bid,
        kind: 'compact',
        className: 'cdiv',
        attrs: { 'data-cb-id': anchor.bid },
        innerHTML: '',
        compact: { bid: anchor.bid, count: anchor.count, tools: anchor.tools, collapsed: !expandedCompacts.has(anchor.bid) }
      })
      return
    }

    // Folded runs are skipped before any markup is built: the point of the
    // divider is that those rows cost no DOM at all while they stay closed.
    const seg = plan.memberOf.get(index)
    if (seg && !expandedCompacts.has(seg.bid)) return
    // An opened run that was built before is spliced in whole at its divider.
    if (seg && segmentCache.has(seg.bid)) return
    const sink = seg ? segmentSink(seg.bid) : blocks

    // Compaction summaries are stored as `role: 'user'` with the flag set, so
    // this has to run before the user branch. Checking after it (as a row type
    // only assistants carry) left the whole multi-thousand character summary
    // rendering as a black outgoing bubble.
    if (isSummaryRow(m)) {
      const summary = String(m.text ?? '')
      if (!summary.trim()) return
      sink.push({ id, kind: 'summary', className: 'summary', attrs: {}, innerHTML: summaryInner(stripCompactSummary(summary)) })
      return
    }

    if (m.role === 'user') {
      const text = String(m.text ?? '')
      if (!text) return
      sink.push({ id, kind: 'message', className: 'msg user', attrs: {}, innerHTML: renderUserBubble(text, [], time, undefined, id), plain: text, stamp: String(m.created_at ?? '') })
      return
    }
    if (m.role !== 'assistant') return

    const text = String(m.text ?? '')
    const reasoning = String(m.reasoning ?? '')

    // tool_calls holds the agent's execution order, not OpenAI tool calls:
    // [{ type: 'reasoning' | 'text' | 'tool' | 'question', content?, tool_info? }]
    // Reading it as OpenAI calls made every restored card look "running", which
    // left over a thousand infinite pulse animations running on the page.
    let order: Array<Record<string, unknown>> = []
    if (typeof m.tool_calls === 'string' && m.tool_calls.trim()) {
      try {
        const parsed = JSON.parse(m.tool_calls)
        if (Array.isArray(parsed)) order = parsed as Array<Record<string, unknown>>
      } catch { order = [] }
    }

    const blockList: Block[] = []
    let seq = 0
    for (const item of order) {
      const type = String(item.type ?? '')
      if (type === 'reasoning') {
        const content = String(item.content ?? '')
        // Every reasoning run keeps its place in the order: reasoning, text and
        // tools interleave, and the transcript must read in the order the agent
        // produced them.
        if (content.trim()) blockList.push({ kind: 'thinking', id: id + '-th' + (seq++), text: content.trim(), live: false })
      } else if (type === 'text') {
        const content = String(item.content ?? '')
        if (content.trim()) blockList.push(...splitBlocks(content, false, id + '-b' + (seq++)))
      } else if (type === 'tool') {
        const info = (item.tool_info ?? item.toolInfo ?? {}) as Record<string, unknown>
        const tid = String(info.id ?? id + '-t' + (seq++))
        const input = String(info.input ?? '')
        const stored = outputs.get(tid)
        blockList.push({
          kind: 'tool',
          id: id + '-tool-' + tid,
          call: {
            name: String(info.name ?? 'tool'),
            arg: input.length > 90 ? input.slice(0, 90) + '\u2026' : input,
            status: mapToolStatus(String(info.status ?? '')),
            durationS: info.duration ? Number(info.duration) / 1000 : undefined,
            input,
            output: info.output ? String(info.output) : stored?.text,
            error: info.error ? String(info.error) : undefined
          }
        })
      }
    }

    // Rows restored without an execution order fall back to the columns.
    if (!order.length) {
      if (reasoning.trim()) blockList.push({ kind: 'thinking', id: id + '-th', text: reasoning.trim(), live: false })
      if (text.trim()) blockList.push(...splitBlocks(text, false, id + '-b'))
    }

    if (!blockList.length) return
    /* A restored turn carries the same flag the live one got: the gateway stored
       it, and a call that was killed mid-flight has no completion row, so its
       card would otherwise come back as "running" forever. */
    const stopped = Boolean(m.interrupted)
    if (stopped) {
      for (const b of blockList) {
        if (b.kind === 'tool' && (b.call.status === 'running' || b.call.status === 'streaming')) {
          b.call = { ...b.call, status: 'cancelled' }
        }
      }
    }
    sink.push({
      id,
      kind: 'message',
      className: 'msg agent',
      attrs: {},
      innerHTML: '',
      blocks: blockList,
      model: streamState.model,
      time,
      streaming: false,
      plain: text,
      interrupted: stopped || undefined
    })
  })
  if (only) {
    const built = segmentBuild.get(only.bid) ?? []
    segmentBuild.delete(only.bid)
    return built
  }
  return blocks
}

function mapToolStatus(raw: string): ToolState {
  const v = raw.toLowerCase()
  if (v === 'completed' || v === 'done' || v === 'success') return 'completed'
  if (v === 'failed' || v === 'error') return 'failed'
  if (v === 'cancelled' || v === 'canceled') return 'cancelled'
  if (v === 'running') return 'running'
  return 'streaming'
}

/** Compaction summaries carry the scaffolding the compressor prepends for the
    model. The card shows the summary itself, the same way the production shell
    strips it before rendering. */
function stripCompactSummary(text: string): string {
  let s = text
  s = s.replace(/^This session is being continued from a previous conversation that ran out of context\. The summary below covers the earlier portion of the conversation\.\s*/s, '')
  s = s.replace(/\n\nIf you need specific details from before compaction \(like exact code snippets, error messages, or content you generated\), read the full transcript at:[\s\S]*/s, '')
  s = s.replace(/\n\nRecent messages are preserved verbatim\./s, '')
  s = s.replace(/\nContinue the conversation from where it left off without asking the user any further questions[\s\S]*/s, '')
  const trimmed = s.trim()
  return trimmed || text.trim()
}

/** The compressor writes GitHub-flavoured markdown, but the card used to pour
    the whole thing into one escaped paragraph, so headings and bullets all
    collapsed into a wall of text. Sections are split first, each body is parsed
    into lists and paragraphs, and only the inline spans stay markdown. */
function summaryTone(heading: string): string {
  if (/决定|结论|决策|decision|decided|chosen/i.test(heading)) return 'accent'
  if (/待办|后续|下一步|todo|next|pending|outstanding|remaining/i.test(heading)) return 'gold'
  if (/线索|上下文|参考|文件|clue|context|reference|files|state/i.test(heading)) return 'teal'
  return 'muted'
}

function inlineSummary(text: string): string {
  const codes: string[] = []
  let s = text.replace(/`([^`]+)`/g, (_all, code: string) => {
    codes.push('<code class="cs-code">' + escapeHtml(code) + '</code>')
    return '\u0000' + (codes.length - 1) + '\u0000'
  })
  s = escapeHtml(s)
  s = s.replace(/\*\*([^*]+)\*\*/g, '<b>$1</b>')
  s = s.replace(/(^|[\s(])\*([^*\n]+)\*/g, '$1<i>$2</i>')
  s = s.replace(/\u0000(\d+)\u0000/g, (_all, i: string) => codes[Number(i)] || '')
  return s
}

interface SummarySection { title: string; body: string[] }

function splitSummary(text: string): SummarySection[] {
  const lines = text.replace(/\r\n/g, '\n').split('\n')
  const out: SummarySection[] = []
  let cur: SummarySection = { title: '', body: [] }
  const flush = () => {
    if (cur.title || cur.body.some(l => l.trim())) out.push(cur)
    cur = { title: '', body: [] }
  }
  for (const line of lines) {
    const h = /^\s{0,3}#{1,6}\s+(.*)$/.exec(line)
    if (h) { flush(); cur.title = h[1].trim(); continue }
    cur.body.push(line)
  }
  flush()
  return out
}

function summaryItem(text: string): string {
  return '<li><span class="cs-mk"></span><span class="cs-tx">' + inlineSummary(text) + '</span></li>'
}

function summaryBody(lines: string[]): string {
  const out: string[] = []
  let para: string[] = []
  let i = 0
  const flushPara = () => {
    if (!para.length) return
    out.push('<p class="cs-para">' + para.map(inlineSummary).join('<br>') + '</p>')
    para = []
  }
  while (i < lines.length) {
    const line = lines[i]
    if (!line.trim()) { flushPara(); i++; continue }
    if (/^\s*[-*+]\s+/.test(line)) {
      flushPara()
      const items: string[] = []
      while (i < lines.length && /^\s*[-*+]\s+/.test(lines[i])) {
        items.push(summaryItem(lines[i].replace(/^\s*[-*+]\s+/, '')))
        i++
      }
      out.push('<ul class="cs-items">' + items.join('') + '</ul>')
      continue
    }
    if (/^\s*\d+[.)]\s+/.test(line)) {
      flushPara()
      const items: string[] = []
      while (i < lines.length && /^\s*\d+[.)]\s+/.test(lines[i])) {
        items.push(summaryItem(lines[i].replace(/^\s*\d+[.)]\s+/, '')))
        i++
      }
      out.push('<ol class="cs-items cs-ol">' + items.join('') + '</ol>')
      continue
    }
    para.push(line)
    i++
  }
  flushPara()
  return out.join('')
}

function summarySection(sec: SummarySection, index: number): string {
  const body = summaryBody(sec.body)
  if (!body) return ''
  const items = (body.match(/<li>/g) || []).length
  const num = String(index + 1).padStart(2, '0')
  const title = sec.title ? inlineSummary(sec.title) : 'Overview'
  return (
    '<section class="cs-sec" data-tone="' + summaryTone(sec.title) + '">' +
    '<span class="cs-sec-num">' + num + '</span>' +
    '<span class="cs-sec-hd"><span class="cs-sec-title">' + title + '</span>' +
    (items ? '<span class="cs-sec-count">' + items + '</span>' : '') +
    '</span>' +
    '<div class="cs-sec-body">' + body + '</div>' +
    '</section>'
  )
}

function summaryInner(text: string): string {
  const sections = splitSummary(text)
  const rendered = sections.map(summarySection).filter(Boolean).join('')
  const body = rendered || ('<p class="cs-para">' + inlineSummary(text) + '</p>')
  return (
    '<div class="summary-hdr" role="button" tabindex="0" aria-expanded="true">' +
    '<span class="summary-ico"><svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><polyline points="4 14 10 14 10 20"/><polyline points="20 10 14 10 14 4"/></svg></span>' +
    '<span class="summary-head"><span class="summary-title">Context compacted</span>' +
    '<span class="summary-meta">summary of the earlier turns</span></span>' +
    '<span class="summary-chevron"><svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="6 9 12 15 18 9"/></svg></span>' +
    '</div>' +
    '<div class="summary-body"><div class="summary-inner"><div class="cs-doc">' +
    body +
    '</div></div></div>'
  )
}

/** `info.tools` and `info.skills` are grouped maps (`{"Agent": [...]}`), not
    flat arrays, so the count has to walk one level in. */
function countGrouped(value: unknown): number {
  if (Array.isArray(value)) return value.length
  if (value && typeof value === 'object') {
    return Object.values(value as Record<string, unknown>).reduce<number>(
      (n, group) => n + (Array.isArray(group) ? group.length : 0),
      0
    )
  }
  return 0
}

export async function loadSession(sessionId: string): Promise<void> {
  if (!gateway.connected) return
  try {
    const result = await gateway.request<{
      session_id?: string
      messages?: Array<Record<string, unknown>>
      info?: { model?: string; title?: string }
    }>('session.resume', { session_id: sessionId })
    const sid = String(result?.session_id ?? sessionId)
    streamState.sessionId = sid
    sessionState.activeId = sessionId
    const projectPath = String((result as Record<string, unknown>)?.project_path ?? '')
    if (projectPath) sessionState.projectPath = projectPath
    // Boundary ids belong to one session, so a new resume drops every fold
    // cache and every open run instead of carrying them across.
    segmentCache.clear()
    segmentBuild.clear()
    expandedCompacts.clear()
    dropClarifyRow()
    if (result?.info?.model) streamState.model = result.info.model
    const info = result?.info as Record<string, unknown> | undefined
    sessionState.toolCount = countGrouped(info?.tools)
    sessionState.skillCount = countGrouped(info?.skills)
    setCatalog(info?.tools, info?.skills)
    const restored = result?.messages ?? []
    // The header counts exchanges, not stored rows: tool output and compaction
    // bookkeeping live in the same table but are not messages anyone sent.
    sessionState.messageCount = restored.filter(isConversationRow).length
    replaceStream(buildHistory(restored))
    /* Tool calls live inside block arrays now, not as standalone `role: 'tool'`
       rows, so counting message rows under-reported the session total. */
    sessionState.toolCalls = countStoredToolRows()
    void refreshPanes()
    // A question the agent is still waiting on has no local copy after a
    // reconnect, so ask the gateway for it.
    void pullClarify()
    window.dispatchEvent(new CustomEvent('pebble:gateway-ready'))
    pinTranscript()
  } catch {
    /* keep the seeded surface when the gateway cannot answer */
  }
}

/** Renders one compacted run, reusing the rows built the last time it opened. */
function renderSegmentRows(seg: CompactSegment): StreamItem[] {
  const cached = segmentCache.get(seg.bid)
  if (cached) return cached
  const built = buildHistory(lastHistory, seg)
  segmentCache.set(seg.bid, built)
  return built
}

/* ---------------- sessions ---------------- */

/** Buckets a row by its relative timestamp, the same way the list is grouped. */
function groupForTime(time: string): SessionGroup {
  if (time === 'now' || /^\d+[mh] ago$/.test(time)) return 'Today'
  if (time === '1d ago') return 'Yesterday'
  return 'Earlier'
}

/** Renames a session. The row updates at once and rolls back on refusal, so the
    list never shows a title the gateway did not keep. */
export function renameSession(id: string, title: string): void {
  const row = sessions.find(s => s.id === id)
  const next = title.trim()
  if (!row || !next || next === row.title) return
  const prev = row.title
  row.title = next
  if (!gateway.connected) return
  void gateway.request('session.title', { session_id: id, title: next }).catch(() => {
    const cur = sessions.find(s => s.id === id)
    if (cur) cur.title = prev
  })
}

/** Deletes a session and opens the neighbour when it was the one on screen. */
export async function deleteSession(id: string): Promise<void> {
  const i = sessions.findIndex(s => s.id === id)
  if (!id || i < 0) return
  const wasActive = sessionState.activeId === id || streamState.sessionId === id
  sessions.splice(i, 1)
  if (gateway.connected) {
    try {
      await gateway.request('session.delete', { session_id: id })
    } catch {
      /* the row stays gone: the refusal belongs to the gateway, not the user */
    }
  }
  if (!wasActive) return
  const next = sessions[i] ?? sessions[i - 1] ?? null
  if (!next) {
    sessionState.activeId = ''
    streamState.sessionId = null
    clearStream()
    return
  }
  await loadSession(next.id)
}

/** Pins a session. The gateway has no pin column, so this stays per client. */
export function toggleSessionPin(id: string): boolean {
  const row = sessions.find(s => s.id === id)
  if (!row) return false
  row.pinned = !row.pinned
  row.group = row.pinned ? 'Pinned' : groupForTime(row.time)
  return row.pinned
}

/* ---------------- permission mode ---------------- */

/** Reads the mode the gateway is actually in, so the segmented control starts on
    the truth instead of the markup's default. */
export async function syncPermissionMode(): Promise<string | null> {
  if (!gateway.connected) return null
  try {
    const res = await gateway.request<{ mode?: string }>('permission.mode.get', {})
    return typeof res?.mode === 'string' ? res.mode : null
  } catch {
    return null
  }
}

export function setPermissionMode(mode: string): void {
  if (!gateway.connected || !mode) return
  void gateway.request('permission.mode.set', { mode }).catch(() => {
    /* syncPermissionMode re-reads the truth on the next connect */
  })
}

/** Folds or unfolds one compacted run.

    The toggle edits the rendered list in place rather than rebuilding it from the
    transcript. Replies that arrived after the session was resumed exist only in
    `streamState.items`, and a rebuild would silently drop them, so folding early
    history must never touch rows it does not own.

    The divider is pinned across the change as well: opening a run inserts its
    rows above the line, and without the compensation the line leaves the
    viewport and the click reads as if nothing happened. */
export function toggleCompact(bid: string): void {
  const seg = lastPlan.byBid.get(bid)
  const items = streamState.items
  const at = items.findIndex(i => i.kind === 'compact' && i.compact?.bid === bid)
  if (!seg || at < 0) return

  const body = document.getElementById('streamBody')
  const line = body?.querySelector<HTMLElement>(`[data-cb-id="${bid}"]`)
  const top = line ? line.getBoundingClientRect().top : null
  const divider = items[at]

  if (expandedCompacts.has(bid)) {
    const rows = segmentCache.get(bid) ?? []
    if (rows.length) {
      const ids = new Set(rows.map(r => r.id))
      for (let i = at - 1; i >= 0; i--) {
        if (ids.has(items[i].id)) items.splice(i, 1)
      }
    }
    expandedCompacts.delete(bid)
    if (divider.compact) divider.compact.collapsed = true
  } else {
    // The run has to be marked open before it is rendered: the builder skips
    // folded rows, so rendering first would return nothing.
    expandedCompacts.add(bid)
    const rows = renderSegmentRows(seg)
    if (rows.length) items.splice(at, 0, ...rows)
    if (divider.compact) divider.compact.collapsed = false
  }

  if (top === null || !body) return
  void nextTick().then(() => {
    const moved = body.querySelector<HTMLElement>(`[data-cb-id="${bid}"]`)
    if (!moved) return
    const delta = moved.getBoundingClientRect().top - top
    if (!delta) return
    // .stream-body sets scroll-behavior: smooth, and assigning scrollTop with it
    // in effect animates the compensation instead of applying it, so the divider
    // still drifts away. Suspend it for the one write.
    const prev = body.style.scrollBehavior
    body.style.scrollBehavior = 'auto'
    body.scrollTop += delta
    body.style.scrollBehavior = prev
  })
}

/* ---------------- clarify ---------------- */

/** The card currently on screen, so a resolve from another client can take it
    away and a session switch can drop it. */
let clarifyRowId: string | null = null

function dropClarifyRow(): void {
  if (!clarifyRowId) return
  const id = clarifyRowId
  clarifyRowId = null
  const at = streamState.items.findIndex(i => i.id === id)
  if (at > -1) streamState.items.splice(at, 1)
}

/** Question payloads arrive in two shapes: the structured `questions` list and
    the older flat `question` + `choices` pair. Both collapse to one card model. */
function normalizeClarifyQuestions(raw: unknown): ClarifyQuestion[] {
  const list = Array.isArray(raw) ? raw : raw ? [raw] : []
  const out: ClarifyQuestion[] = list.map(q => {
    const rec = (q ?? {}) as Record<string, unknown>
    const opts = Array.isArray(rec.options) ? rec.options : []
    return {
      title: String(rec.title ?? 'QUESTION'),
      prompt: String(rec.prompt ?? ''),
      options: opts.map(o => {
        const row = (o ?? {}) as Record<string, unknown>
        return { label: String(row.label ?? ''), detail: String(row.detail ?? '') }
      }),
      multiple: Boolean(rec.multiple)
    }
  }).filter(q => q.prompt)
  return out
}

/** Appends the question card to the end of the transcript. A resolve from the
    phone can arrive for a request this client never saw, so the row id is kept
    rather than matched against the payload alone. */
function showClarify(requestId: string, questions: ClarifyQuestion[]): void {
  if (!requestId || !questions.length) return
  dropClarifyRow()
  const id = 'clarify-' + requestId
  clarifyRowId = id
  streamState.items.push({
    id,
    kind: 'clarify',
    className: 'qcard',
    attrs: {},
    innerHTML: '',
    clarify: { requestId, questions }
  })
}

/** Resolves true once the gateway accepted the answer. A false result leaves the
    card editable, so a dropped connection costs one click instead of the reply. */
export function respondClarify(requestId: string, answers: Record<string, string>): Promise<boolean> {
  if (!gateway.connected || !requestId) return Promise.resolve(false)
  // Annotations made on the preview travel with the answer that asks for a
  // change, so the agent gets them as the instruction instead of a side note.
  const merged = mergePreviewComments(answers)
  const flat = Object.values(merged).join(', ')
  return gateway.request('clarify.respond', { request_id: requestId, answer: flat, answers: merged })
    .then(() => { clearComments(); return true })
    .catch(() => false)
}

export function skipClarify(requestId: string): Promise<boolean> {
  return respondClarify(requestId, {})
}

/** A reconnecting client has no local copy of a question the agent is still
    waiting on; QuestionManager holds it process-wide, so ask for it. */
export async function pullClarify(): Promise<void> {
  if (!gateway.connected) return
  try {
    const res = await gateway.request<{ questions?: unknown; session_id?: string }>('clarify.list', {})
    const raw = res?.questions
    if (!raw) return
    const owner = String(res?.session_id ?? '')
    if (owner && sessionState.activeId && owner !== sessionState.activeId) return
    const questions = normalizeClarifyQuestions(raw)
    if (!questions.length) return
    showClarify('pending', questions)
  } catch {
    /* no pending question */
  }
}

/** Fire-and-forget: the queue row leaves on the local animation schedule and
    permission.resolved confirms it. Repeated calls for the same id are dropped
    because the request is already gone from the queue. */
export function decidePermission(id: string, approved: boolean, rememberMinutes: number): void {
  if (!gateway.connected) return
  void gateway.request('permission.decide', {
    request_id: id,
    approved,
    remember_minutes: rememberMinutes
  }).catch(() => { /* the row is already gone locally */ })
}

/** The transcript owns its scroll offset, but two moments belong at the bottom
    regardless of where the reader left it: sending a prompt and opening another
    session. Announced on document so the scroll surface keeps owning the logic. */
function pinTranscript(): void {
  document.dispatchEvent(new CustomEvent('pebble:stick-bottom'))
}

/** Sends a prompt. The row appears immediately and is reconciled when the
    gateway pushes user_message.saved back with the real message id. */
export function sendPrompt(text: string, attachments: WireAttachment[] = []): boolean {
  const body = text.trim()
  // A turn may be attachments only: the old shell allowed the same.
  if (!body && !attachments.length) return false
  if (!gateway.connected) return false
  const sid = streamState.sessionId || sessionState.activeId
  /* The gateway uses this to set the turn's working directory and to render the
     project path into the system prompt. Without it the backend falls back to
     its own cwd, which in the packaged app is the sidecar's install directory. */
  const projectPath = projects.active || sessionState.projectPath || ''
  if (!gateway.post('prompt.submit', {
    session_id: sid,
    text: body,
    attachments,
    project_path: projectPath || undefined
  })) return false
  pendingUser = body
  const pendingId = nextId('pending')
  const views: AttachmentView[] = attachments.map(a => ({
    name: a.name,
    size: byteLabel(a.size),
    kind: (a.detail ? 'image' : 'file') as AttachmentView['kind']
  }))
  streamState.items.push({
    id: pendingId,
    kind: 'message',
    className: 'msg user',
    attrs: {},
    innerHTML: renderUserBubble(body, views, nowClock(), undefined, pendingId),
    plain: body,
    stamp: stampNow()
  })
  streamState.streaming = true
  pinTranscript()
  return true
}

export function interruptSession(): boolean {
  const sid = streamState.sessionId || sessionState.activeId
  if (!gateway.connected || !sid) return false
  const ok = gateway.post('session.interrupt', { session_id: sid })
  if (ok) streamState.streaming = false
  return ok
}

/** Mirrors the per-turn usage the gateway pushes after each call into the dock.
    Every field is optional on the wire, so a payload that only carries tokens
    still updates tokens and leaves the cost line alone. */
function syncUsage(p: Record<string, unknown>): void {
  const cost = Number(p.session_cost_cny ?? p.session_cost ?? NaN)
  if (Number.isFinite(cost)) usage.sessionCost = cost
  const tin = Number(p.latest_input_tokens ?? NaN)
  const tout = Number(p.latest_output_tokens ?? NaN)
  /* `latest_*` describe one call, while `usage.stats` reports the session total.
     Writing the single call into `tokensIn`/`tokensOut` made the pane flip between
     400k and 490M depending on which writer ran last, so the per-call figure only
     feeds the window occupancy. */
  if (Number.isFinite(tin)) usage.contextTokens = tin
  if (Number.isFinite(tout)) usage.contextOut = tout
  const cache = Number(p.cache_hit_tokens ?? NaN)
  if (Number.isFinite(cache)) usage.cacheHit = cache
  const limit = Number(p.model_context_limit ?? NaN)
  if (Number.isFinite(limit) && limit > 0) {
    usage.contextLimit = limit
    /* Only this payload carries the window size, so it is remembered for the next
       page load; otherwise the ring has no denominator after a reload. */
    try { window.localStorage.setItem('pebble.ctxLimit', String(limit)) } catch { /* private mode */ }
    if (Number.isFinite(tin)) {
      usage.contextPct = Math.max(0, Math.min(100, Math.round((tin / limit) * 100)))
    }
  }
  if (typeof p.period === 'string') usage.currency = p.period.toLowerCase() === 'cny' ? 'cny' : 'usd'

  const call = p.this_call as Record<string, unknown> | undefined
  if (!call) return
  const row: BillRow = {
    model: String(call.model ?? p.model ?? 'model'),
    tokensIn: Number(call.input_tokens ?? tin ?? 0),
    tokensOut: Number(call.output_tokens ?? tout ?? 0),
    cost: Number(call.total_cost ?? call.cost_cny ?? 0),
    tint: ''
  }
  usage.history = [row, ...usage.history].slice(0, 8)
  usage.calls += 1
}

/* ---------------- path whitelist ---------------- */

/** Reads the filesystem boundaries. The gateway stores plain absolute paths, so
    every row reads back with full rights; the R / RWX segment is a display
    control and does not change what the backend enforces. */
export async function refreshPaths(): Promise<void> {
  if (!gateway.connected) return
  try {
    const res = await gateway.request<{ paths?: unknown }>('path_whitelist.read', {})
    const list = Array.isArray(res?.paths) ? res.paths.map(String) : []
    files.paths = list.map(p => ({ path: p, mode: 'read · write · execute' }))
  } catch { /* keep the prototype roots */ }
}

function pushPaths(): void {
  void gateway.request('path_whitelist.save', { paths: files.paths.map(p => p.path) })
    .catch(() => { void refreshPaths() })
}

export function addWhitelistPath(path: string): boolean {
  const p = path.trim()
  if (!p || !gateway.connected) return false
  if (files.paths.some(x => x.path === p)) return false
  // The gateway refuses more than five roots; the pane must not offer a sixth.
  if (files.paths.length >= 5) return false
  files.paths = [...files.paths, { path: p, mode: 'read · write · execute' }]
  pushPaths()
  return true
}

/* ---------------- experience ledger ---------------- */

export interface NewMemory {
  kind: string
  statement: string
  rationale: string
  scope: string
  tools: string[]
  confidence: number
}

export function createMemory(entry: NewMemory): Promise<boolean> {
  const statement = entry.statement.trim()
  if (!gateway.connected || !statement) return Promise.resolve(false)
  return gateway.request('memory.create', {
    kind: entry.kind,
    statement,
    rationale: entry.rationale.trim(),
    scope: entry.scope,
    confidence: entry.confidence,
    applies_to: entry.tools.length ? { tools: entry.tools } : {}
  }).then(() => { void refreshPanes(); return true })
    .catch(() => false)
}

export function deleteMemory(id: string): Promise<boolean> {
  if (!gateway.connected || !id) return Promise.resolve(false)
  return gateway.request('memory.delete', { id })
    .then(() => { void refreshPanes(); return true })
    .catch(() => false)
}

export function updateMemory(id: string, patch: { statement?: string; confidence?: number; status?: string }): Promise<boolean> {
  if (!gateway.connected || !id) return Promise.resolve(false)
  return gateway.request('memory.update', { id, ...patch })
    .then(() => { void refreshPanes(); return true })
    .catch(() => false)
}

/* ---------------- notes ---------------- */

/** The scratchpad is session-only by design (the gateway stores one list), and
    the user note is the standing instruction prepended to every turn. Both are
    read together because the notes pane shows the two blocks side by side. */
export async function refreshNotes(): Promise<void> {
  if (!gateway.connected) return
  try {
    const res = await gateway.request<{ notes?: unknown }>('quick-notes.read', {})
    const list = Array.isArray(res?.notes) ? res.notes : []
    notes.quick = list.map(n => {
      const row = (n ?? {}) as Record<string, unknown>
      return { time: String(row.time ?? ''), text: String(row.text ?? '') }
    })
  } catch { /* keep the prototype scratchpad */ }

  try {
    const res = await gateway.request<{ content?: string }>('usernote.read', {})
    if (typeof res?.content === 'string') {
      notes.usernote = res.content
      notes.usernoteLoaded = true
    }
  } catch { /* keep the prototype note */ }
}

function pushQuickNotes(): void {
  void gateway.request('quick-notes.write', {
    notes: notes.quick.map(n => ({ time: n.time, text: n.text }))
  }).catch(() => { void refreshNotes() })
}

export function addQuickNote(text: string): void {
  const body = text.trim()
  if (!body) return
  if (!gateway.connected) return
  notes.quick = [...notes.quick, { time: nowClock(), text: body }]
  pushQuickNotes()
}

export function removeQuickNote(index: number): void {
  if (!gateway.connected) return
  notes.quick = notes.quick.filter((_, i) => i !== index)
  pushQuickNotes()
}

/** Appends a quick note to the composer without touching what is already typed.

    `#composerTa` is a plain DOM node driven by runtime.js — there is no v-model
    behind it — so the value is written directly and an `input` event is
    dispatched afterwards to let the autosize pass and the token meter follow.
    Without that event the box keeps its old height and the counter stays stale.
    The existing text is never rewritten; the separator is added only when the
    field does not already end in whitespace, and the caret lands at the end so
    the reader keeps typing where the note stopped. */
export function appendToComposer(text: string): void {
  const body = text.trim()
  if (!body) return
  const ta = document.getElementById('composerTa') as HTMLTextAreaElement | null
  if (!ta) return
  const current = ta.value
  const sep = !current || /\s$/.test(current) ? '' : ' '
  ta.value = current + sep + body
  ta.dispatchEvent(new Event('input', { bubbles: true }))
  ta.focus()
  ta.setSelectionRange(ta.value.length, ta.value.length)
}

export function saveUsernote(text: string): void {
  notes.usernote = text
  if (!gateway.connected) return
  void gateway.request('usernote.write', { content: text }).catch(() => { void refreshNotes() })
}

/* ---------------- mcp ---------------- */

/** Pulls the MCP server rack. A successful empty answer replaces the prototype
    rack (an empty project must not show someone else's servers); only a failed
    call leaves it untouched. */
export async function refreshMcp(): Promise<void> {
  if (!gateway.connected) return
  try {
    const mc = await gateway.request<Record<string, unknown>>('mcp.list', {})
    const list = Array.isArray(mc.servers) ? (mc.servers as Array<Record<string, unknown>>) : []
    mcp.servers = list.map(s => {
      const names = Array.isArray(s.tool_names) ? (s.tool_names as string[]) : []
      return {
        name: String(s.name ?? 'server'),
        transport: String(s.transport ?? 'stdio'),
        logo: String(s.name ?? 'S').slice(0, 1).toUpperCase(),
        cmd: String(s.command ?? ''),
        tools: Number(s.tools ?? names.length),
        on: Boolean(s.enabled),
        open: false,
        state: s.error ? 'err' : 'ok',
        chips: names.slice(0, 4),
        more: Math.max(0, names.length - 4),
        toolNames: names,
        token: ''
      }
    })
    mcp.running = list.filter(s => s.connected).length
    mcp.idle = list.filter(s => s.enabled && !s.connected).length
    mcp.tools = list.reduce((n, s) => n + Number(s.tools ?? 0), 0)
  } catch { /* keep prototype numbers */ }
}

/** Flips one server. The switch moves at once and rolls back when the gateway
    refuses, so the rack never claims a state the backend does not hold. */
export function toggleMcpServer(name: string, enabled: boolean): void {
  if (!gateway.connected || !name) return
  const srv = mcp.servers.find(s => s.name === name)
  const prev = srv ? srv.on : !enabled
  if (srv) srv.on = enabled
  void gateway.request('mcp.toggle', { server: name, enabled })
    .then(() => { void refreshMcp() })
    .catch(() => { if (srv) srv.on = prev })
}

export function installMcpServer(name: string): void {
  if (!gateway.connected || !name) return
  void gateway.request('mcp.install_server', { server: name })
    .then(() => { void refreshMcp() })
    .catch(() => { /* the rack keeps its current state */ })
}

/** Approves the project-level .mcp.json, which also hot-loads its servers. */
export function trustMcpProject(): void {
  if (!gateway.connected) return
  const path = String(mcp.pending.path ?? '')
  if (!path) return
  void gateway.request('mcp.trust_project', { path })
    .then(() => { void refreshMcp() })
    .catch(() => { /* keep the pending banner */ })
}

export function reloadMcp(): void {
  if (!gateway.connected) return
  void gateway.request('mcp.reload', {})
    .then(() => { void refreshMcp() })
    .catch(() => { /* keep the rack */ })
}

/* ---------------- agents and models ---------------- */

function numOf(v: unknown, fallback: number): number {
  return typeof v === 'number' && Number.isFinite(v) ? v : fallback
}

function textOf(v: unknown): string {
  return typeof v === 'string' ? v : ''
}

/** Reads the agent list and the model stack behind the LLM pane.

    The two calls are independent: a failing `agent.list` leaves the provider
    cards usable, and a failing `model.options` leaves the agent list intact. */
export async function refreshLlm(): Promise<void> {
  if (!gateway.connected) return

  try {
    const res = await gateway.request<{ agents?: Array<Record<string, unknown>> }>('agent.list', {})
    const list = Array.isArray(res?.agents) ? res.agents : []
    if (list.length) {
      llm.agents = list.map((a, i) => ({
        name: String(a.name ?? 'agent'),
        desc: String(a.description ?? ''),
        initial: String(a.name ?? 'A').slice(0, 1).toUpperCase(),
        tint: ['var(--accent)', 'var(--teal)', 'var(--gold)'][i % 3],
        current: Boolean(a.current)
      }))
    }
  } catch { /* keep the prototype agents */ }

  try {
    const opts = await gateway.request<Record<string, unknown>>('model.options', {})
    const provs = Array.isArray(opts.providers) ? (opts.providers as Array<Record<string, unknown>>) : []
    if (provs.length) {
      llm.providers = provs.map(p => ({
        name: String(p.name ?? 'provider'),
        slug: String(p.slug ?? ''),
        models: Array.isArray(p.models) ? (p.models as unknown[]).map(String) : [],
        authenticated: Boolean(p.authenticated),
        current: Boolean(p.is_current),
        apiKey: String(p.apiKey ?? ''),
        baseUrl: String(p.baseUrl ?? '')
      }))
      const idx = provs.findIndex(p => Boolean(p.is_current))
      const active = (idx >= 0 ? provs[idx] : provs[0]) ?? {}
      llm.provider = String(active.name ?? '')
      llm.temperature = numOf(active.temperature, llm.temperature)
      llm.topP = numOf(active.topP, llm.topP)
      llm.maxTokens = numOf(active.max_tokens, llm.maxTokens)
      llm.reasoningEffort = textOf(active.reasoning_effort) || llm.reasoningEffort
      llm.thinkingEnabled = active.thinking_enabled !== false
      llm.reasoningMode = textOf(active.reasoningMode) || llm.reasoningMode
      llm.maxEffort = textOf(active.maxEffort) || llm.maxEffort
      llm.sessionHeader = textOf(active.sessionHeader)
      const headers = active.extraHeaders
      if (headers && typeof headers === 'object' && Object.keys(headers as object).length) {
        llm.extraHeaders = JSON.stringify(headers, null, 2)
      }
    }
    if (typeof opts.model === 'string' && opts.model) llm.model = opts.model
    if (typeof opts.currency === 'string' && opts.currency) llm.currency = opts.currency
    if (opts.pricing && typeof opts.pricing === 'object') llm.pricing = opts.pricing as typeof llm.pricing
    llm.loaded = true
  } catch { /* keep the prototype numbers */ }
}

/** Switches the agent. The list updates first; a refusal re-reads the truth
    instead of leaving the wrong row highlighted. */
export function switchAgent(name: string): void {
  if (!gateway.connected || !name) return
  llm.agents.forEach(a => { a.current = a.name === name })
  void gateway.request<{ ok?: boolean }>('agent.switch', { name })
    .then(res => { if (res?.ok === false) void refreshLlm() })
    .catch(() => { void refreshLlm() })
}

/** Switches the model on the active provider. `model.switch` only accepts a
    model the active provider lists, so the index is sent for the caller's
    benefit and the truth is re-read on failure. */
export function switchModel(model: string): void {
  if (!gateway.connected || !model) return
  const idx = llm.providers.findIndex(p => p.models.includes(model))
  llm.model = model
  if (model) streamState.model = model
  void gateway.request('model.switch', { model, provider_index: idx })
    .catch(() => { void refreshLlm() })
}

/** Writes the sampling block of the active provider. `model.params.set` owns
    these four fields; everything else is saved through provider.save. */
export function setModelParams(): void {
  if (!gateway.connected) return
  void gateway.request('model.params.set', {
    temperature: llm.temperature,
    topP: llm.topP,
    reasoning_effort: llm.reasoningEffort,
    thinking_enabled: llm.thinkingEnabled
  }).catch(() => { /* the pane keeps the typed values */ })
}

/** Saves the whole provider card, including the request-shaping fields the
    settings UI edits by hand. Every exit reports: a save that silently does
    nothing is indistinguishable from a broken button. */
export function saveProvider(index: number): void {
  if (!gateway.connected) {
    notify('Save skipped', 'Not connected to the gateway, so nothing was written.', 'err')
    return
  }
  const p = llm.providers[index]
  if (!p) {
    notify('Save skipped', 'No provider card is selected.', 'err')
    return
  }
  let headers: Record<string, unknown> = {}
  try {
    const parsed = JSON.parse(llm.extraHeaders || '{}')
    if (parsed && typeof parsed === 'object') headers = parsed as Record<string, unknown>
  } catch { /* malformed json is sent empty, same as the hand-edited file */ }
  void gateway.request('model.provider.save', {
    index,
    provider: {
      slug: p.slug,
      name: p.name,
      apiKey: p.apiKey,
      baseUrl: p.baseUrl,
      models: p.models,
      temperature: llm.temperature,
      topP: llm.topP,
      maxTokens: llm.maxTokens,
      reasoning_effort: llm.reasoningEffort,
      thinking_enabled: llm.thinkingEnabled,
      extraHeaders: headers,
      sessionHeader: llm.sessionHeader,
      reasoningMode: llm.reasoningMode,
      maxEffort: llm.maxEffort
    }
  }).then(() => {
    notify('Saved', p.name + ' written to the provider config.', 'ok')
    void refreshLlm()
  }).catch(e => {
    notify('Save failed', e instanceof Error ? e.message : 'The gateway refused the write.', 'err')
  })
}

export function deleteProvider(index: number): void {
  if (!gateway.connected) {
    notify('Delete skipped', 'Not connected to the gateway, so nothing was removed.', 'err')
    return
  }
  const name = llm.providers[index]?.name
  if (!name) {
    notify('Delete skipped', 'No provider card is selected.', 'err')
    return
  }
  void gateway.request('model.provider.delete', { index })
    .then(() => {
      notify('Deleted', name + ' removed from the provider config.', 'warn')
      void refreshLlm()
    })
    .catch(e => {
      notify('Delete failed', e instanceof Error ? e.message : 'The gateway refused the delete.', 'err')
    })
}

/* ---------------- message actions ---------------- */

type ToastFn = (title: string, message: string, kind?: string) => void

function notify(title: string, message: string, kind: 'ok' | 'warn' | 'err' = 'ok'): void {
  const fn = (window as unknown as { __pebbleToast?: ToastFn }).__pebbleToast
  if (fn) fn(title, message, kind)
}

/** Copies the row's plain text. The clipboard API can refuse inside the Tauri
    webview, so a scratch textarea is the fallback rather than a silent no-op. */
export function copyMessage(text: string): boolean {
  const body = text.trim()
  if (!body) return false
  try {
    void navigator.clipboard.writeText(body)
    notify('Copied', body.slice(0, 60))
    return true
  } catch {
    try {
      const ta = document.createElement('textarea')
      ta.value = body
      ta.style.position = 'fixed'
      ta.style.opacity = '0'
      document.body.appendChild(ta)
      ta.select()
      document.execCommand('copy')
      ta.remove()
      notify('Copied', body.slice(0, 60))
      return true
    } catch {
      notify('Copy failed', 'The clipboard is not available here.', 'err')
      return false
    }
  }
}

/** Removes a row from the transcript. Only rows that came from the store carry a
    numeric message id; a live or pending row is dropped locally and the next
    resume restores the truth. */
/** Removes a row from the transcript, and from the database when the row has a
    stored id. A live or pending row only exists in memory, so it is dropped
    locally and the caller is told it will return — silently pretending the
    delete stuck is worse than saying so. */
export function deleteMessage(itemId: string): void {
  const items = streamState.items
  const at = items.findIndex(i => i.id === itemId)
  if (at > -1) items.splice(at, 1)
  /* A prompt still waiting for its saved id must not be re-adopted when
     user_message.saved arrives, or the row the reader just deleted comes back. */
  if (itemId.indexOf('pending-') === 0) pendingUser = null
  const sid = streamState.sessionId || sessionState.activeId
  if (!/^\d+$/.test(itemId)) {
    notify('Removed locally', 'This row has no stored id yet; it returns on the next reload.', 'warn')
    return
  }
  if (!sid || !gateway.connected) {
    notify('Delete skipped', 'Not connected to the gateway, so nothing was removed.', 'err')
    return
  }
  void gateway.request<{ deleted?: boolean }>('message.delete', { session_id: sid, message_id: Number(itemId) })
    .then(result => {
      if (result?.deleted === false) {
        notify('Delete failed', 'The gateway could not find that message.', 'err')
        void loadSession(sid)
        return
      }
      // The header counts exchanges; a deleted row must stop being counted.
      sessionState.messageCount = Math.max(0, sessionState.messageCount - 1)
      notify('Deleted', 'Message removed from the transcript.', 'warn')
    })
    .catch(() => {
      notify('Delete failed', 'The gateway refused to remove this message.', 'err')
      void loadSession(sid)
    })
}

/** Pulls the file changes for the session on screen.

    A successful answer replaces the prototype rows even when it is empty: a
    session with no edits must not keep showing another project's files. Only a
    failed call leaves the pane alone. */
export async function refreshFileChanges(): Promise<void> {
  if (!gateway.connected) return
  const sid = streamState.sessionId || sessionState.activeId
  try {
    const fc = await gateway.request<Record<string, unknown>>('session.file_changes', { session_id: sid })
    const list = Array.isArray(fc.files) ? (fc.files as Array<Record<string, unknown>>) : []
    files.changes = list.map(f => {
      const rel = String(f.relative_path ?? '')
      const cut = rel.lastIndexOf('/')
      const status = String(f.status ?? 'modify')
      return {
        path: rel,
        dir: cut > -1 ? rel.slice(0, cut + 1) : '',
        base: cut > -1 ? rel.slice(cut + 1) : rel,
        status: status === 'create' || status === 'add' ? 'add' : status === 'delete' || status === 'del' ? 'del' : 'mod',
        added: Number(f.added_lines ?? 0),
        removed: Number(f.removed_lines ?? 0),
        versions: Number(f.active_count ?? 1),
        open: false,
        hunks: []
      }
    })
    files.totalFiles = Number(fc.total_files ?? list.length)
    files.totalAdded = Number(fc.total_additions ?? 0)
    files.totalRemoved = Number(fc.total_deletions ?? 0)
    files.totalVersions = files.changes.reduce((n, f) => n + f.versions, 0)
  } catch {
    /* keep whatever the pane already shows */
  }
}

/** Accepts every pending change: the gateway drops the snapshots and the dock
    clears, in that order, so a refusal leaves the rows visible. */
export async function acceptFileChanges(): Promise<boolean> {
  if (!gateway.connected) return false
  const sid = streamState.sessionId || sessionState.activeId
  try {
    await gateway.request('session.file_accept', { session_id: sid })
    files.changes = []
    files.totalFiles = 0
    files.totalAdded = 0
    files.totalRemoved = 0
    files.totalVersions = 0
    return true
  } catch {
    return false
  }
}

/** Reverts one path one version at a time; `remaining` is what the gateway still
    holds for it, and the row disappears once it reaches zero. */
export async function revertFileChange(relativePath: string): Promise<{ ok: boolean; remaining: number }> {
  if (!gateway.connected || !relativePath) return { ok: false, remaining: 0 }
  const sid = streamState.sessionId || sessionState.activeId
  try {
    const res = await gateway.request<{ remaining?: number }>('session.file_revert', {
      session_id: sid,
      relative_path: relativePath
    })
    const remaining = Number(res?.remaining ?? 0)
    if (remaining > 0) {
      const row = files.changes.find(f => f.path === relativePath)
      if (row) row.versions = remaining
    } else {
      files.changes = files.changes.filter(f => f.path !== relativePath)
    }
    files.totalFiles = files.changes.length
    files.totalVersions = files.changes.reduce((n, f) => n + f.versions, 0)
    return { ok: true, remaining }
  } catch {
    return { ok: false, remaining: 0 }
  }
}

/** Reverts every change made up to a message. */
export async function revertToMessage(messageId: number): Promise<boolean> {
  if (!gateway.connected || !messageId) return false
  const sid = streamState.sessionId || sessionState.activeId
  try {
    await gateway.request('session.file_revert_all', { session_id: sid, message_id: messageId })
    await refreshFileChanges()
    return true
  } catch {
    return false
  }
}

/** Pulls the dock panes. Each call is independent: a failing RPC leaves the
    pane on its prototype numbers instead of blanking it. */
export async function refreshPanes(): Promise<void> {
  if (!gateway.connected) return
  /* Only a real session uuid can answer `usage.stats range=session`. The local
     list id (`s1`) resolves to nothing, and that empty payload would overwrite the
     live totals with zeros on every dock refresh. */
  const sid = streamState.sessionId || (sessionState.activeId.length >= 20 ? sessionState.activeId : '')

  try {
    if (!sid) throw new Error('no session attached')
    const st = await gateway.request<Record<string, unknown>>('usage.stats', { range: 'session', session_id: sid })
    if (typeof st.total_cost === 'number') usage.costToday = st.total_cost
    if (typeof st.total_input_tokens === 'number') usage.tokensIn = st.total_input_tokens
    if (typeof st.total_output_tokens === 'number') usage.tokensOut = st.total_output_tokens
    if (typeof st.total_cache_hit_tokens === 'number') usage.cacheHit = st.total_cache_hit_tokens
    if (typeof st.api_calls === 'number') usage.calls = st.api_calls
    if (typeof st.currency === 'string') usage.currency = st.currency.toLowerCase()
    /* The gateway reports the model's window alongside the totals, which is what
       lets a freshly loaded page size its ring without waiting for a prompt. */
    const limit = Number(st.model_context_limit ?? 0)
    if (Number.isFinite(limit) && limit > 0) {
      usage.contextLimit = limit
      try { window.localStorage.setItem('pebble.ctxLimit', String(limit)) } catch { /* private mode */ }
    }
    if (Array.isArray(st.calls) && st.calls.length) {
      /* The gateway names these `model_name` and `total_cost`; the first pass read
         `model` and `cost`, which silently produced rows labelled "model" at zero. */
      usage.history = (st.calls as Array<Record<string, unknown>>)
        .slice(-8)
        .reverse()
        .map(c => ({
          model: String(c.model_name ?? 'model'),
          tokensIn: Number(c.input_tokens ?? 0) + Number(c.cache_hit_tokens ?? 0),
          tokensOut: Number(c.output_tokens ?? 0),
          cost: Number(c.total_cost ?? 0),
          tint: ''
        }))
      /* Window occupancy has to be restored here too: `message.usage` only arrives
         while a turn is running, so a reload left the ring on the prototype's 31%
         until the next prompt. The newest call's prompt tokens are exactly what
         the window held at that moment. */
      const calls = st.calls as Array<Record<string, unknown>>
      const last = calls[calls.length - 1]
      const occupied = Number(last?.input_tokens ?? 0) + Number(last?.cache_hit_tokens ?? 0)
      if (occupied > 0) {
        usage.contextTokens = occupied
        /* The window size only rides on `message.usage`, so it is cached across
           reloads; without that the ring has no denominator until the next prompt
           and falls back to the prototype's 31%. */
        let cached = 0
        try { cached = Number(window.localStorage.getItem('pebble.ctxLimit') || 0) } catch { /* private mode */ }
        const win = usage.contextLimit || cached
        if (win > 0) {
          usage.contextLimit = win
          usage.contextPct = Math.max(0, Math.min(100, Math.round((occupied / win) * 100)))
        }
      }
    }
    if (Array.isArray(st.models)) {
      usage.models = (st.models as Array<Record<string, unknown>>).map(m => ({
        model: String(m.model_name ?? 'model'),
        calls: Number(m.api_calls ?? 0),
        cost: Number(m.total_cost ?? 0),
        tokensIn: Number(m.total_input_tokens ?? 0),
        tokensOut: Number(m.total_output_tokens ?? 0)
      }))
    }
  } catch { /* keep prototype numbers */ }

  await refreshFileChanges()

  await refreshMcp()

  await refreshNotes()

  await refreshPaths()

  await refreshPairing()

  try {
    const mem = await gateway.request<Record<string, unknown>>('memory.list', { limit: 50 })
    const list = Array.isArray(mem.memories) ? (mem.memories as Array<Record<string, unknown>>) : []
    if (list.length) {
      ledger.entries = list.map(m => {
        const applies = m.applies_to
        const tools = applies && typeof applies === 'object' && Array.isArray((applies as Record<string, unknown>).tools)
          ? ((applies as Record<string, unknown>).tools as unknown[]).map(String)
          : []
        const conf = Number(m.confidence ?? 0)
        return {
          id: String(m.id ?? m.memory_id ?? ''),
          text: String(m.text ?? m.statement ?? m.content ?? ''),
          scope: String(m.scope ?? 'project'),
          kind: String(m.kind ?? m.memory_type ?? 'reference'),
          confidence: Math.round(conf * (conf <= 1 ? 100 : 1)),
          age: String(m.age ?? ''),
          rationale: String(m.rationale ?? ''),
          appliesTo: tools,
          status: String(m.status ?? 'active')
        }
      })
      ledger.total = Number(mem.count ?? list.length)
      context.ledgerTotal = ledger.total
    }
  } catch { /* keep prototype numbers */ }
}

/** Re-reads the session list. A gateway that answers with nothing is a real
    state, but a failed call is not: this returns the rows and lets the caller
    decide, so an offline blip cannot empty the switcher. */
async function fetchSessions(): Promise<Array<Record<string, unknown>>> {
  if (!gateway.connected) return []
  try {
    /* Scoped to the active project: a session belongs to the directory it was
       created under, and an unscoped list is what left another project's
       conversations on screen after a switch. */
    const res = await gateway.request<{ sessions?: Array<Record<string, unknown>> }>('session.list', {
      limit: 200,
      project_path: projects.active || undefined
    })
    return Array.isArray(res?.sessions) ? res.sessions : []
  } catch {
    return []
  }
}

async function refreshSessionList(): Promise<void> {
  const list = await fetchSessions()
  if (!list.length) return
  const total = list.length
  sessions.splice(0, sessions.length, ...list.map((raw, i) => toSession(raw, i, total)))
}

/** Starts a session and opens it. The list is refreshed first so the new row
    exists in the switcher before the transcript swaps underneath it. */
export async function createSession(title = 'New conversation'): Promise<boolean> {
  if (!gateway.connected) return false
  try {
    const created = await gateway.request<{ session_id?: string; id?: string }>('session.create', {
      title,
      /* Without this the row is stored with an empty project_path, lands in the
         unassigned bucket, and the project that started it never sees it. */
      project_path: sessionState.projectPath || ''
    })
    const sid = String(created?.session_id ?? created?.id ?? '')
    if (!sid) return false
    await refreshSessionList()
    /* The switcher has to show the row at once. `session.list` can race the
       create, and the cross-client `session.created` event may not have landed
       yet, so an empty refresh used to leave the reader looking at the old list
       until the next reload. */
    if (!sessions.some(s => s.id === sid)) {
      sessions.unshift({
        id: sid,
        index: null,
        title,
        msgs: 0,
        time: 'now',
        group: 'Today',
        pinned: false
      })
    }
    sessionState.activeId = sid
    streamState.sessionId = sid
    clearStream()
    await loadSession(sid)
    return true
  } catch {
    return false
  }
}

export interface CompactOutcome {
  ok: boolean
  title: string
  message: string
}

/** Compacts the open session.

    The gateway rejects a session with fewer than two rows as well, but it
    answers with a generic failure; checking here produces the specific copy.
    A successful compaction rewrites the transcript, so the session is reloaded
    and the fold caches are dropped with it. */
export async function compressSession(): Promise<CompactOutcome> {
  if (!gateway.connected) {
    return { ok: false, title: 'Not connected', message: 'Start the gateway to compact this session.' }
  }
  const sid = streamState.sessionId || sessionState.activeId
  if (!sid) return { ok: false, title: 'Cannot compact', message: 'No session is open.' }
  const total = lastHistory.length
  if (total < 2) {
    return { ok: false, title: 'Cannot compact', message: `Only ${total} messages in this session; at least 2 are required to compact.` }
  }
  try {
    const r = await gateway.request<Record<string, unknown>>('session.compress', { session_id: sid })
    if (!r?.success) {
      return { ok: false, title: 'Cannot compact', message: String(r?.reason ?? 'Compaction failed') }
    }
    await loadSession(sid)
    const before = Number((r.before as Record<string, unknown> | undefined)?.messages ?? 0)
    const after = Number((r.after as Record<string, unknown> | undefined)?.messages ?? 0)
    /* The window figure comes from the last call's `latest_input_tokens`, which
       still describes the transcript as it was before the fold. The compaction
       result carries the token count it measured after rewriting, so the ring
       drops to the real occupancy now instead of waiting for the next prompt. */
    const afterTokens = Number((r.after as Record<string, unknown> | undefined)?.tokens ?? NaN)
    if (Number.isFinite(afterTokens) && afterTokens >= 0) {
      usage.contextTokens = afterTokens
      // What survived the fold is the whole window; the previous call's output
      // is not part of it any more.
      usage.contextOut = 0
      if (usage.contextLimit > 0) {
        usage.contextPct = Math.max(0, Math.min(100, Math.round((afterTokens / usage.contextLimit) * 100)))
      }
    }
    // The persisted transcript keeps the boundary marker and the summary on top
    // of the rows it kept, so "kept" is the row count minus those two.
    const kept = after > 2 ? after - 2 : 0
    const cut = before > kept ? before - kept : 0
    return {
      ok: true,
      title: 'Compaction complete',
      message: `Compacted ${cut} messages · kept ${kept} · saved ${String(r.saved_percent ?? 0)}%`
    }
  } catch {
    return { ok: false, title: 'Cannot compact', message: 'The gateway refused the request.' }
  }
}

/** Writes the transcript to a markdown file and returns its path. */
export async function exportSession(sessionId?: string): Promise<string | null> {
  const sid = sessionId || streamState.sessionId || sessionState.activeId
  if (!gateway.connected || !sid) return null
  try {
    const r = await gateway.request<{ filepath?: string }>('session.export_save', { session_id: sid })
    return String(r?.filepath ?? '') || null
  } catch {
    return null
  }
}

/** First contact: swap the seeded session list for the real one, then open the
    most recent session. With no stored sessions a fresh one is created and the
    seeded transcript is cleared, because those rows do not belong to it. */
/** The prototype content is a demo: a plan slip with five steps, a permission
    queue with three requests, a transcript that belongs to nobody. Once a real
    session answers, those rows are not just stale, they are wrong. */
export async function bootstrap(): Promise<void> {
  if (!gateway.connected) return
  /* Sessions belong to the project they were created under, so the registry is
     resolved before the list is asked for. An unscoped list is what left the
     previous project's conversations on screen after a switch. */
  if (!projects.loaded) await refreshProjects()
  sessionState.projectPath = projects.active

  let list: Array<Record<string, unknown>> = []
  try {
    const res = await gateway.request<{ sessions?: Array<Record<string, unknown>> }>('session.list', {
      limit: 200,
      project_path: projects.active || undefined
    })
    list = Array.isArray(res?.sessions) ? res.sessions : []
  } catch {
    /* keep the seeded list when session.list is unavailable */
  }

  if (list.length) {
    const total = list.length
    sessions.splice(0, sessions.length, ...list.map((raw, i) => toSession(raw, i, total)))
    // most_recent is the session the user last typed into; the list order is the
    // fallback because the RPC is allowed to answer with nothing.
    let recent = ''
    try {
      const res = await gateway.request<Record<string, unknown>>('session.most_recent', {})
      recent = String(res?.id ?? res?.session_id ?? '')
    } catch {
      /* fall through to the newest row */
    }
    const open = recent && sessions.some(s => s.id === recent) ? recent : String(list[0].id ?? list[0].session_id ?? '')
    if (open) await loadSession(open)
    return
  }

  /* No session stored for this project yet. Going through createSession keeps
     the project_path attached and puts the row in the switcher immediately,
     instead of leaving an unassigned session the list never shows. */
  await createSession()
}

function toSession(raw: Record<string, unknown>, i: number, total: number): Session {
  /* `last_message_at` is the newest message in the session, which is what the
     group is supposed to describe; `updated_at` also moves on renames, so it is
     only the fallback. */
  /* A session with no turns yet answers `last_message_at: null`, and the list
     rows carry their start time as `started_at` (epoch seconds) rather than
     `created_at`. Missing both used to fall through to 0, and 0 buckets to
     "Earlier" — a session created seconds ago sank to the bottom of the
     switcher. Zero therefore counts as absent here, not as a timestamp. */
  const epoch = (v: unknown): number => {
    const n = Number(v)
    return Number.isFinite(n) && n > 0 ? n : 0
  }
  const rawTs = epoch(raw.last_message_at) || epoch(raw.started_at) ||
    epoch(raw.updated_at) || epoch(raw.created_at)
  const ts = rawTs > 1e12 ? rawTs : rawTs * 1000
  const d = ts ? new Date(ts) : null
  const now = new Date()
  const sameDay = d ? d.toDateString() === now.toDateString() : false
  const yest = new Date(now)
  yest.setDate(now.getDate() - 1)
  const isYesterday = d ? d.toDateString() === yest.toDateString() : false
  const group: SessionGroup = bucketFor(ts)
  const clock = d ? `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}` : ''
  return {
    id: String(raw.id ?? raw.session_id ?? ''),
    index: String(total - i).padStart(2, '0'),
    title: String(raw.title ?? 'Untitled session'),
    msgs: Number(raw.message_count ?? raw.msgs ?? 0),
    time: sameDay ? clock || 'now' : isYesterday ? '1d ago' : d ? `${Math.max(1, Math.round((now.getTime() - ts) / 86400000))}d ago` : '',
    group,
    pinned: Boolean(raw.pinned),
    ts
  }
}

/* ---------------- pairing ---------------- */

export async function refreshPairing(): Promise<void> {
  if (!gateway.connected) return
  try {
    const st = await gateway.request<Record<string, unknown>>('pairing.status', {})
    pairing.supported = st.supported !== false
    pairing.enabled = Boolean(st.enabled)
    pairing.port = Number(st.port ?? 0)
    pairing.host = String(st.host ?? '')
    pairing.fingerprint = String(st.fingerprint ?? '')
    pairing.certExpiresAt = String(st.cert_expires_at ?? '')
    pairing.error = String(st.error ?? st.runtime_error ?? '')
    const ps = (st.pairing ?? {}) as Record<string, unknown>
    pairing.code = String(ps.code ?? '')
  } catch { /* keep prototype */ }
}

/** Mints a fresh code. Minting invalidates the previous one, so this runs on
    open and on every "New code" press, never as a background refresh. */
export async function mintPairingCode(): Promise<boolean> {
  if (!gateway.connected) return false
  try {
    const res = await gateway.request<Record<string, unknown>>('pairing.session', {})
    pairing.code = String(res.code ?? '')
    pairing.uri = String(res.uri ?? '')
    pairing.fingerprint = String(res.fingerprint ?? pairing.fingerprint)
    pairing.host = String(res.host ?? pairing.host)
    notify('Pairing code ready', pairing.code ? 'Scan it on the phone.' : 'Code issued.', 'ok')
    return true
  } catch (e) {
    notify('Pairing unavailable', e instanceof Error ? e.message : 'The LAN listener refused.', 'err')
    return false
  }
}

export function setPairingEnabled(enabled: boolean): void {
  if (!gateway.connected) return
  pairing.enabled = enabled
  void gateway.request(enabled ? 'pairing.enable' : 'pairing.disable', {})
    .then(() => { void refreshPairing() })
    .catch(() => { void refreshPairing() })
}

export async function rotatePairingCertificate(): Promise<boolean> {
  if (!gateway.connected) return false
  try {
    await gateway.request('pairing.rotate_certificate', {})
    await refreshPairing()
    return true
  } catch { return false }
}

export async function revokePairedDevice(): Promise<boolean> {
  if (!gateway.connected) return false
  try {
    await gateway.request('pairing.revoke', {})
    await refreshPairing()
    return true
  } catch { return false }
}

/* ---------------- files ---------------- */

export async function searchFiles(q: string, path = '', limit = 100): Promise<Array<Record<string, unknown>>> {
  if (!gateway.connected) return []
  try {
    // An empty path must be omitted, not sent: the gateway falls back to the
    // project root only when the key is missing, and an empty string fails its
    // path guard, which reads as "this directory has no entries".
    const params: Record<string, unknown> = { q, limit }
    if (path) params.path = path
    const res = await gateway.request<{ results?: unknown }>('file.search', params)
    return Array.isArray(res?.results) ? (res.results as Array<Record<string, unknown>>) : []
  } catch { return [] }
}

export function openFile(path: string): void {
  if (!gateway.connected || !path) return
  void gateway.request('file.open', { path }).catch(() => {
    notify('Cannot open', path, 'err')
  })
}

export async function readFileText(path: string): Promise<string> {
  if (!gateway.connected || !path) return ''
  try {
    const res = await gateway.request<{ content?: string }>('file.read_text', { path })
    return String(res?.content ?? '')
  } catch { return '' }
}

export async function readFileBase64(path: string): Promise<string> {
  if (!gateway.connected || !path) return ''
  try {
    const res = await gateway.request<{ data?: string; data_base64?: string }>('file.read_base64', { path })
    return String(res?.data ?? res?.data_base64 ?? '')
  } catch { return '' }
}

export function writeFileBase64(path: string, dataBase64: string): Promise<boolean> {
  if (!gateway.connected || !path) return Promise.resolve(false)
  return gateway.request('file.write_base64', { path, data_base64: dataBase64 })
    .then(() => true)
    .catch(() => false)
}

/* ---------------- mcp secrets ---------------- */

export function setMcpSecret(server: string, envKey: string, value: string): Promise<boolean> {
  if (!gateway.connected) return Promise.resolve(false)
  return gateway.request('mcp.set_secret', { server, env_key: envKey, value })
    .then(() => { void refreshMcp(); return true })
    .catch(() => false)
}

export async function getMcpInstallInfo(): Promise<Array<Record<string, unknown>>> {
  if (!gateway.connected) return []
  try {
    const res = await gateway.request<{ servers?: unknown }>('mcp.install_info', {})
    return Array.isArray(res?.servers) ? (res.servers as Array<Record<string, unknown>>) : []
  } catch { return [] }
}

export async function getMcpSecretStatus(server: string): Promise<Record<string, boolean>> {
  if (!gateway.connected || !server) return {}
  try {
    const res = await gateway.request<{ keys?: unknown }>('mcp.secret_status', { server })
    return (res?.keys && typeof res.keys === 'object' ? res.keys : {}) as Record<string, boolean>
  } catch { return {} }
}

/* ---------------- session extras ---------------- */

export async function fileDiff(relativePath: string): Promise<Array<Record<string, unknown>>> {
  if (!gateway.connected || !relativePath) return []
  const sid = streamState.sessionId || sessionState.activeId
  try {
    const res = await gateway.request<{ hunks?: unknown; diff?: unknown }>('session.file_diff', { session_id: sid, relative_path: relativePath })
    const raw = res?.hunks ?? res?.diff
    return Array.isArray(raw) ? (raw as Array<Record<string, unknown>>) : []
  } catch { return [] }
}

/** Asks the gateway for the transcript as markdown instead of rebuilding it
    locally, so the export matches what the model actually saw. */
export async function exportSessionMarkdown(): Promise<string> {
  if (!gateway.connected) return ''
  const sid = streamState.sessionId || sessionState.activeId
  if (!sid) return ''
  try {
    const res = await gateway.request<{ markdown?: string; content?: string }>('session.export', { session_id: sid })
    return String(res?.markdown ?? res?.content ?? '')
  } catch {
    notify('Export failed', 'The gateway could not build the markdown.', 'err')
    return ''
  }
}

export async function unassignedCount(): Promise<number> {
  if (!gateway.connected) return 0
  try {
    const res = await gateway.request<{ count?: number }>('session.unassigned_count', {})
    return Number(res?.count ?? 0)
  } catch { return 0 }
}

export async function adoptUnassigned(projectPath: string): Promise<number> {
  if (!gateway.connected || !projectPath) return 0
  try {
    const res = await gateway.request<{ adopted?: number }>('session.adopt_unassigned', { project_path: projectPath })
    await refreshSessionList()
    return Number(res?.adopted ?? 0)
  } catch { return 0 }
}

/* ---------------- projects ---------------- */

/** Loads the registry. `active` is authoritative: the gateway resolves it to a
    concrete directory, so the switcher shows the same path the tools will use. */
export async function refreshProjects(): Promise<void> {
  if (!gateway.connected) return
  try {
    const res = await gateway.request<{ projects?: string[]; active?: string }>('project.list', {})
    const list = Array.isArray(res?.projects) ? res.projects.map(String) : []
    projects.list.splice(0, projects.list.length, ...list)
    projects.active = String(res?.active ?? '')
    projects.loaded = true
    if (projects.active) sessionState.projectPath = projects.active
  } catch {
    /* keep the last known registry */
  }
}

/** Registers a directory and opens it. The gateway refuses drive roots and
    missing folders, so its message is surfaced instead of a generic failure. */
export async function addProject(path: string): Promise<{ ok: boolean; error?: string }> {
  const p = path.trim()
  if (!p) return { ok: false, error: 'path required' }
  if (!gateway.connected) return { ok: false, error: 'gateway offline' }
  try {
    await gateway.request('project.add', { path: p })
    await refreshProjects()
    const match = projects.list.find(x => x.toLowerCase() === p.toLowerCase())
      ?? projects.list[projects.list.length - 1]
    if (match) await switchProject(match)
    return { ok: true }
  } catch (e) {
    return { ok: false, error: e instanceof Error ? e.message : 'add failed' }
  }
}

/** Points the gateway at a directory and reloads the surface: every session row
    belongs to the project that was active when it was created. */
export async function switchProject(path: string): Promise<boolean> {
  if (!gateway.connected) return false
  try {
    await gateway.request('project.set_active', { path })
    projects.active = path
    sessionState.projectPath = path
    await refreshProjects()
    await bootstrap()
    return true
  } catch {
    return false
  }
}

/** Drops a directory from the registry. When it was the active one the gateway
    rewrites `active` itself, so the list is re-read rather than guessed. */
export async function removeProject(path: string): Promise<void> {
  if (!gateway.connected) return
  try {
    await gateway.request('project.delete', { path })
    await refreshProjects()
  } catch {
    /* keep the list */
  }
}

/* ---------------- command palette ---------------- */

export async function completeSlash(prefix: string): Promise<Array<Record<string, unknown>>> {
  if (!gateway.connected) return []
  try {
    const res = await gateway.request<{ items?: unknown; suggestions?: unknown }>('complete.slash', { prefix })
    const raw = res?.items ?? res?.suggestions
    return Array.isArray(raw) ? (raw as Array<Record<string, unknown>>) : []
  } catch { return [] }
}

export function execSlash(command: string): Promise<boolean> {
  if (!gateway.connected || !command.trim()) return Promise.resolve(false)
  return gateway.request('slash.exec', { command }).then(() => true).catch(() => false)
}

export function dispatchCommand(name: string, args: Record<string, unknown> = {}): Promise<boolean> {
  if (!gateway.connected || !name) return Promise.resolve(false)
  return gateway.request('command.dispatch', { name, ...args }).then(() => true).catch(() => false)
}

/* ---------------- config and pricing ---------------- */

export async function getConfig(key: string): Promise<Record<string, unknown> | null> {
  if (!gateway.connected || !key) return null
  try {
    const res = await gateway.request<Record<string, unknown>>('config.get', { key })
    return (res?.config ?? res) as Record<string, unknown>
  } catch { return null }
}

export function setConfig(key: string, value: unknown): Promise<boolean> {
  if (!gateway.connected || !key) return Promise.resolve(false)
  return gateway.request('config.set', { key, value }).then(() => true).catch(() => false)
}

/* The gateway stores the whole table with its per-period blocks, so the caller
   sends the entries back in the shape they arrived in rather than a flattened
   row list; flattening would drop `pricing_peak` and `pricing_tier_high`. */
export function savePricing(models: Record<string, unknown>, currency = 'usd'): Promise<boolean> {
  if (!gateway.connected) return Promise.resolve(false)
  return gateway.request('pricing.save', { models, default_currency: currency }).then(() => true).catch(() => false)
}

/* ---------------- permissions ---------------- */

export async function listPermissions(): Promise<Array<Record<string, unknown>>> {
  if (!gateway.connected) return []
  try {
    const res = await gateway.request<{ requests?: unknown; queue?: unknown }>('permission.list', {})
    const raw = res?.requests ?? res?.queue
    return Array.isArray(raw) ? (raw as Array<Record<string, unknown>>) : []
  } catch { return [] }
}

export async function verifyPermissionAudit(): Promise<{ ok: boolean; detail: string }> {
  if (!gateway.connected) return { ok: false, detail: 'offline' }
  try {
    const res = await gateway.request<{ ok?: boolean; valid?: boolean; error?: string }>('permission.audit.verify', {})
    return { ok: res?.ok ?? res?.valid ?? false, detail: String(res?.error ?? '') }
  } catch { return { ok: false, detail: 'verify failed' } }
}

export async function checkPermission(tool: string, content: string): Promise<{ allowed: boolean; message: string }> {
  if (!gateway.connected) return { allowed: false, message: 'offline' }
  try {
    const res = await gateway.request<{ allowed?: boolean; message?: string }>('permission.check', { tool, content })
    return { allowed: Boolean(res?.allowed), message: String(res?.message ?? '') }
  } catch { return { allowed: false, message: 'check failed' } }
}

/* ---------------- agent memory ---------------- */

export async function getAgentMemory(agentType = '', scope = 'project'): Promise<string> {
  if (!gateway.connected) return ''
  try {
    const res = await gateway.request<{ content?: string; memory?: string }>('agent-memory.get', {
      agent_type: agentType,
      scope
    })
    return String(res?.content ?? res?.memory ?? '')
  } catch { return '' }
}

export function setAgentMemory(content: string, agentType = '', scope = 'project'): Promise<boolean> {
  if (!gateway.connected) return Promise.resolve(false)
  return gateway
    .request('agent-memory.set', { content, agent_type: agentType, scope })
    .then(() => true)
    .catch(() => false)
}

export function clearAgentMemory(agentType = '', scope = 'project'): Promise<boolean> {
  if (!gateway.connected) return Promise.resolve(false)
  return gateway
    .request('agent-memory.clear', { agent_type: agentType, scope })
    .then(() => true)
    .catch(() => false)
}

/* ---------------- memory extras ---------------- */

export async function getMemory(id: string): Promise<Record<string, unknown> | null> {
  if (!gateway.connected || !id) return null
  try {
    const res = await gateway.request<{ memory?: unknown }>('memory.get', { id })
    return (res?.memory ?? null) as Record<string, unknown> | null
  } catch { return null }
}

export async function memoryStats(): Promise<Record<string, unknown>> {
  if (!gateway.connected) return {}
  try {
    return await gateway.request<Record<string, unknown>>('memory.stats', {})
  } catch { return {} }
}

export async function memoryPrompt(query: string, agentName = ''): Promise<string> {
  if (!gateway.connected) return ''
  try {
    const res = await gateway.request<{ prompt?: string; content?: string }>('memory.prompt', { query, agent_name: agentName || undefined })
    return String(res?.prompt ?? res?.content ?? '')
  } catch { return '' }
}

/* ---------------- setup and theme ---------------- */

export async function getSetupStatus(): Promise<Record<string, unknown>> {
  if (!gateway.connected) return {}
  try {
    return await gateway.request<Record<string, unknown>>('setup.status', {})
  } catch { return {} }
}

/** Orbit keeps its own light/dark + accent choice. The gateway theme object
    belongs to the other shell (named palettes), so the Orbit pick is stored
    inside `colors` and never overwrites `current`. */
export async function saveOrbitTheme(mode: string, accent: string): Promise<void> {
  if (!gateway.connected) return
  try {
    const cur = await gateway.request<{ theme?: Record<string, unknown> }>('theme.get', {})
    const theme = (cur?.theme ?? {}) as Record<string, unknown>
    const colors = (theme.colors && typeof theme.colors === 'object' ? theme.colors : {}) as Record<string, unknown>
    colors.orbitMode = mode
    colors.orbitAccent = accent
    await gateway.request('theme.save', {
      theme: { current: theme.current ?? 'amber', custom: theme.custom ?? [], colors }
    })
  } catch { /* theme stays local when the gateway is unreachable */ }
}

export async function readOrbitTheme(): Promise<{ mode: string; accent: string } | null> {
  if (!gateway.connected) return null
  try {
    const cur = await gateway.request<{ theme?: Record<string, unknown> }>('theme.get', {})
    const colors = ((cur?.theme ?? {}) as Record<string, unknown>).colors as Record<string, unknown> | undefined
    const mode = String(colors?.orbitMode ?? '')
    const accent = String(colors?.orbitAccent ?? '')
    if (!mode && !accent) return null
    return { mode, accent }
  } catch { return null }
}

/* ---------------- message search ---------------- */

/** Full-text search across the project's messages. The gateway searches stored
    transcripts, so hits can come from sessions other than the open one; the row
    keeps the session id so the caller can jump there. */
export async function searchMessages(q: string, limit = 30): Promise<void> {
  search.query = q
  const body = q.trim()
  if (!gateway.connected || !body) {
    search.hits = []
    search.total = 0
    return
  }
  try {
    const res = await gateway.request<{ results?: unknown; total?: number }>('session.search', { q: body, limit })
    const list = Array.isArray(res?.results) ? (res.results as Array<Record<string, unknown>>) : []
    search.hits = list.map(r => ({
      sessionId: String(r.session_id ?? r.sid ?? ''),
      messageId: Number(r.message_id ?? r.id ?? 0),
      role: String(r.role ?? ''),
      text: String(r.text ?? r.content ?? r.snippet ?? ''),
      createdAt: String(r.created_at ?? '')
    }))
    search.total = Number(res?.total ?? search.hits.length)
  } catch {
    search.hits = []
    search.total = 0
  }
}

/** Wires the two theme hooks the Orbit shell owns: persist when the surface
    changes, apply when another machine changed it. */
export function bindThemeSync(): void {
  const w = window as unknown as { __pebbleThemeSave?: (mode: string) => void }
  w.__pebbleThemeSave = (mode: string) => {
    const accent = getComputedStyle(document.documentElement).getPropertyValue('--accent').trim()
    void saveOrbitTheme(mode, accent)
  }
}

export async function pullOrbitTheme(): Promise<void> {
  const saved = await readOrbitTheme()
  if (!saved) return
  const w = window as unknown as { __pebbleThemeApply?: (mode: string, accent: string) => void }
  if (w.__pebbleThemeApply) w.__pebbleThemeApply(saved.mode, saved.accent)
}

/* ---------------- image viewer ---------------- */

const IMAGE_MIME: Record<string, string> = {
  png: 'image/png',
  jpg: 'image/jpeg',
  jpeg: 'image/jpeg',
  gif: 'image/gif',
  webp: 'image/webp',
  svg: 'image/svg+xml',
  bmp: 'image/bmp'
}

export function isImagePath(path: string): boolean {
  const ext = path.split('.').pop()?.toLowerCase() ?? ''
  return ext in IMAGE_MIME
}

/** Loads an image into the viewer sheet. The sheet is opened by the same class
    the prototype's overlay uses, so it animates and closes like every other. */
export async function openImage(path: string): Promise<boolean> {
  if (!gateway.connected || !path) return false
  const data = await readFileBase64(path)
  if (!data) {
    notify('Cannot open image', path, 'err')
    return false
  }
  const ext = path.split('.').pop()?.toLowerCase() ?? 'png'
  const mime = IMAGE_MIME[ext] || 'image/png'
  image.path = path
  image.name = path.split(/[\\/]/).pop() || path
  image.dataUrl = data.startsWith('data:') ? data : `data:${mime};base64,${data}`
  image.meta = ext.toUpperCase()
  const el = document.getElementById('sheet-image')
  el?.classList.add('open')
  el?.setAttribute('aria-hidden', 'false')
  return true
}

export { escapeHtml }
