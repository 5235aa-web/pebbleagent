/** 解析并结构化渲染「批量转发消息」卡片所需的数据。 */

export interface ForwardedToolRecord {
  name: string
  status: string
  argHint: string
  detail: string
}

export interface ForwardedSection {
  index: number
  roleKind: 'user' | 'assistant' | 'summary'
  roleLabel: string
  time: string
  bodyMarkdown: string
  tools: ForwardedToolRecord[]
}

export interface ForwardedPayload {
  sourceTitle: string
  count: number
  sections: ForwardedSection[]
  raw: string
}

const HEADER_EN_RE = /^\[Forwarded messages · from session "([^"]+)"(?:\s*\((\d+)\s*total\))?\]\s*/
const HEADER_ZH_RE = /^\[转发消息 · 来自会话「([^」]+)」(?:（共\s*(\d+)\s*条）)?\]\s*/
const SECTION_SPLIT_RE = /\n---\n(?=\s*###\s+\d+\.\s+(?:User|Pebble Assistant|Context Summary \(Full\)|用户|Pebble 助手|上下文摘要（全量）))/
const SECTION_HEAD_RE = /^###\s+(\d+)\.\s+(User|Pebble Assistant|Context Summary \(Full\)|用户|Pebble 助手|上下文摘要（全量）)(?:\s*\(([^)]+)\)|（([^）]+)）)?\s*/
const TOOL_SECTION_RE = /\n\*\*(?:Edit\/Exec Tool Records|编辑\/执行类工具记录)[^*\n]*\*\*\n/
const TOOL_ITEM_RE = /^- `([^`]+)` \[([^\]]+)\](?:\n```text\n([\s\S]*?)\n```)?/gm

const ROLE_LABEL_MAP: Record<string, { kind: ForwardedSection['roleKind']; label: string }> = {
  User: { kind: 'user', label: 'User' },
  用户: { kind: 'user', label: 'User' },
  'Pebble Assistant': { kind: 'assistant', label: 'Pebble Assistant' },
  'Pebble 助手': { kind: 'assistant', label: 'Pebble Assistant' },
  'Context Summary (Full)': { kind: 'summary', label: 'Context Summary (Full)' },
  '上下文摘要（全量）': { kind: 'summary', label: 'Context Summary (Full)' }
}

export function isForwardedText(text: string): boolean {
  return typeof text === 'string' && (
    text.startsWith('[Forwarded messages · from session "') ||
    text.startsWith('[转发消息 · 来自会话「')
  )
}

function extractArgHint(detail: string): string {
  if (!detail) return ''
  const firstLine = detail.split('\n')[0] || ''
  const paramLine = firstLine.startsWith('Args: ')
    ? firstLine.slice(6)
    : firstLine.startsWith('参数: ')
      ? firstLine.slice(4)
      : firstLine
  const keyMatch = /(?:file_path|path|target_file|target_path|command|cmd|source|url)=([^,\n]+)/.exec(paramLine)
  if (keyMatch) return keyMatch[1].trim()
  if (paramLine.startsWith('{') && paramLine.includes(':')) {
    const jsonMatch = /"(?:file_path|path|command|cmd|source)"\s*:\s*"([^"]+)"/.exec(paramLine)
    if (jsonMatch) return jsonMatch[1].trim()
  }
  return paramLine.length > 64 ? paramLine.slice(0, 61) + '...' : paramLine
}

function parseToolsBlock(toolsRaw: string): ForwardedToolRecord[] {
  const records: ForwardedToolRecord[] = []
  TOOL_ITEM_RE.lastIndex = 0
  let match: RegExpExecArray | null
  while ((match = TOOL_ITEM_RE.exec(toolsRaw)) !== null) {
    const detail = (match[3] || '').trim()
    records.push({
      name: match[1].trim(),
      status: match[2].trim(),
      argHint: extractArgHint(detail),
      detail
    })
  }
  return records
}

export function parseForwardedPayload(rawText: string): ForwardedPayload | null {
  if (!isForwardedText(rawText)) return null
  const normalized = rawText.replace(/\r\n/g, '\n')
  const hm = HEADER_EN_RE.exec(normalized) || HEADER_ZH_RE.exec(normalized)
  if (!hm) return null

  const sourceTitle = hm[1].trim() || 'Untitled session'
  let rest = normalized.slice(hm[0].length)
  if (rest.startsWith('---')) {
    rest = rest.replace(/^---\s*/, '')
  }

  const rawChunks = rest
    .split(SECTION_SPLIT_RE)
    .map(s => s.trim())
    .filter(Boolean)

  const sections: ForwardedSection[] = []
  for (let i = 0; i < rawChunks.length; i++) {
    const chunk = rawChunks[i]
    const sh = SECTION_HEAD_RE.exec(chunk)
    let index = i + 1
    let roleLabel = 'Message'
    let roleKind: ForwardedSection['roleKind'] = 'user'
    let time = ''
    let body = chunk

    if (sh) {
      index = Number(sh[1]) || i + 1
      const mapped = ROLE_LABEL_MAP[sh[2]]
      roleLabel = mapped?.label || sh[2]
      roleKind = mapped?.kind || 'user'
      time = (sh[3] || sh[4] || '').trim()
      body = chunk.slice(sh[0].length).trim()
    }

    let bodyMarkdown = body
    let tools: ForwardedToolRecord[] = []
    if (roleKind === 'assistant') {
      const tm = TOOL_SECTION_RE.exec(body)
      if (tm) {
        bodyMarkdown = body.slice(0, tm.index).trim()
        const toolsPart = body.slice(tm.index + tm[0].length).trim()
        tools = parseToolsBlock(toolsPart)
      }
    }

    sections.push({
      index,
      roleKind,
      roleLabel,
      time,
      bodyMarkdown,
      tools
    })
  }

  if (!sections.length) return null
  const count = Number(hm[2]) || sections.length
  return { sourceTitle, count, sections, raw: rawText }
}
