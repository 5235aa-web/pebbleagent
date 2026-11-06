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
  running: false
})

/* ---- live generation rate ----

   Every chunk the backend emits already carries `tokens`, its running count for
   the call in flight, reasoning included. The divisor here is only the time
   between chunks, so tool execution, API round-trips and waiting on a clarify
   card never reach it. The previous readout divided visible text by the run's
   whole wall clock with a 3.6-characters-per-token guess, which is why it sat
   near zero: tool time inflated the divisor, reasoning was missing from the
   dividend and Chinese text was counted at an English ratio.

   A window opens on the first chunk after any other event and ends on that
   event, so an interleaved tool call stops the measurement instead of
   stretching it. When a call settles, `message.usage.latest_output_tokens`
   replaces the estimate banked for that call. */
export const runRate = reactive({
  tokens: 0,
  ms: 0,
  /* Open between two chunks of the same generation. */
  open: false,
  lastAt: 0,
  /* Tokens already banked for the call in flight: the backend counts from zero
     per call, so the next call's counter must not be read as a delta. */
  banked: 0,
  rate: 0,
  measured: false
})

/* A longer gap is a stall rather than generation, so it is dropped instead of
   inflating the divisor. */
const RATE_MAX_GAP_MS = 1200
/* One chunk a few milliseconds after the previous one is noise, not a rate. */
const RATE_MIN_MS = 250

function rateSettle(): void {
  if (runRate.ms < RATE_MIN_MS || runRate.tokens <= 0) return
  runRate.rate = runRate.tokens / (runRate.ms / 1000)
  runRate.measured = true
}

function rateChunk(rawTokens: unknown): void {
  const now = Date.now()
  const cum = Number(rawTokens)
  if (Number.isFinite(cum)) {
    /* A counter that went backwards means the next call started. */
    if (cum < runRate.banked) runRate.banked = 0
    const added = cum - runRate.banked
    if (added > 0) {
      runRate.tokens += added
      runRate.banked = cum
    }
  }
  if (runRate.open) {
    const gap = now - runRate.lastAt
    if (gap > 0 && gap <= RATE_MAX_GAP_MS) runRate.ms += gap
  }
  runRate.open = true
  runRate.lastAt = now
  rateSettle()
}

/* Events that mean the model stopped generating: a tool took over, the call
   settled, or the turn is waiting on a person. Side-channel pushes in the
   middle of a stream (todos, file changes, a session title) are not in here,
   so they cannot cut a generation window in half and inflate the rate. */
const RATE_STOP_EVENTS = new Set([
  'tool.start',
  'tool.complete',
  'permission.request',
  'clarify.request',
  'message.complete',
  'message.usage',
  'error'
])

/** Runs once per event before its branch: a chunk feeds the window, a real stop
    ends it, and a settled call rewrites its own share with the exact count the
    provider reported. */
function rateOnEvent(type: string, p: Record<string, unknown>): void {
  if (type === 'message.delta' || type === 'thinking.delta' || type === 'tool.call_streaming') {
    rateChunk(p.tokens)
    return
  }
  if (!RATE_STOP_EVENTS.has(type)) return
  runRate.open = false
  runRate.lastAt = 0
  /* Nothing was measured for this call, so there is no window to correct. */
  if (type !== 'message.usage' || !runRate.banked) return
  const exact = Number(p.latest_output_tokens)
  if (!Number.isFinite(exact) || exact < 0) return
  runRate.tokens = Math.max(0, runRate.tokens - runRate.banked + exact)
  runRate.banked = 0
  rateSettle()
}

function rateReset(): void {
  runRate.tokens = 0
  runRate.ms = 0
  runRate.open = false
  runRate.lastAt = 0
  runRate.banked = 0
  runRate.rate = 0
  runRate.measured = false
}

/** Tokens per second of generation for this run; `—` until a window lands, so a
    turn that produced nothing never reads as a plausible zero. The island and
    the usage pane both call this, which is what keeps them in agreement. */
export function formatRate(): string {
  return runRate.measured ? runRate.rate.toFixed(1) : '—'
}

/* ---- skills of the current turn ----

   The agent preloads a fixed list (the yaml `skills:`) and then pulls in more
   with `load_skill` as the task needs them. "skills loaded" describes exactly
   that per-turn union, so this list is cleared when the next turn starts rather
   than persisted: the catalogue on disk is a different thing and lives in
   `catalog.skillGroups`. */
export const turnSkills = reactive({ names: [] as string[] })

/** `load_skill` takes `{"name": ...}`; a half-streamed argument object fails to
    parse and is ignored, which is correct because the call has not run yet. */
function noteLoadedSkill(raw: string): void {
  try {
    const parsed = JSON.parse(raw) as { name?: unknown }
    const name = String(parsed?.name ?? '').trim()
    if (name && !turnSkills.names.includes(name)) turnSkills.names.push(name)
  } catch { /* not a complete argument object yet */ }
}

watch(
  () => streamState.streaming,
  (now, before) => {
    if (now && !before) {
      runTiming.startAt = Date.now()
      runTiming.elapsedMs = 0
      runTiming.running = true
      rateReset()
      turnSkills.names.splice(0, turnSkills.names.length)
      return
    }
    if (!now && before) {
      runTiming.elapsedMs = runTiming.startAt ? Date.now() - runTiming.startAt : 0
      runTiming.running = false
      runRate.open = false
      runRate.lastAt = 0
    }
  }
)
import { permissions, plan, sessions, sessionState, projects, setCatalog, catalog, bucketFor, type Session, type SessionGroup } from './store'
import { usage, files, mcp, ledger, context, llm, notes, pairing, search, image, isOpenCodeProvider, isProviderReady, type BillRow, type ClarifyQuestion, type ClarifyAnswer, type LlmProvider } from './panels'
import type { StreamItem } from './stream'
import { renderUserBubble, nowClock, nowStamp, clockOf, escapeHtml, type AttachmentView, type ToolCallView, type ToolState } from './render'
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

/* ---- live plan card ----

   The card hides itself once every step is done, but not on the same frame: an
   instant hide means the reader never sees the full ring and the last check,
   which is the only confirmation that the plan finished. A fresh list cancels a
   pending hide, so a plan written right after the previous one closed stays up. */
let planHideTimer = 0

function cancelPlanHide(): void {
  if (!planHideTimer) return
  window.clearTimeout(planHideTimer)
  planHideTimer = 0
}

/** Also wired to the card's own close button, which has to beat the timer. */
export function closePlan(): void {
  cancelPlanHide()
  plan.open = false
}

/** The plan belongs to the session on screen: a resume of another conversation
    drops it, otherwise the previous checklist stays up until the next
    `todos.update` happens to arrive. */
function resetPlan(): void {
  cancelPlanHide()
  plan.open = false
  plan.title = ''
  plan.sub = ''
  plan.token = ''
  plan.steps.splice(0, plan.steps.length)
}

function schedulePlanHide(): void {
  cancelPlanHide()
  planHideTimer = window.setTimeout(() => {
    planHideTimer = 0
    plan.open = false
  }, 1200)
}

/* ---- event handlers ---- */

function onEvent(ev: GatewayEvent) {
  const p = ev.payload
  rateOnEvent(ev.type, p)
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
          const row = streamState.items[idx]
          const adopted = String(p.message_id ?? row.id)
          /* The markup carries the pending id in data-item-id. Once the row adopts
             the stored id, a click that still reads the old value finds no row and
             the handler returns silently — copy and delete both went dead that way. */
          if (adopted !== row.id) {
            row.innerHTML = row.innerHTML.split('data-item-id="' + row.id + '"').join('data-item-id="' + adopted + '"')
            row.id = adopted
          }
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
      const rowId = String(p.message_id ?? nextId('user'))
      streamState.items.push({
        id: rowId,
        kind: 'message',
        className: 'msg user',
        attrs: {},
        innerHTML: renderUserBubble(text, attachments, nowClock(), Number(p.tokens) || undefined, rowId),
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
        // tool.start carries the complete argument object; the streaming phase
        // usually carries half of it, so a failed parse just means "not yet".
        if (name === 'load_skill') noteLoadedSkill(args)
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
        closePlan()
        return
      }
      /* A new list arrives before the old one finished hiding: keep it up. */
      cancelPlanHide()
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
      /* Every step done: the card retires on its own after the ring fills. */
      if (todos.every(t => String(t.status ?? '') === 'completed')) schedulePlanHide()
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
      files.state = 'ok'
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

    case 'mcp.state_changed': {
      /* The payload carries only per-server state; the rack is re-read so the
         counts, tool badges and error text come from one answer instead of
         being patched from a delta that may arrive out of order. */
      scheduleMcpRefresh()
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
        /* A reconnect is a recovery, not a replay: the list may have moved
           while the socket was down, so it is re-read instead of trusted. */
        void refreshSessionList()
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

/** `info.tools` and `info.skills` are grouped maps (`{"Tools": [...]}`), not
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
    resetPlan()
    if (result?.info?.model) streamState.model = result.info.model
    const info = result?.info as Record<string, unknown> | undefined
    sessionState.toolCount = countGrouped(info?.tools)
    sessionState.skillCount = countGrouped(info?.skills)
    setCatalog(info?.tools, info?.skills)
    catalog.agentSkills = Array.isArray(info?.agent_skills)
      ? (info.agent_skills as unknown[]).map(String).filter(Boolean)
      : []
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

/** Deletes a session and opens the neighbour when it was the one on screen.

    Returns false when the gateway refused. The row goes back where it was: a
    refused delete used to stay hidden, so the session looked deleted until the
    next restart brought it back. */
export async function deleteSession(id: string): Promise<boolean> {
  const i = sessions.findIndex(s => s.id === id)
  if (!id || i < 0) return false
  const wasActive = sessionState.activeId === id || streamState.sessionId === id
  const row = sessions[i]
  sessions.splice(i, 1)
  if (!gateway.connected) {
    /* Deleting a stored session is a gateway write. Offline it cannot happen,
       and reporting success would be a lie the next refresh exposes. */
    sessions.splice(Math.min(i, sessions.length), 0, row)
    notify('Delete failed', 'The gateway is offline, so the session was not deleted.', 'err')
    return false
  }
  try {
    await gateway.request('session.delete', { session_id: id })
  } catch (e) {
    sessions.splice(Math.min(i, sessions.length), 0, row)
    notify('Delete refused', e instanceof Error ? e.message : 'the gateway kept the session', 'err')
    return false
  }
  if (wasActive) {
    const next = sessions[i] ?? sessions[i - 1] ?? null
    if (!next) {
      sessionState.activeId = ''
      streamState.sessionId = null
      clearStream()
    } else {
      await loadSession(next.id)
    }
  }
  return true
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
/** Flattened copy of one answer, for the broadcast that only announces the
    reply. The structured payload is what the agent actually reads. */
function answerText(answer: ClarifyAnswer): string {
  const picks = answer.picked.join(', ')
  const typed = answer.custom.trim()
  if (picks && typed) return `${picks} · ${typed}`
  return picks || typed
}

export function respondClarify(requestId: string, answers: Record<string, ClarifyAnswer>): Promise<boolean> {
  if (!gateway.connected || !requestId) return Promise.resolve(false)
  // Annotations made on the preview travel with the answer that asks for a
  // change, so the agent gets them as the instruction instead of a side note.
  const merged = mergePreviewComments(answers)
  const flat = Object.values(merged).map(answerText).join(' | ')
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
  /* `message.usage` names the model the backend actually called. The live row
     paint and the header both read `streamState.model`, so accepting this
     value keeps the label truthful even when the configured model, the active
     provider card and the resolved endpoint disagree. */
  const usedModel = String(p.model ?? '').trim()
  if (usedModel) streamState.model = usedModel
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
  /* Real prompt parts from the gateway. The panel subtracts them from
     `contextTokens` to get the conversation share, so no part is a guess. */
  const breakdown = p.context_breakdown
  if (breakdown && typeof breakdown === 'object') {
    const parts = breakdown as Record<string, unknown>
    usage.contextBase = Math.max(0, Number(parts.system_base) || 0)
    usage.contextSkills = Math.max(0, Number(parts.skills) || 0)
    usage.contextTools = Math.max(0, Number(parts.tools) || 0)
  }
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
  /* `period` is the billing window (peak / offpeak), never a currency. Comparing
     it against 'cny' failed for every time-of-day model and flipped the whole
     pane to `$`, so it is no longer read here: `usage.stats` is the only
     authority on the currency the table is kept in. */

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
    const res = await gateway.request<{ paths?: unknown; rejected?: unknown }>('path_whitelist.read', {})
    const list = Array.isArray(res?.paths) ? res.paths.map(String) : []
    files.paths = list.map(p => ({ path: p, mode: 'read · write · execute' }))
    const rej = Array.isArray(res?.rejected) ? (res.rejected as Array<Record<string, unknown>>) : []
    files.rejected = rej.map(x => ({ path: String(x.path ?? ''), reason: String(x.reason ?? 'rejected') }))
  } catch {
    /* Leave the last known roots in place; an empty list would read as "no
       boundary configured", which is a different claim. */
  }
}

/** Rewrites the file with the usable rows only, which is the one way to clear
    entries the gateway refuses to save. */
export function dropRejectedPaths(): void {
  if (!files.rejected.length) return
  files.rejected = []
  pushPaths()
}

function pushPaths(): void {
  void gateway.request('path_whitelist.save', { paths: files.paths.map(p => p.path) })
    .catch((e) => {
      notify('Path not saved', e instanceof Error ? e.message : 'The gateway refused the change.', 'err')
      /* The rows on screen are what the gateway accepted, not what was typed. */
      void refreshPaths()
    })
}

/** Every refusal names its reason. The old boolean returned false for a blank
    field, a duplicate, a closed gateway and the fifth root alike, so the pane
    had nothing to say and the click read as dead. */
export function addWhitelistPath(path: string): { ok: boolean; error?: string } {
  const p = path.trim()
  if (!p) return { ok: false, error: 'Enter a path first.' }
  if (!gateway.connected) return { ok: false, error: 'Not connected to the gateway.' }
  if (files.paths.some(x => x.path === p)) return { ok: false, error: 'That path is already listed.' }
  if (files.paths.length >= 5) return { ok: false, error: 'Five roots is the ceiling; remove one first.' }
  files.paths = [...files.paths, { path: p, mode: 'read · write · execute' }]
  pushPaths()
  return { ok: true }
}

export function updateWhitelistPath(index: number, path: string): { ok: boolean; error?: string } {
  const p = path.trim()
  if (!p) return { ok: false, error: 'Enter a path first.' }
  if (!gateway.connected) return { ok: false, error: 'Not connected to the gateway.' }
  if (!(index >= 0 && index < files.paths.length)) return { ok: false, error: 'That row is gone; reopen the panel.' }
  if (files.paths.some((x, i) => i !== index && x.path === p)) return { ok: false, error: 'That path is already listed.' }
  files.paths = files.paths.map((x, i) => (i === index ? { ...x, path: p } : x))
  pushPaths()
  return { ok: true }
}

export function removeWhitelistPath(path: string): { ok: boolean; error?: string } {
  if (!gateway.connected) return { ok: false, error: 'Not connected to the gateway.' }
  const next = files.paths.filter(x => x.path !== path)
  if (next.length === files.paths.length) return { ok: false, error: 'That path is not in the list.' }
  files.paths = next
  pushPaths()
  return { ok: true }
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

/** The scratchpad is session-only by design (the gateway stores one list), the
    user note is the standing instruction prepended to every turn, and the
    notebook is the reader's own journal — global, never sent anywhere. All
    three are read together because the notes pane shows them stacked. */
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

  try {
    const res = await gateway.request<{ entries?: unknown }>('notebook.read', {})
    const list = Array.isArray(res?.entries) ? res.entries : []
    notes.notebook = list.map((e, i) => {
      const row = (e ?? {}) as Record<string, unknown>
      return {
        id: String(row.id ?? '') || 'entry-' + i,
        time: String(row.time ?? ''),
        text: String(row.text ?? '')
      }
    })
  } catch { /* keep the prototype journal */ }
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

/* The notebook is a journal, not a scratchpad: it is global, it survives the
   session, and nothing in it ever reaches the composer or the prompt. */

function pushNotebook(): void {
  void gateway.request('notebook.write', {
    entries: notes.notebook.map(e => ({ id: e.id, time: e.time, text: e.text }))
  }).catch(() => { void refreshNotes() })
}

/** Opens a draft with the current stamp already on it. The row stays local
    until it has text, so an entry opened and abandoned leaves nothing behind. */
export function addNotebookEntry(): string {
  const id = Date.now().toString(36) + Math.random().toString(36).slice(2, 7)
  notes.notebook = [{ id, time: nowStamp(), text: '' }, ...notes.notebook]
  return id
}

/** Writes a draft back; an entry emptied out is dropped instead of stored. */
export function saveNotebookEntry(id: string, text: string): void {
  const entry = notes.notebook.find(e => e.id === id)
  if (!entry) return
  const body = text.trim()
  if (!body) {
    discardNotebookEntry(id)
    return
  }
  entry.text = body
  if (gateway.connected) pushNotebook()
}

export function removeNotebookEntry(id: string): void {
  notes.notebook = notes.notebook.filter(e => e.id !== id)
  if (gateway.connected) pushNotebook()
}

/** Drops a draft without writing: the file never held it in the first place. */
function discardNotebookEntry(id: string): void {
  notes.notebook = notes.notebook.filter(e => e.id !== id)
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
        ready: Boolean(s.ready ?? true),
        missing: Array.isArray(s.missing) ? (s.missing as string[]) : [],
        installable: Boolean(s.installable),
        requiresToken: Boolean(s.requires_token),
        tokenEnv: String(s.token_env ?? ''),
        token: ''
      }
    })
    mcp.running = list.filter(s => s.connected).length
    mcp.idle = list.filter(s => s.enabled && !s.connected).length
    mcp.tools = list.reduce((n, s) => n + Number(s.tools ?? 0), 0)

    /* Both queues are replaced wholesale: a banner left over from the previous
       answer would keep asking for an approval that already happened. */
    const project = (Array.isArray(mc.pending_project_servers) ? mc.pending_project_servers : []) as Array<Record<string, unknown>>
    const first = project[0]
    mcp.pendingProject = first
      ? {
          path: String(first.path ?? ''),
          servers: Array.isArray(first.servers) ? (first.servers as string[]) : []
        }
      : null
    const userPending = (Array.isArray(mc.pending_user_servers) ? mc.pending_user_servers : []) as Array<Record<string, unknown>>
    mcp.pendingUser = userPending.map(p => ({
      name: String(p.name ?? ''),
      target: String(p.target ?? '')
    }))

    /* Declared in mcp_install.json but missing from .mcp.json. Replaced
       wholesale like the pending queues: a server installed in this pass must
       disappear from the row, not linger until the next reload. */
    const avail = Array.isArray(mc.available) ? (mc.available as Array<Record<string, unknown>>) : []
    mcp.available = avail.map(a => ({
      name: String(a.name ?? ''),
      desc: String(a.desc ?? ''),
      missing: Array.isArray(a.missing) ? (a.missing as string[]) : []
    }))
  } catch { /* keep prototype numbers */ }
}

/* The backend pushes one event per server as it connects or drops, so a cold
   start arrives as a burst of three to six. Coalescing keeps the panel honest
   without firing one mcp.list per server. */
let mcpRefreshTimer = 0

function scheduleMcpRefresh(): void {
  if (mcpRefreshTimer) return
  mcpRefreshTimer = window.setTimeout(() => {
    mcpRefreshTimer = 0
    void refreshMcp()
  }, 250)
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
    .catch((e) => {
      /* A missing prerequisite (Node, uv) is the common failure and the backend
         names it; swallowing it left the Install button looking dead. */
      notify('Install failed', e instanceof Error ? e.message : 'The server refused the install.', 'err')
    })
}

/** Approves the project-level .mcp.json, which also hot-loads its servers. */
export function trustMcpProject(): void {
  if (!gateway.connected) return
  const path = String(mcp.pendingProject?.path ?? '')
  if (!path) return
  void gateway.request('mcp.trust_project', { path })
    .then(() => { void refreshMcp() })
    .catch(() => { /* keep the pending banner */ })
}

/** Approves one entry of the user-level file. An entry becomes a process, so
    this is the step that lets an agent-written server run at all. */
export function approveMcpServer(name: string): void {
  if (!gateway.connected || !name) return
  void gateway.request('mcp.approve_server', { server: name })
    .then(() => { void refreshMcp() })
    .catch(() => { /* stays in the queue */ })
}

/** Withdraws approval, which also stops a server that is already connected. */
export function rejectMcpServer(name: string): void {
  if (!gateway.connected || !name) return
  void gateway.request('mcp.reject_server', { server: name })
    .then(() => { void refreshMcp() })
    .catch(() => { /* stays in the queue */ })
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
function providerFromWire(p: Record<string, unknown>, existing?: LlmProvider): LlmProvider {
  const models = Array.isArray(p.models) ? (p.models as unknown[]).map(String) : []
  const baseUrl = String(p.baseUrl ?? '')
  const mapped: LlmProvider = {
    name: String(p.name ?? 'provider'),
    slug: String(p.slug ?? ''),
    models,
    authenticated: Boolean(p.authenticated),
    current: Boolean(p.is_current),
    ready: typeof p.ready === 'boolean' ? Boolean(p.ready) : Boolean(baseUrl.trim() && models.length),
    apiKey: String(p.apiKey ?? ''),
    baseUrl,
    temperature: numOf(p.temperature, 1),
    topP: numOf(p.topP, 1),
    reasoningEffort: textOf(p.reasoning_effort) || 'high',
    thinkingEnabled: p.thinking_enabled !== false,
    extraHeaders: JSON.stringify(
      p.extraHeaders && typeof p.extraHeaders === 'object' ? p.extraHeaders : {},
      null,
      2
    ),
    sessionHeader: textOf(p.sessionHeader),
    reasoningMode: textOf(p.reasoningMode) || 'thinking',
    maxEffort: textOf(p.maxEffort) || 'downgrade',
    dirty: false
  }
  /* Keep a card the reader is still editing. Without this a gateway.ready or
     model.changed refresh replaces the provider object and silently discards a
     half-typed key or base url. */
  if (existing?.dirty) {
    return { ...existing, current: mapped.current, authenticated: mapped.authenticated, ready: mapped.ready }
  }
  return mapped
}

function providerEditSnapshot(p: LlmProvider): string {
  return JSON.stringify({
    name: p.name,
    baseUrl: p.baseUrl,
    apiKey: p.apiKey,
    models: p.models,
    temperature: p.temperature,
    topP: p.topP,
    reasoningEffort: p.reasoningEffort,
    thinkingEnabled: p.thinkingEnabled,
    extraHeaders: p.extraHeaders,
    sessionHeader: p.sessionHeader,
    reasoningMode: p.reasoningMode,
    maxEffort: p.maxEffort
  })
}

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
    const bySlug = new Map(llm.providers.filter(p => p.slug).map(p => [p.slug, p]))
    llm.providers = provs.map(p => providerFromWire(p, bySlug.get(String(p.slug ?? ''))))

    const idx = llm.providers.findIndex(p => p.current)
    const active = idx >= 0 ? llm.providers[idx] : llm.providers[0]
    if (active) {
      llm.provider = active.name
      llm.temperature = active.temperature
      llm.topP = active.topP
      llm.reasoningEffort = active.reasoningEffort
      llm.thinkingEnabled = active.thinkingEnabled
      llm.reasoningMode = active.reasoningMode
      llm.maxEffort = active.maxEffort
      llm.sessionHeader = active.sessionHeader
      llm.extraHeaders = active.extraHeaders
    } else {
      /* Deleting the last card must clear the pane, not leave a ghost row that
         can still be saved or deleted. */
      llm.provider = ''
      llm.temperature = 1
      llm.topP = 1
      llm.reasoningEffort = 'high'
      llm.thinkingEnabled = true
      llm.reasoningMode = 'thinking'
      llm.maxEffort = 'downgrade'
      llm.sessionHeader = ''
      llm.extraHeaders = '{}'
      streamState.model = ''
    }
    /* The backend returns the effective model (active provider's fallback
       included). With no providers at all there is no model to keep; an empty
       list must clear the header instead of leaving a stale name. */
    llm.model = provs.length && typeof opts.model === 'string' ? opts.model : ''
    if (typeof opts.currency === 'string' && opts.currency) llm.currency = opts.currency
    if (opts.pricing && typeof opts.pricing === 'object') llm.pricing = opts.pricing as typeof llm.pricing
    llm.loaded = true
  } catch { /* keep the last good state, but never mark an unreadable pane loaded */ }
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

/** Switches the model on the active provider. The backend only accepts a model
    the active provider lists; the UI waits for that answer instead of painting
    a selection the next turn would not use. */
export function switchModel(model: string): Promise<boolean> {
  if (!gateway.connected || !model) return Promise.resolve(false)
  return gateway.request<{ ok?: boolean; error?: string }>('model.switch', { model })
    .then(res => {
      if (res?.ok === false) {
        notify('Switch failed', res.error || 'The gateway refused the model switch.', 'err')
        void refreshLlm()
        return false
      }
      llm.model = model
      streamState.model = model
      return true
    })
    .catch(e => {
      notify('Switch failed', e instanceof Error ? e.message : 'The gateway refused the model switch.', 'err')
      void refreshLlm()
      return false
    })
}

/** Writes the sampling block of the selected provider. `model.params.set` owns
    these four fields; everything else is saved through provider.save. */
export function setModelParams(index?: number): Promise<boolean> {
  if (!gateway.connected) return Promise.resolve(false)
  const hasIndex = typeof index === 'number'
  const p = hasIndex ? llm.providers[index as number] : undefined
  if (hasIndex && !p) {
    notify('Settings not saved', 'That provider card no longer exists.', 'err')
    return Promise.resolve(false)
  }
  const payload: Record<string, unknown> = p
    ? {
        index,
        temperature: p.temperature,
        topP: p.topP,
        reasoning_effort: p.reasoningEffort,
        thinking_enabled: p.thinkingEnabled
      }
    : {
        temperature: llm.temperature,
        topP: llm.topP,
        reasoning_effort: llm.reasoningEffort,
        thinking_enabled: llm.thinkingEnabled
      }
  return gateway.request<{ ok?: boolean; error?: string }>('model.params.set', payload)
    .then(res => {
      if (res?.ok === false) {
        notify('Settings not saved', res.error || 'The gateway refused the parameter change.', 'err')
        return false
      }
      return true
    })
    .catch(e => {
      notify('Settings not saved', e instanceof Error ? e.message : 'The gateway refused the parameter change.', 'err')
      return false
    })
}

/** Saves the selected provider card. Request shaping is OpenCode-only, so other
    providers omit it entirely. Returns false (and leaves the local draft in
    place) on validation or gateway errors. */
export function saveProvider(index: number): Promise<boolean> {
  if (!gateway.connected) {
    notify('Save skipped', 'Not connected to the gateway, so nothing was written.', 'err')
    return Promise.resolve(false)
  }
  const p = llm.providers[index]
  if (!p) {
    notify('Save skipped', 'No provider card is selected.', 'err')
    return Promise.resolve(false)
  }

  const name = p.name.trim()
  if (!name) {
    notify('Save skipped', 'Provider name cannot be empty.', 'err')
    return Promise.resolve(false)
  }
  const baseUrl = p.baseUrl.trim()
  if (baseUrl && !/^https?:\/\//i.test(baseUrl)) {
    notify('Save skipped', 'Base url must start with http:// or https://.', 'err')
    return Promise.resolve(false)
  }
  const models = p.models.map(m => m.trim()).filter(Boolean)
  if (models.some(m => /\s/.test(m))) {
    notify('Save skipped', 'Model ids must not contain spaces. Use the provider\'s exact model id.', 'err')
    return Promise.resolve(false)
  }

  let shaping: Record<string, unknown> = {}
  if (isOpenCodeProvider(name)) {
    let headers: Record<string, unknown> = {}
    try {
      const parsed = JSON.parse(p.extraHeaders || '{}')
      if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) {
        throw new Error('extra headers must be a JSON object')
      }
      headers = parsed as Record<string, unknown>
    } catch (e) {
      notify(
        'Invalid extra headers',
        e instanceof Error ? e.message : 'Fix the JSON before saving; the existing headers were left untouched.',
        'err'
      )
      return Promise.resolve(false)
    }
    shaping = {
      extraHeaders: headers,
      sessionHeader: p.sessionHeader,
      reasoningMode: p.reasoningMode,
      maxEffort: p.maxEffort
    }
  }

  const snapshot = providerEditSnapshot(p)
  return gateway.request<{ ok?: boolean; error?: string }>('model.provider.save', {
    index,
    provider: {
      slug: p.slug,
      name,
      apiKey: p.apiKey.trim(),
      baseUrl,
      models,
      temperature: p.temperature,
      topP: p.topP,
      reasoning_effort: p.reasoningEffort,
      thinking_enabled: p.thinkingEnabled,
      ...shaping
    }
  }).then(async res => {
    const current = llm.providers[index] ?? p
    if (res?.ok === false) {
      current.dirty = true
      notify('Save failed', res.error || 'The gateway refused the write.', 'err')
      return false
    }
    /* Keep dirty while the request is in flight so a reconnect refresh cannot
       replace a half-typed value. Clear it only when the card still matches the
       snapshot that was actually written. */
    if (providerEditSnapshot(current) === snapshot) current.dirty = false
    const ready = isProviderReady(current)
    const hasKey = Boolean(current.apiKey.trim())
    const message = !ready
      ? current.name + ' saved as a draft. Add a base url and at least one model before it can become active.'
      : !hasKey
        ? current.name + ' saved. No API key is set; local endpoints may still work, hosted ones will reject requests.'
        : current.name + ' written to the provider config.'
    notify('Saved', message, ready ? (hasKey ? 'ok' : 'warn') : 'warn')
    await refreshLlm()
    return true
  }).catch(e => {
    const current = llm.providers[index] ?? p
    current.dirty = true
    notify('Save failed', e instanceof Error ? e.message : 'The gateway refused the write.', 'err')
    return false
  })
}

export function deleteProvider(index: number): Promise<boolean> {
  if (!gateway.connected) {
    notify('Delete skipped', 'Not connected to the gateway, so nothing was removed.', 'err')
    return Promise.resolve(false)
  }
  const p = llm.providers[index]
  if (!p) {
    notify('Delete skipped', 'No provider card is selected.', 'err')
    return Promise.resolve(false)
  }
  return gateway.request<{ ok?: boolean; error?: string }>('model.provider.delete', { index })
    .then(async res => {
      if (res?.ok === false) {
        notify('Delete failed', res.error || 'The gateway refused the delete.', 'err')
        return false
      }
      notify('Deleted', p.name + ' removed from the provider config.', 'warn')
      await refreshLlm()
      return true
    })
    .catch(e => {
      notify('Delete failed', e instanceof Error ? e.message : 'The gateway refused the delete.', 'err')
      return false
    })
}

/** Appends a draft provider card without making it active. The returned index is
    the card the caller should select for editing. */
export function addProvider(): Promise<number | null> {
  if (!gateway.connected) {
    notify('Add skipped', 'Not connected to the gateway, so nothing was written.', 'err')
    return Promise.resolve(null)
  }
  return gateway.request<{ ok?: boolean; index?: number; error?: string }>('model.provider.save', {
    index: -1,
    provider: {
      name: 'New provider',
      apiKey: '',
      baseUrl: '',
      models: [],
      temperature: 1,
      topP: 1,
      reasoning_effort: 'high',
      thinking_enabled: true
    }
  }).then(async res => {
    if (res?.ok === false) {
      notify('Add failed', res.error || 'The gateway refused the write.', 'err')
      return null
    }
    notify(
      'Provider added',
      'This card is a draft. Fill in its base url and key, add a model, then save — it becomes active only when ready.',
      'ok'
    )
    await refreshLlm()
    return typeof res?.index === 'number' ? res.index : null
  }).catch(e => {
    notify('Add failed', e instanceof Error ? e.message : 'The gateway refused the write.', 'err')
    return null
  })
}

/** Switches the active provider. The backend rejects a draft provider, so a card
    without a base url/model cannot make the endpoint disagree with the
    highlighted settings card. */
export function switchProvider(index: number, model: string): Promise<boolean> {
  if (!gateway.connected) {
    notify('Switch skipped', 'Not connected to the gateway, so nothing changed.', 'err')
    return Promise.resolve(false)
  }
  return gateway.request<{ ok?: boolean; error?: string }>('model.provider.set', { index, model })
    .then(async res => {
      if (res?.ok === false) {
        notify('Switch failed', res.error || 'The gateway refused the change.', 'err')
        await refreshLlm()
        return false
      }
      await refreshLlm()
      return true
    })
    .catch(async e => {
      notify('Switch failed', e instanceof Error ? e.message : 'The gateway refused the change.', 'err')
      await refreshLlm()
      return false
    })
}

/* ---------------- message actions ---------------- */

type ToastFn = (title: string, message: string, kind?: string) => void

function notify(title: string, message: string, kind: 'ok' | 'warn' | 'err' = 'ok'): void {
  if (typeof window === 'undefined') return
  const fn = (window as unknown as { __pebbleToast?: ToastFn }).__pebbleToast
  if (fn) fn(title, message, kind)
}

/** Copies the row's plain text. The clipboard API can refuse inside the Tauri
    webview, so a scratch textarea is the fallback rather than a silent no-op.
    Every path reports: an empty row says so, and a rejected write retries
    through the textarea instead of printing "Copied" over a failed copy. */
export function copyMessage(text: string): boolean {
  const body = text.trim()
  if (!body) {
    notify('Nothing to copy', 'That row carries no text.', 'warn')
    return false
  }
  const scratch = (): boolean => {
    const ta = document.createElement('textarea')
    ta.value = body
    ta.style.position = 'fixed'
    ta.style.opacity = '0'
    document.body.appendChild(ta)
    try {
      ta.select()
      /* execCommand answers whether the copy happened; ignoring it kept the
         "Copied" toast on top of a refused write. */
      if (!document.execCommand('copy')) {
        notify('Copy failed', 'The clipboard is not available here.', 'err')
        return false
      }
      notify('Copied', body.slice(0, 60))
      return true
    } catch {
      notify('Copy failed', 'The clipboard is not available here.', 'err')
      return false
    } finally {
      ta.remove()
    }
  }
  try {
    const wrote = navigator.clipboard?.writeText(body)
    if (!wrote) return scratch()
    wrote.then(() => notify('Copied', body.slice(0, 60))).catch(scratch)
    return true
  } catch {
    return scratch()
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
  if (!gateway.connected) {
    files.state = 'failed'
    return
  }
  const sid = streamState.sessionId || sessionState.activeId
  /* No session yet: asking with an empty id only earns an error. Leave the
     state alone — session load and `file.change` both call back here. */
  if (!sid) return
  files.state = 'loading'
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
    files.state = 'ok'
  } catch {
    /* Keep whatever the pane already shows, but say the read failed: an empty
       list and a refused call look identical otherwise. */
    files.state = 'failed'
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
    files.state = 'ok'
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
  if (!gateway.connected) {
    /* Nothing can be read offline. If the ledger never loaded, this is a failed
       read, not an empty one. */
    if (ledger.state === 'idle' || ledger.state === 'loading') ledger.state = 'failed'
    return
  }
  /* Only a real session uuid can answer `usage.stats range=session`. The local
     list id (`s1`) resolves to nothing, and that empty payload would overwrite the
     live totals with zeros on every dock refresh. */
  const sid = streamState.sessionId || (sessionState.activeId.length >= 20 ? sessionState.activeId : '')

  try {
    /* Four windows, one request each. The session one needs a real uuid, so it is
       rejected (not thrown) when there is none: the three global windows do not
       depend on a session and must survive its absence. */
    const [sessionStats, todayStats, weekStats, monthStats] = await Promise.allSettled([
      sid
        ? gateway.request<Record<string, unknown>>('usage.stats', { range: 'session', session_id: sid })
        : Promise.reject(new Error('no session attached')),
      gateway.request<Record<string, unknown>>('usage.stats', { range: 'today' }),
      gateway.request<Record<string, unknown>>('usage.stats', { range: '7d' }),
      gateway.request<Record<string, unknown>>('usage.stats', { range: '30d' })
    ])
    const costOf = (r: PromiseSettledResult<Record<string, unknown>>): number | null =>
      r.status === 'fulfilled' && typeof r.value.total_cost === 'number' ? r.value.total_cost : null
    /* The whole database's day / week / month are answered before the session is
       judged valid. A missing or unreadable session used to freeze these three on
       their last values, which read as "nothing spent" over a ledger that kept
       growing. */
    const todayCost = costOf(todayStats)
    if (todayCost !== null) usage.costToday = todayCost
    const weekCost = costOf(weekStats)
    if (weekCost !== null) usage.cost7d = weekCost
    const monthCost = costOf(monthStats)
    if (monthCost !== null) usage.cost30d = monthCost

    if (sessionStats.status !== 'fulfilled') throw new Error('session usage unavailable')
    const st = sessionStats.value
    /* The session's own total and the whole database's day are different
       questions; they used to share one field, so the hero read "today" over the
       session's number. */
    if (typeof st.total_cost === 'number') usage.sessionCost = st.total_cost
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
  } catch { /* leave the last known figures alone */ }

  await refreshFileChanges()

  await refreshMcp()

  await refreshNotes()

  await refreshPaths()

  await refreshPairing()

  /* Set here, not at the top of the pass: the calls above are unrelated reads,
     and a throw among them would leave the status stuck on "loading" forever. */
  ledger.state = 'loading'
  try {
    const mem = await gateway.request<Record<string, unknown>>('memory.list', { limit: 50 })
    const list = Array.isArray(mem.memories) ? (mem.memories as Array<Record<string, unknown>>) : []
    /* An empty answer replaces the list instead of leaving the previous rows
       behind: a ledger with nothing in it is a state the panel can describe. */
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
    ledger.state = 'ok'
  } catch {
    /* Keep the last known rows but say the read failed; the panel must not
       render an unreachable gateway as an empty ledger. */
    ledger.state = 'failed'
  }
}

/** Re-reads the session list. `null` means the call failed, an empty array
    means the project genuinely has no sessions. Collapsing the two — the old
    contract — is what kept the seeded prototype rows on screen forever. */
async function fetchSessions(): Promise<Array<Record<string, unknown>> | null> {
  if (!gateway.connected) return null
  try {
    /* Scoped to the active project: a session belongs to the directory it was
       created under, and an unscoped list is what left another project's
       conversations on screen after a switch. */
    const res = await gateway.request<{ sessions?: Array<Record<string, unknown>> }>('session.list', {
      limit: 200,
      project_path: projects.active || undefined
    })
    return Array.isArray(res?.sessions) ? res.sessions : null
  } catch {
    return null
  }
}

async function refreshSessionList(): Promise<void> {
  sessionState.listState = 'loading'
  const list = await fetchSessions()
  if (list === null) {
    sessionState.listState = 'failed'
    return
  }
  sessionState.listState = 'ok'
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
  /* A term typed while one project was open should not filter the next one's
     sessions into an empty panel. */
  sessionState.filter = ''
  /* Sessions belong to the project they were created under, so the registry is
     resolved before the list is asked for. An unscoped list is what left the
     previous project's conversations on screen after a switch. */
  if (!projects.loaded) await refreshProjects()
  sessionState.projectPath = projects.active

  sessionState.listState = 'loading'
  let list: Array<Record<string, unknown>> | null = null
  try {
    const res = await gateway.request<{ sessions?: Array<Record<string, unknown>> }>('session.list', {
      limit: 200,
      project_path: projects.active || undefined
    })
    list = Array.isArray(res?.sessions) ? res.sessions : null
  } catch {
    /* A failed call says nothing about the project. Leaving the panel alone is
       the honest answer; reporting zero sessions would erase real rows. */
  }

  if (list === null) {
    sessionState.listState = 'failed'
    return
  }
  sessionState.listState = 'ok'

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
    /* First contact also fills the dock: usage, files, notes and the experience
       ledger belong to the session that just opened. Without this the ledger
       stayed "idle" until the user happened to touch the dock. */
    void refreshPanes()
    return
  }

  /* The project really has no sessions. Drop whatever the switcher is holding
     before creating one: the seeded prototype rows are not a fallback, they are
     other conversations, and they stayed clickable and switchable. */
  sessions.splice(0, sessions.length)

  /* No session stored for this project yet. Going through createSession keeps
     the project_path attached and puts the row in the switcher immediately,
     instead of leaving an unassigned session the list never shows. */
  await createSession()
  void refreshPanes()
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

/** Same call as searchFiles, but keeps the gateway's error so the project tree
    can tell an empty folder from a refused read. */
export async function searchProjectDir(path = '', limit = 200): Promise<{ rows: Array<Record<string, unknown>>; error: string }> {
  if (!gateway.connected) return { rows: [], error: 'gateway offline' }
  try {
    const params: Record<string, unknown> = { q: '', limit }
    if (path) params.path = path
    const res = await gateway.request<{ results?: unknown; error?: unknown }>('file.search', params)
    const rows = Array.isArray(res?.results) ? (res.results as Array<Record<string, unknown>>) : []
    return { rows, error: String(res?.error ?? '') }
  } catch (e) {
    return { rows: [], error: e instanceof Error ? e.message : 'could not read this folder' }
  }
}

export function openFile(path: string): void {  if (!gateway.connected || !path) return
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
    locally, so the export matches what the model actually saw. Takes an explicit
    id because the context menu can export a row that is not the open one. */
export async function exportSessionMarkdown(sessionId?: string): Promise<string> {
  if (!gateway.connected) return ''
  const sid = sessionId || streamState.sessionId || sessionState.activeId
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

export interface AdoptResult {
  ok: boolean
  adopted: number
  error?: string
}

/** Adopts every session that has no project yet. The count comes from the
    gateway, so the panel reports what actually moved instead of a guess. */
export async function adoptUnassigned(projectPath: string): Promise<AdoptResult> {
  if (!gateway.connected) return { ok: false, adopted: 0, error: 'gateway offline' }
  if (!projectPath) return { ok: false, adopted: 0, error: 'no active project' }
  try {
    const res = await gateway.request<{ adopted?: number }>('session.adopt_unassigned', { project_path: projectPath })
    await refreshSessionList()
    return { ok: true, adopted: Number(res?.adopted ?? 0) }
  } catch (e) {
    return { ok: false, adopted: 0, error: e instanceof Error ? e.message : 'could not adopt sessions' }
  }
}

/* ---------------- projects ---------------- */

/** Loads the registry. `active` is authoritative: the gateway resolves it to a
    concrete directory, so the switcher shows the same path the tools will use. */
export async function refreshProjects(): Promise<void> {
  if (!gateway.connected) return
  try {
    const res = await gateway.request<{ projects?: string[]; active?: string; corrupt?: boolean; backup?: string }>('project.list', {})
    const list = Array.isArray(res?.projects) ? res.projects.map(String) : []
    projects.list.splice(0, projects.list.length, ...list)
    projects.active = String(res?.active ?? '')
    projects.corrupt = res?.corrupt === true
    projects.backup = String(res?.backup ?? '')
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
    const res = await gateway.request<{ added?: string; active?: string }>('project.add', { path: p })
    await refreshProjects()
    /* The gateway answers with the exact path it stored. Guessing it by
       comparing the user's spelling and falling back to the last row opened an
       unrelated project whenever realpath resolved case or a link differently. */
    const target = String(res?.added ?? '') || String(res?.active ?? '')
    if (target) await switchProject(target)
    return { ok: true }
  } catch (e) {
    return { ok: false, error: e instanceof Error ? e.message : 'add failed' }
  }
}

/** Points the gateway at a directory and reloads the surface: every session row
    belongs to the project that was active when it was created. A running turn is
    refused rather than carried across: its tools resolve paths against the
    service's current root, so switching underneath it writes into the new
    project halfway through the reply. */
export async function switchProject(path: string): Promise<boolean> {
  if (!gateway.connected) return false
  /* The gateway owns this decision: it reads the live worker registry, which
     cannot go stale. The local flag is only a shortcut so a running reply does
     not wait for a round trip, and it is skipped while the socket is down:
     a disconnect can leave `streaming` stuck on, which would lock every switch
     behind a reply that is no longer running. */
  if (streamState.streaming && streamState.gatewayOnline) {
    notify(
      'Cannot switch project',
      'A reply is still running. Stop it first, then switch the project root.',
      'warn'
    )
    return false
  }
  const sid = streamState.sessionId || ''
  try {
    await gateway.request('project.set_active', { path, session_id: sid || undefined })
  } catch (e) {
    /* The gateway refuses a missing folder, a stale registry entry, a corrupt
       registry and a turn still running anywhere. Reporting it is the only way
       the panel stops looking like the switch worked. */
    notify(
      'Project switch failed',
      e instanceof Error ? e.message : 'The gateway refused this project.',
      'err'
    )
    return false
  }
  projects.active = path
  sessionState.projectPath = path
  /* The switch already landed. A reload that fails afterwards must not be
     reported as a refusal, or the panel contradicts the gateway. */
  try {
    await refreshProjects()
    await bootstrap()
  } catch {
    notify(
      'Project switched',
      'The surface could not reload. Reopen the window if it looks stale.',
      'warn'
    )
  }
  return true
}

/** Drops a directory from the registry. When it was the active one the gateway
    rewrites `active` itself, so the list is re-read rather than guessed. */
export interface RemoveResult {
  ok: boolean
  error?: string
}

export async function removeProject(path: string): Promise<RemoveResult> {
  if (!gateway.connected) return { ok: false, error: 'gateway offline' }
  try {
    await gateway.request('project.delete', { path })
    await refreshProjects()
    return { ok: true }
  } catch (e) {
    /* A refusal is the gateway's answer (a corrupt registry, for instance), so
       the list is re-read from it rather than kept as if the row had gone. */
    await refreshProjects()
    return { ok: false, error: e instanceof Error ? e.message : 'could not remove this project' }
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
/** Writes the pricing table. `pricing.save` answers `{ok: false, error}` for a
    rejected table instead of a JSON-RPC error, so returning "did the request
    throw" reported every validation failure as a success: the panel said
    "saved" while the file on disk was untouched. */
export async function savePricing(
  models: Record<string, unknown>,
  currency = 'cny'
): Promise<{ ok: boolean; error: string }> {
  if (!gateway.connected) return { ok: false, error: 'the gateway is offline' }
  try {
    const res = await gateway.request<{ ok?: boolean; error?: string; count?: number }>('pricing.save', {
      models,
      default_currency: currency
    })
    if (res?.ok === false) return { ok: false, error: String(res.error ?? 'the table was refused') }
    return { ok: true, error: '' }
  } catch (e) {
    return { ok: false, error: e instanceof Error ? e.message : 'the table was refused' }
  }
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

/** `null` means the note could not be read (offline, no agent, or a refused
    call). An empty string is a real answer: that agent has no note yet. The two
    used to be the same value, so a failed read looked like an empty note and
    saving over it destroyed the stored text. */
export async function getAgentMemory(agentType = '', scope = 'project'): Promise<string | null> {
  if (!gateway.connected || !agentType) return null
  try {
    const res = await gateway.request<{ success?: boolean; content?: string; memory?: string }>('agent-memory.get', {
      agent_type: agentType,
      scope
    })
    if (res?.success === false) return null
    return String(res?.content ?? res?.memory ?? '')
  } catch { return null }
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

/** Shared toast host. Components call this instead of reaching for the window
    global, so every surface reports through the same stack. */
export function toast(title: string, message: string, kind: 'ok' | 'warn' | 'err' = 'ok'): void {
  notify(title, message, kind)
}

/** Hands an address to the OS default browser. The gateway validates the
    protocol again; this check only keeps a non-address off the wire.

    A click that does nothing reads as a broken link, so every failure path says
    which one it was: offline, slow, or a refusal from the gateway. */
export async function openExternalUrl(raw: string): Promise<void> {
  const url = String(raw || '').trim()
  if (!/^https?:\/\//i.test(url)) {
    toast('Link not opened', 'Only http and https links can be opened.', 'warn')
    return
  }
  try {
    await gateway.request('system.open_url', { url })
  } catch (error) {
    const message = error instanceof Error ? error.message : ''
    toast(
      'Link not opened',
      message === 'Not connected'
        ? 'The gateway is offline, so the link was not opened.'
        : message.startsWith('Timeout:')
          ? 'The gateway did not answer in time.'
          : message || 'The gateway refused the link.',
      'warn'
    )
  }
}

export { escapeHtml }
