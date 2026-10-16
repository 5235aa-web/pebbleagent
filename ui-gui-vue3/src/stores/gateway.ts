import { defineStore } from 'pinia'
import { ref } from 'vue'
import { getGateway, type GatewayEvent } from '../services/gatewayClient'
import { playCompletionSound, primeCompletionSound } from '../services/soundNotify'
import type { Message, ToolCall, ToolStatus, FileChangeItem, ExecutionOrderItem } from '../types/agentChat'
import { useConfigStore } from './config'
import { useCommentStore } from './comment'

const LS_PREFIX = 'pebble:'
function lsKey(s: string): string { return `${LS_PREFIX}${s}` }
function getCachedMessages(sid: string): Message[] | null { try { const r = localStorage.getItem(lsKey(`messages:${sid}`)); return r ? JSON.parse(r) : null } catch { return null } }
function setCachedMessages(sid: string, msgs: Message[]) { try { localStorage.setItem(lsKey(`messages:${sid}`), JSON.stringify(msgs)) } catch {} }
function removeCachedMessages(sid: string) { try { localStorage.removeItem(lsKey(`messages:${sid}`)) } catch {} }
function getLastSessionId(): string | null { try { return localStorage.getItem(lsKey('last-session')) } catch { return null } }
function setLastSessionId(sid: string) { try { localStorage.setItem(lsKey('last-session'), sid) } catch {} }

interface BackendMessage { role: 'user' | 'assistant' | 'tool' | 'system'; text?: string; content?: string; reasoning?: string; name?: string; context?: string; tool_calls?: string; created_at?: number; message_id?: number; interrupted?: boolean; is_compact_summary?: boolean; is_compacted?: boolean }

function normalizeExecutionOrder(raw: unknown): ExecutionOrderItem[] | null {
  if (!Array.isArray(raw)) return null
  const items: ExecutionOrderItem[] = []
  // 跟踪已处理的 tool id，同一工具只保留最后一条（completed 覆盖 running）
  const seenTools = new Set<string>()
  for (const r of raw) {
    if (!r || typeof r !== 'object') continue
    // 检测后端 tool_execution 事件格式：{type:"tool_execution", tool_name, status, ...}
    if ((r as any).type === 'tool_execution') {
      const t = r as any
      const tid = String(t.tool_execution_id || t.tool_call_id || '')
      if (seenTools.has(tid)) {
        // 替换之前的同 id 记录（保留最新状态）
        const idx = items.findIndex(item => item.type === 'tool' && item.toolInfo?._toolId === tid)
        if (idx >= 0) {
          items[idx] = {
            id: tid, type: 'tool',
            toolInfo: {
              _toolId: tid,
              status: (String(t.status || 'completed')) as ToolStatus,
              label: String(t.tool_name || 'tool'),
              input: String(t.input || ''),
              output: t.output ? String(t.output) : undefined,
              error: t.error ? String(t.error) : undefined,
              duration: Number(t.duration || 0)
            }
          }
        }
        continue
      }
      seenTools.add(tid)
      items.push({
        id: tid, type: 'tool',
        toolInfo: {
          _toolId: tid,
          status: (String(t.status || 'completed')) as ToolStatus,
          label: String(t.tool_name || 'tool'),
          input: String(t.input || ''),
          output: t.output ? String(t.output) : undefined,
          error: t.error ? String(t.error) : undefined,
          duration: Number(t.duration || 0)
        }
      })
      continue
    }
    // 检测原始工具记录（无 type 字段）
    const isRawTool = !(r as any).type && ((r as any).name || (r as any).tool_name) && (r as any).status
    if (isRawTool) {
      const t = r as any
      const tid = String(t.id || t.tool_call_id || '')
      items.push({
        id: tid, type: 'tool',
        toolInfo: {
          _toolId: tid,
          status: (String(t.status || 'completed')) as ToolStatus,
          label: String(t.name || t.tool_name || 'tool'),
          input: String(t.input || ''),
          output: t.output ? String(t.output) : undefined,
          error: t.error ? String(t.error) : undefined,
          duration: Number(t.duration || 0)
        }
      })
      continue
    }
    const item: ExecutionOrderItem = { id: String((r as any).id || (r as any).order || items.length), type: (r as any).type as ExecutionOrderItem['type'] }
    if ((r as any).content) item.content = String((r as any).content)
    if ((r as any).startTime) item.startTime = Number((r as any).startTime)
    if ((r as any).durationMs) item.durationMs = Number((r as any).durationMs)
    const ti = (r as any).tool_info || (r as any).toolInfo
    if (ti && typeof ti === 'object') {
      const t = ti as any
      item.toolInfo = { _toolId: String(t.id || t._toolId || ''), status: (String(t.status || 'completed')) as ToolStatus, label: String(t.name || t.label || 'tool'), input: String(t.input || ''), output: t.output ? String(t.output) : undefined, error: t.error ? String(t.error) : undefined, duration: Number(t.duration || 0) }
      if (t.files && Array.isArray(t.files)) item.toolInfo.files = t.files as string[]
    }
    items.push(item)
  }
  return items.length > 0 ? items : null
}

function extractCompactSummary(content: string): string | null {
  // 剥离引导前缀文本，提取原始摘要内容
  let s = content
  // 移除头部引导段落
  s = s.replace(/^This session is being continued from a previous conversation that ran out of context\. The summary below covers the earlier portion of the conversation\.\s*/s, '')
  // 移除 transcript 引用行
  s = s.replace(/\n\nIf you need specific details from before compaction \(like exact code snippets, error messages, or content you generated\), read the full transcript at:.*/s, '')
  // 移除末尾 suppress 模式指令
  s = s.replace(/\n\nRecent messages are preserved verbatim\./s, '')
  s = s.replace(/\nContinue the conversation from where it left off without asking the user any further questions.*/s, '')
  s = s.trim()
  return s || null
}

function convertBackendMessages(raw: BackendMessage[]): Message[] {
  const result: Message[] = []
  const pendingTools: Array<{ name: string; input: string }> = []
  for (let i = 0; i < raw.length; i++) {
    const bm = raw[i]
    if (bm.role === 'system') {
      // 压缩边界标记 → 折叠分割线锚点（渲染层按 isCompacted 分段折叠/展开）
      const sc = bm.text || bm.content || ''
      if (sc.startsWith('[COMPACT_BOUNDARY:')) {
        const m = sc.match(/\[COMPACT_BOUNDARY:([^\]]+)\]/)
        result.push({ id: `cb_${m ? m[1] : i}`, role: 'agent', content: '', isCompactBoundary: true, createdAt: bm.created_at ? bm.created_at * 1000 : undefined })
      }
      continue
    }
    if (bm.role === 'tool') { pendingTools.push({ name: bm.name || 'tool', input: bm.context || '' }); continue }
    if (bm.role === 'user') {
      const txt = bm.text || bm.content || ''
      // 优先按后端 is_compact_summary 字段识别摘要消息；旧文本前缀保留为向后兼容兜底
      if (bm.is_compact_summary === true || txt.startsWith('This session is being continued')) {
        const sc = extractCompactSummary(txt)
        if (sc) { result.push({ id: `hist_${i}`, messageId: bm.message_id, role: 'agent', content: '', summaryCard: sc, createdAt: bm.created_at ? bm.created_at * 1000 : undefined, isCompacted: bm.is_compacted || undefined }) }
        continue
      }
      result.push({ id: `hist_${i}`, messageId: bm.message_id, role: 'user', content: txt, createdAt: bm.created_at ? bm.created_at * 1000 : undefined, isCompacted: bm.is_compacted || undefined }); continue
    }
    if (bm.role === 'assistant') {
      const msg: Message = { id: `hist_${i}`, messageId: bm.message_id, role: 'agent', content: bm.text || bm.content || '', reasoning: bm.reasoning || undefined, createdAt: bm.created_at ? bm.created_at * 1000 : undefined, interrupted: bm.interrupted, isCompacted: bm.is_compacted || undefined }
      if (bm.tool_calls) {
        try { const raw2 = JSON.parse(bm.tool_calls); const execOrder = normalizeExecutionOrder(raw2); if (execOrder) { msg.executionOrder = execOrder; const tools = execOrder.filter(item => item.type === 'tool' && item.toolInfo).map(item => item.toolInfo!); if (tools.length > 0) msg.toolCalls = tools; if (!execOrder.some(item => item.type === 'text') && (msg.content || '').length > 0) { execOrder.push({ id: 'text_fallback', type: 'text', content: msg.content }) }; if (msg.reasoning && !execOrder.some(item => item.type === 'reasoning')) { execOrder.unshift({ id: 'reasoning_fallback', type: 'reasoning', content: msg.reasoning, startTime: msg.createdAt, durationMs: 0 }) }; const qToolIdx = execOrder.findIndex(item => item.type === 'tool' && item.toolInfo?.label === 'ask_user_question' && item.toolInfo?.output); if (qToolIdx >= 0) { try { const qo = JSON.parse(execOrder[qToolIdx].toolInfo!.output || ''); if (qo && Array.isArray(qo.questions) && qo.questions.length > 0) { execOrder.splice(qToolIdx + 1, 0, { id: `restored_q_${i}_${qToolIdx}`, type: 'question', questionId: `q_restored_${i}_${qToolIdx}`, questions: qo.questions.map((x: any) => ({ question: String(x.question || ''), header: String(x.header || 'QUESTION'), options: Array.isArray(x.options) ? x.options.map((o: any) => ({ label: String(o.label || ''), description: String(o.description || '') })) : [] })), state: qo.cancelled ? 'skipped' : 'answered' }); msg.executionOrder = [...execOrder] } } catch { /* 解析失败则不恢复提问卡片 */ } } pendingTools.length = 0 } } catch {}
      }
      if (!msg.executionOrder && pendingTools.length > 0) { msg.toolCalls = pendingTools.map(t => ({ status: 'completed' as ToolStatus, label: t.name, duration: 0, input: t.input })); pendingTools.length = 0 }
      result.push(msg)
    }
  }
  return result
}

interface StreamingState { content: string; reasoning: string; toolCalls: ToolCall[]; currentTool: ToolCall | null; order: ExecutionOrderItem[]; _orderSeq: number }
function emptyStream(): StreamingState { return { content: '', reasoning: '', toolCalls: [], currentTool: null, order: [], _orderSeq: 0 } }
// 后端消息为权威（无附件），localStorage 缓存里可能带图片附件（图片不进 DB、当次即焚）
// → 恢复时按 messageId 把缓存附件合并回后端 user 消息，保住图片显示（方案 A：前端合并缓存）
function mergeCachedAttachments(converted: Message[], cached: Message[]): Message[] {
  if (converted.length === 0 || cached.length === 0) return [...converted]
  const byId = new Map<number, Message>()
  for (const m of converted) { if (m.role === 'user' && m.messageId) byId.set(m.messageId, m) }
  let changed = false
  const out = [...converted]
  for (const cm of cached) {
    if (cm.role !== 'user' || !cm.messageId || !cm.attachments || cm.attachments.length === 0) continue
    const target = byId.get(cm.messageId)
    if (target && (!target.attachments || target.attachments.length === 0)) {
      const idx = out.indexOf(target)
      if (idx >= 0) { out[idx] = { ...target, attachments: cm.attachments }; changed = true }
    }
  }
  return changed ? out : converted
}

export interface UsageData { sessionCostCny: number; latestInputTokens: number; latestOutputTokens: number; modelContextLimit: number; period?: string | null; callHistory?: Array<{ model: string; inputTokens: number; outputTokens: number; cacheHitTokens: number; costCny: number; period?: string | null }> }
export interface AttachmentItem { name: string; type: string; size: number; text_content?: string; data_base64?: string; detail?: string; isImage?: boolean }

let _idCounter = 0
function nextId(): string { return `msg_${Date.now()}_${++_idCounter}` }

export const useGatewayStore = defineStore('gateway', () => {
  const gateway = getGateway()
  const connected = ref(false)
  const sessionId = ref<string | null>(null)
  const messages = ref<Message[]>([])
  const streamingMessage = ref<Message | null>(null)
  const streaming = ref(false)
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
      gateway.request<{ files: Array<{ relative_path: string; status: string; added_lines: number; removed_lines: number }> }>('session.file_changes', { session_id: sid, project_path: configStore.projectPath }).then(fc => { if (fc?.files?.length) { fileChanges.value = fc.files.map(f => ({ id: `fc_restored_${f.relative_path.replace(/[\\/.]/g, '_')}`, relativePath: f.relative_path, operationType: (f.status === 'created' ? 'create' : f.status === 'deleted' ? 'delete' : 'modify') as 'create' | 'modify' | 'delete', toolName: '', toolId: '', addedLines: f.added_lines, removedLines: f.removed_lines, status: f.status, hunks: undefined, expanded: false, reverted: false, loadingDiff: false })) } else { fileChanges.value = [] } }).catch(() => { fileChanges.value = [] })
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
      case 'message.delta': { const t = (payload.text as string) || ''; streamRef.value.content += t; const id = agentMsgIdRef.value; if (id) { const o = streamRef.value.order; const l = o[o.length - 1]; if (l && l.type === 'text') { l.content += t } else { if (l && l.type === 'reasoning' && l.startTime) l.durationMs = Date.now() - l.startTime; o.push({ id: `text_${++streamRef.value._orderSeq}`, type: 'text', content: t, startTime: Date.now() }) }; scheduleStreamFlush() } break }
      case 'thinking.delta': { const t = (payload.text as string) || ''; streamRef.value.reasoning += t; const id = agentMsgIdRef.value; if (id) { const o = streamRef.value.order; const l = o[o.length - 1]; if (l && l.type === 'reasoning') { l.content += t } else { if (l && l.type === 'text' && l.startTime) l.durationMs = Date.now() - l.startTime; o.push({ id: `reasoning_${++streamRef.value._orderSeq}`, type: 'reasoning', content: t, startTime: Date.now() }) }; scheduleStreamFlush() } break }
      case 'tool.call_streaming': { const tid = (payload.tool_id as string) || ''; const n = (payload.name as string) || 'tool'; const at = (payload.args_text as string) || ''; if (!tid) break; const ex = streamRef.value.toolCalls.find(tc => tc._toolId === tid); if (ex) { ex.input = at } else { const o = streamRef.value.order; const l = o[o.length - 1]; if (l && l.type === 'reasoning' && l.startTime) l.durationMs = Date.now() - l.startTime; const tc: ToolCall = { _toolId: tid, status: 'streaming', label: n, duration: 0, input: at }; streamRef.value.currentTool = tc; streamRef.value.toolCalls.push(tc); streamRef.value.order.push({ id: `tool_${++streamRef.value._orderSeq}_${tid}`, type: 'tool', toolInfo: tc }) }; if (agentMsgIdRef.value) scheduleStreamFlush(); break }
      case 'tool.start': { const tid = (payload.tool_id as string) || ''; const n = (payload.name as string) || 'tool'; const at = (payload.args_text as string) || ''; const ex = tid ? streamRef.value.toolCalls.find(tc => tc._toolId === tid) : undefined; if (ex) { ex.status = 'running'; ex.label = n; if (at) ex.input = at } else { const tc: ToolCall = { _toolId: tid || `tool_${Date.now()}`, status: 'running', label: n, duration: 0, input: at }; streamRef.value.currentTool = tc; streamRef.value.toolCalls.push(tc); streamRef.value.order.push({ id: `tool_${++streamRef.value._orderSeq}_${tid}`, type: 'tool', toolInfo: tc }) }; if (agentMsgIdRef.value) scheduleStreamFlush(); break }
      case 'tool.complete': { const tid = (payload.tool_id as string) || ''; const tc = tid ? streamRef.value.toolCalls.find(t => t._toolId === tid) : streamRef.value.currentTool; if (tc) { const ds = (payload.duration_s as number) || 0; tc.status = payload.error ? 'failed' : 'completed'; tc.duration = Math.round(ds * 1000); tc.output = (payload.result_text as string) || undefined; tc.error = (payload.error as string) || undefined; if (!tid) streamRef.value.currentTool = null; const o = streamRef.value.order; for (let i = o.length - 1; i >= 0; i--) { if (o[i].type === 'tool' && o[i].toolInfo === tc) { o[i].toolInfo = { ...tc }; break } } }; if (agentMsgIdRef.value) scheduleStreamFlush(); break }
      case 'file.change': { const fp = payload as Record<string, unknown>; const rp = (fp.relative_path as string) || ''; if (rp) { const ni: FileChangeItem = { id: `fc_${Date.now()}_${Math.random().toString(36).slice(2, 6)}`, relativePath: rp, operationType: (fp.operation_type as FileChangeItem['operationType']) || 'modify', toolName: (fp.tool_name as string) || '', toolId: (fp.tool_id as string) || '', expanded: false, reverted: false, loadingDiff: false }; fileChanges.value = [...fileChanges.value.filter(f => f.relativePath !== rp || f.reverted), ni] }; window.dispatchEvent(new CustomEvent('gateway:file-change', { detail: { path: rp } })); break }
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
        streaming.value = false; agentMsgIdRef.value = null; streamRef.value = emptyStream()
        window.dispatchEvent(new CustomEvent('gateway:message-complete', { detail: { sessionId: (payload.session_id as string) || sessionIdRef.value || '' } }))
        break
      }
      case 'error': { const em = (payload.message as string) || 'Unknown error'; const id = agentMsgIdRef.value; if (id) { if (streamingMessage.value && streamingMessage.value.id === id) { streamingMessage.value = { ...streamingMessage.value, content: streamingMessage.value.content + `\n\n**Error:** ${em}` } } else { streamingMessage.value = { id, role: 'agent', content: `**Error:** ${em}`, createdAt: Date.now() } } } else { messages.value.push({ id: nextId(), role: 'agent', content: `**Error:** ${em}` }) }; streaming.value = false; agentMsgIdRef.value = null; streamRef.value = emptyStream(); break }
      case 'todos.update': { window.dispatchEvent(new CustomEvent('gateway-todos', { detail: payload })); break }
      case 'clarify.request': { const ri = (payload.request_id as string) || ''; const q = (payload.question as string) || ''; const ch = (payload.choices as string[]) || []; // console.log('[gateway] clarify.request q=',q.slice(0,50)) // 调试：如有问题请开启
      // 多问题支持（2026-10-15）：优先消费完整 questions 数组；缺失时回退单问题字段（旧后端兼容）
      const qsRaw = (payload.questions as Array<{ question?: string; header?: string; options?: string[] }> | undefined) || [];
      const qs = qsRaw.length > 0
        ? qsRaw.map(x => ({ header: String(x.header || 'QUESTION'), question: String(x.question || ''), options: (x.options || []).map(c => ({ label: String(c), description: '' })) }))
        : [{ header: 'QUESTION', question: q, options: ch.map(c => ({ label: c, description: '' })) }];
      const smId = agentMsgIdRef.value;
      if (smId && streamingMessage.value) {
        // 内联渲染：提问元素插入 order 尾部（此刻 order 尾部恰是 ask_user_question 工具卡），
        // 卡片停驻该位置，后续文本/工具追加在其下方，不再钉在整条消息末尾
        const o = streamRef.value.order;
        o.push({ id: `q_${ri}`, type: 'question', questionId: ri, questions: qs });
        scheduleStreamFlush();
      } else {
        // 兜底：无流式消息时作为独立 agent 消息（executionOrder 内联，content 置空避免问题文本重复显示）
        messages.value.push({ id: nextId(), role: 'agent', content: '', executionOrder: [{ id: `q_${ri}`, type: 'question', questionId: ri, questions: qs }] });
      }
      pendingClarify.value = { requestId: ri, question: q || qs[0]?.question || '' }; // console.log('[gateway] pendingClarify SET') // 调试
      playCompletionSound() // AI 提问提醒：与回复完成提示音共用音效设置（off 时静默）
      break }
      case 'permission.request': { window.dispatchEvent(new CustomEvent('gateway:permission-request', { detail: { payload } })); break }
      case 'message.usage': { const cc = (payload.session_cost_cny as number) || 0; const it = (payload.latest_input_tokens as number) || 0; const ot = (payload.latest_output_tokens as number) || 0; const cl = (payload.model_context_limit as number) || 0; const pp = (payload.period as string) || null; const tc = payload.this_call as Record<string, unknown> | undefined; const ce = tc ? { model: (tc.model as string) || (payload.model as string) || 'unknown', inputTokens: (tc.input_tokens as number) || it, outputTokens: (tc.output_tokens as number) || ot, cacheHitTokens: (tc.cache_hit_tokens as number) || 0, costCny: (tc.total_cost as number) || (tc.cost_cny as number) || 0, period: (tc.period as string) || pp } : undefined; usage.value = { sessionCostCny: cc, latestInputTokens: it, latestOutputTokens: ot, modelContextLimit: cl, period: pp, callHistory: ce ? [...(usage.value.callHistory || []), ce] : usage.value.callHistory }; break }
      case 'user_message.saved': { const si = (payload.message_id as number) || 0; if (si) { for (let i = messages.value.length - 1; i >= 0; i--) { if (messages.value[i].role === 'user' && !messages.value[i].messageId) { messages.value[i] = { ...messages.value[i], messageId: si }; const sid = sessionIdRef.value; if (sid) persistMessages(messages.value); break } } }; const ssid = (payload.session_id as string) || sessionIdRef.value || ''; if (ssid) window.dispatchEvent(new CustomEvent('gateway:user-message-saved', { detail: { sessionId: ssid } })); break }
      case 'session.updated': { const title = payload.title as string; const sid = (payload.session_id as string) || ''; if (title && sid) window.dispatchEvent(new CustomEvent('gateway:session-updated', { detail: { sessionId: sid, title } })); break }
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
  // ask_user_question 卡片状态回写：提交/跳过瞬间把卡片 state 置为 answered/skipped。
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

  return { connected, sessionId, messages, streamingMessage, streaming, usage, fileChanges, sessionIdRef, pendingClarify, sendMessage, stopGeneration, clearMessages, createSession: async () => { await createSessionInternal() }, resumeSession, request: <T>(m: string, p?: Record<string, unknown>) => gateway.request<T>(m, p), updateUsage: (patch: Partial<UsageData>) => { usage.value = { ...usage.value, ...patch } }, fetchPricing: async () => { try { const r = await gateway.request<{ pricing?: unknown; period?: string | null; cn_holidays?: { holidays?: string[]; workdays?: string[]; years_covered?: number[]; updated_at?: string | null } | null }>('config.get', { key: 'pricing' }); if (r && typeof r === 'object' && (r as Record<string, unknown>).period) { usage.value = { ...usage.value, period: (r as Record<string, unknown>).period as string } } return r ?? null } catch { return null } }, handleEvent, loadInitialSession, setupConnection, gateway }
})
