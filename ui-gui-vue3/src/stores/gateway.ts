import { defineStore } from 'pinia'
import { ref } from 'vue'
import { getGateway, type GatewayEvent } from '../services/gatewayClient'
import { playCompletionSound, primeCompletionSound } from '../services/soundNotify'
import type { Message, ToolCall, ToolStatus, FileChangeItem, ExecutionOrderItem } from '../types/agentChat'
import { useConfigStore } from './config'
import { useCommentStore } from './comment'
import {
  getCachedMessages, setCachedMessages, removeCachedMessages,
  getLastSessionId, setLastSessionId,
} from './gateway/storage'
import {
  convertBackendMessages, mergeCachedAttachments, emptyStream, nextId,
  type BackendMessage, type StreamingState,
} from './gateway/messageNormalize'

export interface UsageData { sessionCostCny: number; latestInputTokens: number; latestOutputTokens: number; modelContextLimit: number; period?: string | null; callHistory?: Array<{ model: string; inputTokens: number; outputTokens: number; cacheHitTokens: number; costCny: number; period?: string | null }> }
export interface AttachmentItem { name: string; type: string; size: number; text_content?: string; data_base64?: string; detail?: string; isImage?: boolean }


export const useGatewayStore = defineStore('gateway', () => {
  const gateway = getGateway()
  const connected = ref(false)
  const sessionId = ref<string | null>(null)
  const messages = ref<Message[]>([])
  const streamingMessage = ref<Message | null>(null)
  const streaming = ref(false)
  // Live generation rate (tokens/s). Derived only from the cumulative token
  // count carried by streaming deltas; independent of usage/cost accounting.
  const streamRate = ref(0)
  const rateSamples: Array<{ t: number; v: number }> = []
  let rateTimer: number | null = null
  let rateLastAt = 0
  let rateTokens = 0
  function ratePush(tokens: number) {
    if (!tokens) return
    const now = performance.now()
    if (tokens < rateTokens) rateSamples.length = 0
    rateTokens = tokens; rateLastAt = now
    rateSamples.push({ t: now, v: tokens })
    const cut = now - 2500
    while (rateSamples.length && rateSamples[0].t < cut) rateSamples.shift()
    if (rateTimer === null) rateTimer = window.setInterval(rateTick, 500)
  }
  function rateTick() {
    if (!streaming.value) { rateStop(); return }
    const now = performance.now()
    if (now - rateLastAt > 1500 || rateSamples.length < 2) { streamRate.value = 0; return }
    const a = rateSamples[0]; const b = rateSamples[rateSamples.length - 1]
    const dt = (b.t - a.t) / 1000
    streamRate.value = dt > 0.05 ? Math.max(0, (b.v - a.v) / dt) : 0
  }
  function rateStop() {
    if (rateTimer !== null) { window.clearInterval(rateTimer); rateTimer = null }
    rateSamples.length = 0; rateLastAt = 0; rateTokens = 0; streamRate.value = 0
  }
  const usage = ref<UsageData>({ sessionCostCny: 0, latestInputTokens: 0, latestOutputTokens: 0, modelContextLimit: 0, callHistory: [] })
  const fileChanges = ref<FileChangeItem[]>([])
  const sessionIdRef = ref<string | null>(null)
  const agentMsgIdRef = ref<string | null>(null)
  const streamRef = ref<StreamingState>(emptyStream())
  const abortFlushingRef = ref(false)
  const initialLoadDone = ref(false)
  const pendingClarify = ref<{ requestId: string; question: string } | null>(null)
  const configStore = useConfigStore()
  let flushTimeoutRef: number | null = null
  let _lastFlushAt = 0
  let _lastPersist = 0
  let _streamLostOnDisconnect = false  // 断线时流式输出被中断 → 重连后从后端数据库恢复显示

  function persistMessages(msgs: Message[]) { const sid = sessionIdRef.value; if (sid) setCachedMessages(sid, msgs) }

  function persistStreamingState(sid: string, content: string, reasoning: string, toolCalls: ToolCall[], order: ExecutionOrderItem[], id: string) {
    const now = Date.now(); if (now - _lastPersist < 2000) return; _lastPersist = now
    const prev = messages.value; const existingIdx = prev.findIndex(m => m.id === id)
    const sm: Message = { id, role: 'agent', content, reasoning: reasoning || undefined, toolCalls: [...toolCalls], executionOrder: order.length > 0 ? [...order] : undefined, questionCard: streamingMessage.value?.id === id ? streamingMessage.value.questionCard : undefined, createdAt: Date.now() }
    const merged = existingIdx >= 0 ? [...prev.slice(0, existingIdx), sm, ...prev.slice(existingIdx + 1)] : [...prev, sm]
    setCachedMessages(sid, merged)
  }

  function flushStreamState() {
    flushTimeoutRef = null; _lastFlushAt = performance.now(); const id = agentMsgIdRef.value; if (!id) return; if (abortFlushingRef.value) return
    const s = streamRef.value
    if (!streamingMessage.value || streamingMessage.value.id !== id) { streamingMessage.value = { id, role: 'agent', content: s.content, reasoning: s.reasoning || undefined, toolCalls: [...s.toolCalls], executionOrder: s.order.length > 0 ? [...s.order] : undefined, createdAt: Date.now() } }
    else { streamingMessage.value = { ...streamingMessage.value, content: s.content, reasoning: s.reasoning || undefined, toolCalls: [...s.toolCalls], executionOrder: s.order.length > 0 ? [...s.order] : undefined } }
    const sid = sessionIdRef.value; if (sid) persistStreamingState(sid, s.content, s.reasoning, s.toolCalls, s.order, id)
  }

  // 流式刷新节流：最短间隔 120ms（约 8fps）——降低流式期间的每帧重渲染频率（渲染性能修复 ①）
  function scheduleStreamFlush() { if (flushTimeoutRef) return; const wait = Math.max(0, 120 - (performance.now() - _lastFlushAt)); flushTimeoutRef = window.setTimeout(flushStreamState, wait) }

  async function resumeSession(sid: string) {
    try {
      const result = await gateway.request<{ session_id: string; messages: BackendMessage[]; message_count: number; cost_summary?: { total_cost_cny: number; total_calls: number; total_input_tokens: number; total_output_tokens: number; total_cache_hit_tokens: number }; info?: Record<string, unknown> }>('session.resume', { session_id: sid })
      const converted = convertBackendMessages(result.messages || [])
      const cached = getCachedMessages(sid) || []
      // 后端为权威来源；合并 localStorage 缓存中的图片附件（方案 A），后端无数据时整段用缓存兜底
      let finalMsgs: Message[]
      if (converted.length > 0) {
        finalMsgs = mergeCachedAttachments(converted, cached)
      } else if (cached.length > 0) {
        finalMsgs = cached
      } else {
        finalMsgs = []
      }
      streamingMessage.value = null; messages.value = finalMsgs; sessionId.value = sid; sessionIdRef.value = sid
      setLastSessionId(sid); persistMessages(finalMsgs)
      gateway.request<{ files: Array<{ relative_path: string; status: string; added_lines: number; removed_lines: number; active_count?: number }> }>('session.file_changes', { session_id: sid, project_path: configStore.projectPath }).then(fc => { if (fc?.files?.length) { fileChanges.value = fc.files.map(f => ({ id: `fc_restored_${f.relative_path.replace(/[\\/.]/g, '_')}`, relativePath: f.relative_path, operationType: (f.status === 'created' ? 'create' : f.status === 'deleted' ? 'delete' : 'modify') as 'create' | 'modify' | 'delete', toolName: '', toolId: '', addedLines: f.added_lines, removedLines: f.removed_lines, status: f.status, hunks: undefined, expanded: false, reverted: false, loadingDiff: false, activeCount: f.active_count ?? 1 })) } else { fileChanges.value = [] } }).catch(() => { fileChanges.value = [] })
      const cs = result.cost_summary; usage.value = { sessionCostCny: cs ? cs.total_cost_cny : 0, latestInputTokens: 0, latestOutputTokens: 0, modelContextLimit: 0, callHistory: [] }
      window.dispatchEvent(new CustomEvent('gateway:session-loaded', { detail: { sessionId: sid, title: result.info?.title || '', messageCount: result.message_count || finalMsgs.length } }))
    } catch {
      const cached = getCachedMessages(sid); if (cached && cached.length > 0) { streamingMessage.value = null; messages.value = cached; fileChanges.value = []; sessionId.value = sid; sessionIdRef.value = sid }
      usage.value = { sessionCostCny: 0, latestInputTokens: 0, latestOutputTokens: 0, modelContextLimit: 0, callHistory: [] }
    }
  }

  async function createSessionInternal(): Promise<string> {
    try { let pp = ''; try { const cfg = useConfigStore(); pp = cfg.activeProjectPath || cfg.projectPath || '' } catch { /* pinia not ready */ }
    const result = await gateway.request<{ session_id: string; info?: Record<string, unknown> }>('session.create', pp ? { project_path: pp } : {}); const sid = result.session_id; sessionId.value = sid; sessionIdRef.value = sid; setLastSessionId(sid); streamingMessage.value = null; messages.value = []; fileChanges.value = []; removeCachedMessages(sid); usage.value = { sessionCostCny: 0, latestInputTokens: 0, latestOutputTokens: 0, modelContextLimit: 0, callHistory: [] }; window.dispatchEvent(new CustomEvent('gateway:session-created', { detail: { sessionId: sid, title: 'New conversation' } })); return sid } catch { return 'default' }
  }

  async function loadInitialSession() {
    if (initialLoadDone.value) return; initialLoadDone.value = true
    try { const cachedSid = getLastSessionId(); if (cachedSid) { await resumeSession(cachedSid); return }; const recent = await gateway.request<{ session_id?: string }>('session.most_recent'); if (recent?.session_id) { await resumeSession(recent.session_id) } else { await createSessionInternal() } } catch { /* silent */ }
  }

  function handleEvent(ev: GatewayEvent) {
    const { type, payload } = ev
    switch (type) {
      case 'message.delta': { const t = (payload.text as string) || ''; ratePush((payload.tokens as number) || 0); streamRef.value.content += t; const id = agentMsgIdRef.value; if (id) { const o = streamRef.value.order; const l = o[o.length - 1]; if (l && l.type === 'text') { l.content += t } else { if (l && l.type === 'reasoning' && l.startTime) l.durationMs = Date.now() - l.startTime; o.push({ id: `text_${++streamRef.value._orderSeq}`, type: 'text', content: t, startTime: Date.now() }) }; scheduleStreamFlush() } break }
      case 'thinking.delta': { const t = (payload.text as string) || ''; ratePush((payload.tokens as number) || 0); streamRef.value.reasoning += t; const id = agentMsgIdRef.value; if (id) { const o = streamRef.value.order; const l = o[o.length - 1]; if (l && l.type === 'reasoning') { l.content += t } else { if (l && l.type === 'text' && l.startTime) l.durationMs = Date.now() - l.startTime; o.push({ id: `reasoning_${++streamRef.value._orderSeq}`, type: 'reasoning', content: t, startTime: Date.now() }) }; scheduleStreamFlush() } break }
      case 'tool.call_streaming': { const tid = (payload.tool_id as string) || ''; const n = (payload.name as string) || 'tool'; const at = (payload.args_text as string) || ''; if (!tid) break; ratePush((payload.tokens as number) || 0); const ex = streamRef.value.toolCalls.find(tc => tc._toolId === tid); if (ex) { ex.input = at } else { const o = streamRef.value.order; const l = o[o.length - 1]; if (l && l.type === 'reasoning' && l.startTime) l.durationMs = Date.now() - l.startTime; const tc: ToolCall = { _toolId: tid, status: 'streaming', label: n, duration: 0, input: at }; streamRef.value.currentTool = tc; streamRef.value.toolCalls.push(tc); streamRef.value.order.push({ id: `tool_${++streamRef.value._orderSeq}_${tid}`, type: 'tool', toolInfo: tc }) }; if (agentMsgIdRef.value) scheduleStreamFlush(); break }
      case 'tool.start': { const tid = (payload.tool_id as string) || ''; const n = (payload.name as string) || 'tool'; const at = (payload.args_text as string) || ''; const ex = tid ? streamRef.value.toolCalls.find(tc => tc._toolId === tid) : undefined; if (ex) { ex.status = 'running'; ex.label = n; if (at) ex.input = at } else { const tc: ToolCall = { _toolId: tid || `tool_${Date.now()}`, status: 'running', label: n, duration: 0, input: at }; streamRef.value.currentTool = tc; streamRef.value.toolCalls.push(tc); streamRef.value.order.push({ id: `tool_${++streamRef.value._orderSeq}_${tid}`, type: 'tool', toolInfo: tc }) }; if (agentMsgIdRef.value) scheduleStreamFlush(); break }
      case 'tool.complete': { const tid = (payload.tool_id as string) || ''; const tc = tid ? streamRef.value.toolCalls.find(t => t._toolId === tid) : streamRef.value.currentTool; if (tc) { const ds = (payload.duration_s as number) || 0; tc.status = payload.error ? 'failed' : 'completed'; tc.duration = Math.round(ds * 1000); tc.output = (payload.result_text as string) || undefined; tc.error = (payload.error as string) || undefined; if (!tid) streamRef.value.currentTool = null; const o = streamRef.value.order; for (let i = o.length - 1; i >= 0; i--) { if (o[i].type === 'tool' && o[i].toolInfo === tc) { o[i].toolInfo = { ...tc }; break } } }; if (agentMsgIdRef.value) scheduleStreamFlush(); break }
      case 'file.change': { const fp = payload as Record<string, unknown>; const rp = (fp.relative_path as string) || ''; if (rp) { const ni: FileChangeItem = { id: `fc_${Date.now()}_${Math.random().toString(36).slice(2, 6)}`, relativePath: rp, operationType: (fp.operation_type as FileChangeItem['operationType']) || 'modify', toolName: (fp.tool_name as string) || '', toolId: (fp.tool_id as string) || '', expanded: false, reverted: false, loadingDiff: false, activeCount: typeof fp.active_count === 'number' && fp.active_count > 0 ? fp.active_count : 1 }; fileChanges.value = [...fileChanges.value.filter(f => f.relativePath !== rp || f.reverted), ni] }; window.dispatchEvent(new CustomEvent('gateway:file-change', { detail: { path: rp } })); break }
      case 'message.complete': {
        const id = agentMsgIdRef.value; const interrupted = (payload.interrupted as boolean) || false
        if (id) {
          const fc = (payload.text as string) || streamRef.value.content
          const fr = (payload.reasoning as string) || streamRef.value.reasoning || undefined
          const ft = [...streamRef.value.toolCalls]
          const li = streamRef.value.order[streamRef.value.order.length - 1]
          if (li && li.type === 'reasoning' && li.startTime && !li.durationMs) li.durationMs = Date.now() - li.startTime
          const fo = [...streamRef.value.order]
          const mi = (payload.message_id as number) || undefined
          let tcs = ft
          if (interrupted && tcs.length > 0) { tcs = tcs.map(tc => tc.status === 'running' || tc.status === 'streaming' ? { ...tc, status: 'cancelled' as ToolStatus } : tc) }
          let eo = fo
          if (interrupted && tcs.length > 0) { const tm = new Map(tcs.filter(tc => tc._toolId).map(tc => [tc._toolId!, tc])); eo = fo.map(item => { if (item.type === 'tool' && item.toolInfo?._toolId && tm.has(item.toolInfo._toolId)) { return { ...item, toolInfo: tm.get(item.toolInfo._toolId)! } } return item }) }
          const fm: Message = { id, role: 'agent', content: fc, reasoning: fr, messageId: mi, toolCalls: tcs, executionOrder: eo.length > 0 ? eo : undefined, interrupted, createdAt: Date.now() }
          streamingMessage.value = null
          const ei = messages.value.findIndex(m => m.id === id)
          if (ei >= 0) { messages.value.splice(ei, 1, fm) } else { messages.value.push(fm) }
          persistMessages(messages.value)
        }
        if (!interrupted) playCompletionSound()
        rateStop()
        streaming.value = false; agentMsgIdRef.value = null; streamRef.value = emptyStream()
        window.dispatchEvent(new CustomEvent('gateway:message-complete', { detail: { sessionId: (payload.session_id as string) || sessionIdRef.value || '' } }))
        break
      }
      case 'prompt.rejected': { const reason = (payload.reason as string) || 'payload rejected'; messages.value.push({ id: nextId(), role: 'agent', content: `**Error:** ${reason}` }); break }
      case 'error': { const em = (payload.message as string) || 'Unknown error'; const id = agentMsgIdRef.value; if (id) { if (streamingMessage.value && streamingMessage.value.id === id) { streamingMessage.value = { ...streamingMessage.value, content: streamingMessage.value.content + `\n\n**Error:** ${em}` } } else { streamingMessage.value = { id, role: 'agent', content: `**Error:** ${em}`, createdAt: Date.now() } } } else { messages.value.push({ id: nextId(), role: 'agent', content: `**Error:** ${em}` }) }; streaming.value = false; agentMsgIdRef.value = null; streamRef.value = emptyStream(); break }
      case 'todos.update': { window.dispatchEvent(new CustomEvent('gateway-todos', { detail: payload })); break }
      case 'clarify.request': { const ri = (payload.request_id as string) || ''; const q = (payload.question as string) || ''; const ch = (payload.choices as string[]) || []; // console.log('[gateway] clarify.request q=',q.slice(0,50)) // 调试：如有问题请开启
      // 多问题支持：优先消费完整 questions 数组；缺失时回退单问题字段（旧后端兼容）
      const qsRaw = (payload.questions as Array<{ prompt?: string; title?: string; options?: Array<string | { label?: string; detail?: string }> }> | undefined) || [];
      const qs = qsRaw.length > 0
        ? qsRaw.map(x => ({ title: String(x.title || 'QUESTION'), prompt: String(x.prompt || ''), options: (x.options || []).map(c => typeof c === 'string' ? { label: c, detail: '' } : { label: String(c.label || ''), detail: String(c.detail || '') }) }))
        : [{ title: 'QUESTION', prompt: q, options: ch.map(c => ({ label: c, detail: '' })) }];
      const smId = agentMsgIdRef.value;
      if (smId && streamingMessage.value) {
        // 内联渲染：提问元素插入 order 尾部（此刻 order 尾部恰是 clarify 工具卡），
        // 卡片停驻该位置，后续文本/工具追加在其下方，不再钉在整条消息末尾
        const o = streamRef.value.order;
        o.push({ id: `q_${ri}`, type: 'question', questionId: ri, questions: qs });
        scheduleStreamFlush();
      } else {
        // 兜底：无流式消息时作为独立 agent 消息（executionOrder 内联，content 置空避免问题文本重复显示）
        messages.value.push({ id: nextId(), role: 'agent', content: '', executionOrder: [{ id: `q_${ri}`, type: 'question', questionId: ri, questions: qs }] });
      }
      pendingClarify.value = { requestId: ri, question: q || qs[0]?.prompt || '' }; // console.log('[gateway] pendingClarify SET') // 调试
      playCompletionSound() // AI 提问提醒：与回复完成提示音共用音效设置（off 时静默）
      break }
      case 'permission.request': { window.dispatchEvent(new CustomEvent('gateway:permission-request', { detail: { payload } })); break }
      case 'message.usage': { const cc = (payload.session_cost_cny as number) || 0; const it = (payload.latest_input_tokens as number) || 0; const ot = (payload.latest_output_tokens as number) || 0; const cl = (payload.model_context_limit as number) || 0; const pp = (payload.period as string) || null; const tc = payload.this_call as Record<string, unknown> | undefined; const ce = tc ? { model: (tc.model as string) || (payload.model as string) || 'unknown', inputTokens: (tc.input_tokens as number) || it, outputTokens: (tc.output_tokens as number) || ot, cacheHitTokens: (tc.cache_hit_tokens as number) || 0, costCny: (tc.total_cost as number) || (tc.cost_cny as number) || 0, period: (tc.period as string) || pp } : undefined; usage.value = { sessionCostCny: cc, latestInputTokens: it, latestOutputTokens: ot, modelContextLimit: cl, period: pp, callHistory: ce ? [...(usage.value.callHistory || []), ce] : usage.value.callHistory }; break }
      case 'user_message.saved': { const si = (payload.message_id as number) || 0; if (si) { for (let i = messages.value.length - 1; i >= 0; i--) { if (messages.value[i].role === 'user' && !messages.value[i].messageId) { messages.value[i] = { ...messages.value[i], messageId: si }; const sid = sessionIdRef.value; if (sid) persistMessages(messages.value); break } } }; const ssid = (payload.session_id as string) || sessionIdRef.value || ''; if (ssid) window.dispatchEvent(new CustomEvent('gateway:user-message-saved', { detail: { sessionId: ssid } })); break }
      case 'session.updated': { const title = payload.title as string; const sid = (payload.session_id as string) || ''; if (title && sid) window.dispatchEvent(new CustomEvent('gateway:session-updated', { detail: { sessionId: sid, title } })); break }
      case 'mcp.state_changed': { window.dispatchEvent(new CustomEvent('gateway:mcp-state', { detail: { payload } })); break }
      case 'gateway.ready': { break }
    }
  }

  function sendMessage(text: string, attachments?: AttachmentItem[]) {
    if (!text.trim() && (!attachments || attachments.length === 0)) return; if (streaming.value) return
    abortFlushingRef.value = false; pendingClarify.value = null; const sid = sessionIdRef.value || 'default'
    const isImageExt = (n: string) => /\.(png|jpe?g|gif|webp|bmp|svg)$/i.test(n)
    const ams = attachments?.map(a => ({
      name: a.name, size: a.size,
      icon: a.name.split('.').pop()?.toUpperCase().slice(0,2) || 'FI',
      dataUrl: a.data_base64 ? `data:${isImageExt(a.name)?'image/'+a.name.split('.').pop()?.toLowerCase().replace('jpg','jpeg'):'application/octet-stream'};base64,${a.data_base64}` : undefined,
      isImage: isImageExt(a.name)
    }))
    const um: Message = { id: nextId(), role: 'user', content: text || '', createdAt: Date.now(), attachments: ams }
    const am: Message = { id: nextId(), role: 'agent', content: '', createdAt: Date.now() }
    primeCompletionSound(); agentMsgIdRef.value = am.id; streamRef.value = emptyStream(); streaming.value = true
    messages.value.push(um); setCachedMessages(sid, messages.value); streamingMessage.value = am
    const ats = attachments?.map(a => ({ name: a.name, type: a.type, size: a.size, text_content: a.text_content, data_base64: a.data_base64, detail: a.detail }))
    if (!gateway.sendMessage(sid, text, ats, configStore.projectPath)) failPendingStream('Not connected')
  }

  // 连接断开 / 消息未能发出：把未完成的流标记为错误并复位状态，避免界面永久停留在“回复中”
  function failPendingStream(msg: string) {
    const id = agentMsgIdRef.value
    if (id) {
      if (streamingMessage.value && streamingMessage.value.id === id) { streamingMessage.value = { ...streamingMessage.value, content: (streamingMessage.value.content ? streamingMessage.value.content + '\n\n' : '') + `**Error:** ${msg}` } }
      else { streamingMessage.value = { id, role: 'agent', content: `**Error:** ${msg}`, createdAt: Date.now() } }
    } else { messages.value.push({ id: nextId(), role: 'agent', content: `**Error:** ${msg}` }) }
    rateStop()
    streaming.value = false; agentMsgIdRef.value = null; streamRef.value = emptyStream()
  }

  // 断线期间流式输出被中断 → 重连后从后端数据库恢复显示（后端"先保存后发送"，断线只丢显示不丢数据）。
  // 若会话尾部仍是用户消息（后端任务可能仍在执行中），短轮询等待结果落库（最多 12 次 × 10s）。
  async function recoverAfterReconnect() {
    const sid = sessionIdRef.value
    if (!sid || streaming.value) return
    await resumeSession(sid)
    for (let i = 0; i < 12; i++) {
      if (streaming.value) return
      const last = messages.value[messages.value.length - 1]
      if (!last || last.role !== 'user') return
      await new Promise(r => setTimeout(r, 10000))
      if (streaming.value) return
      await resumeSession(sid)
    }
  }

  function stopGeneration() { if (!streaming.value) return; const sid = sessionIdRef.value; abortFlushingRef.value = true; gateway.request('session.interrupt', { session_id: sid || '' }).catch(() => {}); streaming.value = false }

  function clearMessages() { const sid = sessionIdRef.value; if (sid) removeCachedMessages(sid); streamingMessage.value = null; messages.value = []; streamRef.value = emptyStream(); agentMsgIdRef.value = null; streaming.value = false }

  let _done = false
  function setupConnection() {
    if (_done) return; _done = true
    gateway.onEvent(handleEvent)
    gateway.onStatus(isC => { connected.value = isC; if (isC) { window.dispatchEvent(new Event('gateway:connected')); loadInitialSession().catch(console.error); configStore.loadProvidersFromBackend().catch(console.error); if (_streamLostOnDisconnect) { _streamLostOnDisconnect = false; setTimeout(() => { recoverAfterReconnect().catch(console.error) }, 800) } } else if (streaming.value) { _streamLostOnDisconnect = true; failPendingStream('Connection lost') } })
  // clarify 卡片状态回写：提交/跳过瞬间把卡片 state 置为 answered/skipped。
  // 卡片载体有两种：executionOrder 内联 question 元素（新路径）+ 消息级 questionCard（旧数据兼容）。
  function markQuestionState(id: string, state: 'answered' | 'skipped') {
    const mark = (m?: Message | null) => {
      if (!m) return
      if (m.executionOrder) {
        let changed = false
        const eo = m.executionOrder.map(it => { if (it.type === 'question' && it.questionId === id) { changed = true; return { ...it, state } } return it })
        if (changed) m.executionOrder = eo
      }
      if (m.questionCard && m.questionCard.id === id) m.questionCard = { ...m.questionCard, state }
    }
    mark(streamingMessage.value)
    for (const m of messages.value) mark(m)
  }
    window.addEventListener('ask-question-submit', ((e: CustomEvent) => { const d = e.detail as { id?: string; answers?: Record<string, string> } | undefined; if (!d?.id || !d?.answers) return; let answers = { ...d.answers }; let at = Object.values(answers).filter(Boolean).join('; '); const cs = useCommentStore(); const cms = cs.getComments(); if (cms.length > 0 && (at.includes('修改') || at.includes('Modify'))) { const lines = cms.map(c => `- Location: ${c.selector}\n  Element: <${c.tag}>\n  Original: "${c.textSnippet}"\n  Comment: ${c.body}`); const merged = `Modify\n\n[HTML Page Feedback] The user annotated ${cms.length} edits on the page:\n${lines.join('\n\n')}`; const k = Object.keys(answers).find(k => (answers[k] || '').includes('修改') || (answers[k] || '').includes('Modify')) || Object.keys(answers)[0]; if (k) answers = { ...answers, [k]: merged }; at = Object.values(answers).filter(Boolean).join('; '); cs.clearComments() }; markQuestionState(d.id, 'answered'); pendingClarify.value = null; gateway.request('clarify.respond', { answer: at, request_id: d.id, answers }).catch(() => {}) }) as EventListener)
    window.addEventListener('ask-question-skip', ((e: CustomEvent) => { const d = e.detail as { id?: string } | undefined; if (!d?.id) return; markQuestionState(d.id, 'skipped'); pendingClarify.value = null; gateway.request('clarify.respond', { answer: '[SKIPPED]', request_id: d.id }).catch(() => {}) }) as EventListener)
    window.addEventListener('gateway:resume-session', ((e: CustomEvent) => { const d = e.detail as { sessionId?: string } | undefined; if (d?.sessionId) resumeSession(d.sessionId) }) as EventListener)
    window.addEventListener('gateway:create-session', (() => { createSessionInternal().catch(console.error) }) as EventListener)
    window.addEventListener('gateway:delete-message', ((e: CustomEvent) => { const d = e.detail as { messageId?: number; localId?: string } | undefined; if (!d) return; const sid = sessionIdRef.value; if (d.messageId) { messages.value = messages.value.filter(m => m.messageId !== d.messageId) } else if (d.localId) { messages.value = messages.value.filter(m => m.id !== d.localId) }; if (sid) persistMessages(messages.value) }) as EventListener)
    gateway.connect()
  }

  return { connected, sessionId, messages, streamingMessage, streaming, usage, fileChanges, sessionIdRef, pendingClarify, sendMessage, stopGeneration, clearMessages, createSession: async () => { await createSessionInternal() }, resumeSession, request: <T>(m: string, p?: Record<string, unknown>) => gateway.request<T>(m, p), updateUsage: (patch: Partial<UsageData>) => { usage.value = { ...usage.value, ...patch } }, fetchPricing: async () => { try { const r = await gateway.request<{ pricing?: unknown; period?: string | null; cn_holidays?: { holidays?: string[]; workdays?: string[]; years_covered?: number[]; updated_at?: string | null } | null }>('config.get', { key: 'pricing' }); if (r && typeof r === 'object' && (r as Record<string, unknown>).period) { usage.value = { ...usage.value, period: (r as Record<string, unknown>).period as string } } return r ?? null } catch { return null } }, handleEvent, loadInitialSession, setupConnection, gateway, streamRate }
})
