import type { Message, ToolCall, ToolStatus, ExecutionOrderItem } from '../../types/agentChat'

export interface BackendMessage {
  role: 'user' | 'assistant' | 'tool' | 'system'
  text?: string
  content?: string
  reasoning?: string
  name?: string
  context?: string
  tool_calls?: string
  created_at?: number
  message_id?: number
  interrupted?: boolean
  is_compact_summary?: boolean
  is_compacted?: boolean
}

export function normalizeExecutionOrder(raw: unknown): ExecutionOrderItem[] | null {
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

export function extractCompactSummary(content: string): string | null {
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

export function convertBackendMessages(raw: BackendMessage[]): Message[] {
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
        try { const raw2 = JSON.parse(bm.tool_calls); const execOrder = normalizeExecutionOrder(raw2); if (execOrder) { msg.executionOrder = execOrder; const tools = execOrder.filter(item => item.type === 'tool' && item.toolInfo).map(item => item.toolInfo!); if (tools.length > 0) msg.toolCalls = tools; if (!execOrder.some(item => item.type === 'text') && (msg.content || '').length > 0) { execOrder.push({ id: 'text_fallback', type: 'text', content: msg.content }) }; if (msg.reasoning && !execOrder.some(item => item.type === 'reasoning')) { execOrder.unshift({ id: 'reasoning_fallback', type: 'reasoning', content: msg.reasoning, startTime: msg.createdAt, durationMs: 0 }) }; const qToolIdx = execOrder.findIndex(item => item.type === 'tool' && item.toolInfo?.label === 'clarify' && item.toolInfo?.output); if (qToolIdx >= 0) { try { const qo = JSON.parse(execOrder[qToolIdx].toolInfo!.output || ''); if (qo && Array.isArray(qo.questions) && qo.questions.length > 0) { execOrder.splice(qToolIdx + 1, 0, { id: `restored_q_${i}_${qToolIdx}`, type: 'question', questionId: `q_restored_${i}_${qToolIdx}`, questions: qo.questions.map((x: any) => ({ prompt: String(x.prompt || ''), title: String(x.title || 'QUESTION'), options: Array.isArray(x.options) ? x.options.map((o: any) => ({ label: String(o.label || ''), detail: String(o.detail || '') })) : [] })), state: qo.cancelled ? 'skipped' : 'answered' }); msg.executionOrder = [...execOrder] } } catch { /* 解析失败则不恢复提问卡片 */ } } pendingTools.length = 0 } } catch {}
      }
      if (!msg.executionOrder && pendingTools.length > 0) { msg.toolCalls = pendingTools.map(t => ({ status: 'completed' as ToolStatus, label: t.name, duration: 0, input: t.input })); pendingTools.length = 0 }
      result.push(msg)
    }
  }
  return result
}

export interface StreamingState { content: string; reasoning: string; toolCalls: ToolCall[]; currentTool: ToolCall | null; order: ExecutionOrderItem[]; _orderSeq: number }

export function emptyStream(): StreamingState { return { content: '', reasoning: '', toolCalls: [], currentTool: null, order: [], _orderSeq: 0 } }

// 后端消息为权威（无附件），localStorage 缓存里可能带图片附件（图片不进 DB、当次即焚）
// → 恢复时按 messageId 把缓存附件合并回后端 user 消息，保住图片显示（方案 A：前端合并缓存）
export function mergeCachedAttachments(converted: Message[], cached: Message[]): Message[] {
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

let _idCounter = 0

export function nextId(): string { return `msg_${Date.now()}_${++_idCounter}` }
