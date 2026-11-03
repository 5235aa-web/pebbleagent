/* Markdown and row renderers for live message rows.

   Everything here emits the exact class names orbit.css already styles: .md
   paragraphs, .code blocks with a gutter and tk-* tokens, user bubbles with
   their attachment chips. Tool calls are a component (`StreamToolCard.vue`),
   not markup from this module. */


const CARET = '<span class="caret"></span>'

const ICONS: Record<string, string> = {
  search: '<circle cx="11" cy="11" r="7"/><line x1="20" y1="20" x2="16" y2="16"/>',
  edit: '<path d="M12 20h9"/><path d="M16.5 3.5a2.12 2.12 0 0 1 3 3L7 19l-4 1 1-4z"/>',
  read: '<path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/>',
  shell: '<polyline points="4 17 10 11 4 5"/><line x1="12" y1="19" x2="20" y2="19"/>',
  form: '<rect x="3" y="4" width="18" height="16" rx="2"/><line x1="7" y1="9" x2="17" y2="9"/><line x1="7" y1="14" x2="13" y2="14"/>'
}

export function toolIcon(name: string): string {
  const n = name.toLowerCase()
  const path =
    /^(grep|glob|search|find|web_search)/.test(n) ? ICONS.search
    : /(edit|write|create|move|delete|patch|apply)/.test(n) ? ICONS.edit
    : /(read|cat|list|open|note|inspect)/.test(n) ? ICONS.read
    : /(shell|bash|exec|command|run)/.test(n) ? ICONS.shell
    : ICONS.form
  return '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7">' + path + '</svg>'
}

export function escapeHtml(text: string): string {
  return text
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
}

/* ---- syntax highlighting ----
   The prototype hand-wrote tk-* spans. A small tokenizer reproduces the same
   five token classes: comments, strings, numbers, keywords, function calls. */

const KEYWORDS: Record<string, string[]> = {
  js: ['const', 'let', 'var', 'function', 'return', 'if', 'else', 'for', 'while', 'import', 'export', 'from', 'class', 'new', 'await', 'async', 'try', 'catch', 'throw', 'typeof', 'instanceof', 'null', 'undefined', 'true', 'false', 'this'],
  ts: ['const', 'let', 'var', 'function', 'return', 'if', 'else', 'for', 'while', 'import', 'export', 'from', 'class', 'interface', 'type', 'new', 'await', 'async', 'try', 'catch', 'throw', 'typeof', 'null', 'undefined', 'true', 'false', 'this', 'as'],
  css: ['var', 'calc', 'rgb', 'hsl', 'linear-gradient', 'important'],
  py: ['def', 'class', 'return', 'if', 'elif', 'else', 'for', 'while', 'import', 'from', 'as', 'with', 'try', 'except', 'finally', 'raise', 'None', 'True', 'False', 'lambda', 'yield', 'async', 'await', 'pass', 'in', 'not', 'and', 'or', 'self'],
  sh: ['if', 'then', 'else', 'fi', 'for', 'do', 'done', 'while', 'case', 'esac', 'echo', 'export', 'cd', 'source'],
  json: ['true', 'false', 'null']
}

function langKey(lang: string): string {
  const l = (lang || '').toLowerCase()
  if (l === 'js' || l === 'jsx' || l === 'javascript') return 'js'
  if (l === 'ts' || l === 'tsx' || l === 'typescript' || l === 'vue') return 'ts'
  if (l === 'css' || l === 'scss' || l === 'less') return 'css'
  if (l === 'py' || l === 'python') return 'py'
  if (l === 'sh' || l === 'bash' || l === 'shell' || l === 'ps1') return 'sh'
  if (l === 'json') return 'json'
  return ''
}

export function highlight(code: string, lang: string): string {
  const key = langKey(lang)
  const words = KEYWORDS[key] || []
  // Split into code / string / comment runs first so nothing inside them is
  // re-tokenized.
  const parts: string[] = []
  const re = /(\/\/[^\n]*|#[^\n]*|\/\*[\s\S]*?\*\/)|("(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*'|`(?:\\.|[^`\\])*`)/g
  let last = 0
  let m: RegExpExecArray | null
  while ((m = re.exec(code)) !== null) {
    if (m.index > last) parts.push(tok(code.slice(last, m.index), words))
    if (m[1]) parts.push('<span class="tk-c">' + escapeHtml(m[1]) + '</span>')
    else parts.push('<span class="tk-s">' + escapeHtml(m[2]) + '</span>')
    last = m.index + m[0].length
  }
  if (last < code.length) parts.push(tok(code.slice(last), words))
  return parts.join('')
}

function tok(text: string, words: string[]): string {
  const re = /([A-Za-z_$][\w$]*)|(\d+(?:\.\d+)?(?:px|rem|em|s|ms|%|k)?)/g
  let out = ''
  let last = 0
  let m: RegExpExecArray | null
  while ((m = re.exec(text)) !== null) {
    out += escapeHtml(text.slice(last, m.index))
    if (m[1]) {
      const w = m[1]
      if (words.indexOf(w) > -1) out += '<span class="tk-k">' + w + '</span>'
      else {
        const next = text.slice(m.index + w.length, m.index + w.length + 1)
        out += next === '(' ? '<span class="tk-f">' + w + '</span>' : escapeHtml(w)
      }
    } else {
      out += '<span class="tk-n">' + escapeHtml(m[2]) + '</span>'
    }
    last = m.index + m[0].length
  }
  out += escapeHtml(text.slice(last))
  return out
}

/* ---- code block ---- */

export function renderCodeBlock(lang: string, code: string): string {
  const lines = code.replace(/\n$/, '').split('\n')
  const gutter = lines.map((_, i) => i + 1).join('\n')
  const body = lines
    .map(line => '<span class="ln">' + (line ? highlight(line, lang) : '') + '</span>')
    .join('')
  return (
    '<div class="code">' +
    '<div class="code-hdr">' +
    '<span class="code-lang">' + escapeHtml(lang || 'code') + '</span>' +
    '<span class="code-lines">' + lines.length + ' lines</span>' +
    '<span class="code-hdr-spacer"></span>' +
    '<button class="code-copy" data-copy>' +
    '<svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8">' +
    '<rect x="9" y="9" width="12" height="12" rx="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/></svg> copy' +
    '</button></div>' +
    '<div class="code-body"><div class="gutter">' + gutter + '</div>' +
    '<pre class="code-pre">' + body + '</pre></div></div>'
  )
}

/* ---- markdown ---- */

function inline(text: string): string {
  const codes: string[] = []
  let s = text.replace(/`([^`]+)`/g, (_all, code) => {
    codes.push('<code>' + escapeHtml(code) + '</code>')
    return '\u0000' + (codes.length - 1) + '\u0000'
  })
  s = escapeHtml(s)
  s = s.replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>')
  s = s.replace(/(^|[\s(])\*([^*]+)\*/g, '$1<em>$2</em>')
  s = s.replace(/\[([^\]]+)\]\(([^)]+)\)/g, '<a href="$2" target="_blank" rel="noreferrer">$1</a>')
  s = s.replace(/\u0000(\d+)\u0000/g, (_all, i) => codes[Number(i)] || '')
  return s
}

/** Renders the inside of a .md container: paragraphs, h4, ul/ol lists. */
export function renderMarkdown(text: string, streaming = false): string {
  const src = text.replace(/\r\n/g, '\n')
  const out: string[] = []
  const lines = src.split('\n')
  let i = 0
  let para: string[] = []

  const flushPara = () => {
    if (!para.length) return
    out.push('<p>' + para.map(inline).join('<br>') + '</p>')
    para = []
  }

  while (i < lines.length) {
    const line = lines[i]
    if (!line.trim()) { flushPara(); i++; continue }

    const h = /^#{1,6}\s+(.*)$/.exec(line)
    if (h) { flushPara(); out.push('<h4>' + inline(h[1]) + '</h4>'); i++; continue }

    if (/^\s*[-*+]\s+/.test(line)) {
      flushPara()
      const items: string[] = []
      while (i < lines.length && /^\s*[-*+]\s+/.test(lines[i])) {
        items.push('<li>' + inline(lines[i].replace(/^\s*[-*+]\s+/, '')) + '</li>')
        i++
      }
      out.push('<ul>' + items.join('') + '</ul>')
      continue
    }

    if (/^\s*\d+[.)]\s+/.test(line)) {
      flushPara()
      const items: string[] = []
      while (i < lines.length && /^\s*\d+[.)]\s+/.test(lines[i])) {
        items.push('<li>' + inline(lines[i].replace(/^\s*\d+[.)]\s+/, '')) + '</li>')
        i++
      }
      out.push('<ol>' + items.join('') + '</ol>')
      continue
    }

    para.push(line)
    i++
  }
  flushPara()

  if (streaming && out.length) out[out.length - 1] = out[out.length - 1].replace(/<\/p>$/, CARET + '</p>')
  else if (streaming) out.push('<p>' + CARET + '</p>')
  return out.join('')
}

/* ---- composed rows ---- */


export type ToolState = 'streaming' | 'running' | 'completed' | 'failed' | 'cancelled'

export interface ToolCallView {
  name: string
  arg?: string
  status: ToolState
  durationS?: number
  input?: string
  output?: string
  error?: string
}

/* A chip in a user bubble. `size` arrives already formatted for display, so the
   renderer never does the conversion itself. */
export interface AttachmentView {
  name: string
  size: string
  kind: 'image' | 'file'
}

export function renderAttachments(items: AttachmentView[]): string {
  if (!items.length) return ''
  const fileIco = '<span class="ac-ico"><svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/></svg></span>'
  const imgIco = '<span class="ac-ico"><svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="3" y="3" width="18" height="18" rx="2"/><circle cx="8.5" cy="8.5" r="1.5"/><polyline points="21 15 16 10 5 21"/></svg></span>'
  return (
    '<div class="user-attach">' +
    items.map(a =>
      '<span class="attach-chip">' + (a.kind === 'image' ? imgIco : fileIco) +
      escapeHtml(a.name) + '<span class="ac-size">' + escapeHtml(a.size) + '</span></span>'
    ).join('') +
    '</div>'
  )
}

export function renderUserBubble(text: string, attachments: AttachmentView[], time: string, tokens?: number, itemId?: string): string {
  const actions = itemId
    ? '<div class="msg-actions ma-user">' +
      '<button class="ma-btn" type="button" data-msg-act="copy" data-item-id="' + escapeHtml(itemId) + '">' +
      '<svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6"><rect x="9" y="9" width="12" height="12" rx="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/></svg>' +
      'Copy</button>' +
      '<button class="ma-btn" type="button" data-msg-act="del" data-item-id="' + escapeHtml(itemId) + '">' +
      '<svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6"><polyline points="3 6 5 6 21 6"/><path d="M19 6l-1 14H6L5 6"/><path d="M10 11v6M14 11v6"/></svg>' +
      'Delete</button>' +
      '</div>'
    : ''
  return (
    '<div class="bubble">' +
    renderAttachments(attachments) +
    '<div class="bubble-text">' + escapeHtml(text) + '</div>' +
    '<div class="bubble-foot"><span>' + escapeHtml(time) + '</span><span>·</span><span>' +
    (tokens ? tokens + ' tokens' : 'sent') + '</span></div>' +
    '</div>' + actions
  )
}

/** Streaming fast path: escaping and line breaks only.

    A full markdown pass on every delta re-parses the whole growing answer,
    which is quadratic in the reply length and rebuilds the DOM subtree dozens
    of times per second. The settled text is rendered properly once, on
    message.complete. */
export function renderPlain(text: string, streaming: boolean): string {
  const blocks = text.split(/\n{2,}/)
  const out: string[] = []
  for (let i = 0; i < blocks.length; i++) {
    const body = escapeHtml(blocks[i]).replace(/\n/g, '<br>')
    if (!body && i < blocks.length - 1) continue
    const last = i === blocks.length - 1
    out.push('<p>' + body + (streaming && last ? CARET : '') + '</p>')
  }
  return out.join('')
}

export function nowClock(): string {
  const d = new Date()
  const h = String(d.getHours()).padStart(2, '0')
  const m = String(d.getMinutes()).padStart(2, '0')
  return h + ':' + m
}

/** A notebook entry outlives the session, so its stamp carries the day too:
    `YYYY-MM-DD HH:MM`. Minutes are the finest grain the reader needs. */
export function nowStamp(): string {
  const d = new Date()
  const p = (n: number) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`
}

/* History carries `created_at` as an epoch float (`messages.created_at` is REAL
   in the gateway store), while live rows carry a clock string. Slicing the
   float as if it were `YYYY-MM-DD HH:MM:SS` returns the fractional digits, so
   every formatter has to come through here first. */
export function dateOf(raw: unknown): Date | null {
  const s = String(raw ?? '').trim()
  if (!s) return null
  const n = Number(s)
  if (Number.isFinite(n) && n > 0) {
    // Seconds run to ~1.7e9 and milliseconds to ~1.7e12, so 1e11 splits them.
    const d = new Date(n > 1e11 ? n : n * 1000)
    return Number.isNaN(d.getTime()) ? null : d
  }
  // `2026-10-29 10:22:35` is not a shape the Date parser accepts everywhere.
  const d = new Date(s.includes('T') ? s : s.replace(' ', 'T'))
  return Number.isNaN(d.getTime()) ? null : d
}

/** Wall clock `HH:MM` for a stored timestamp, empty when it cannot be read. */
export function clockOf(raw: unknown): string {
  const d = dateOf(raw)
  if (!d) return ''
  return String(d.getHours()).padStart(2, '0') + ':' + String(d.getMinutes()).padStart(2, '0')
}
