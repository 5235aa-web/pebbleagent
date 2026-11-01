/* Preview stage state.

   Nothing on this screen is authored by the front end: every `@pebble-file:`
   directive the agent writes in a reply becomes an entry, the active entry is
   read back through `file.read_text`, and that source is the only thing the
   iframe renders. The frame keeps the app shell's sandbox shape — scripts and
   inline styles still run, but it cannot reach the network, post a form, or
   resolve a base URL. */

import { computed, reactive, watch } from 'vue'
import { gateway } from './gateway'
import { streamState } from './stream'
import { sessionState } from './store'
import imageLibSource from '../vendor/html-to-image.js?raw'
import { FEEDBACK_CSS, FEEDBACK_JS } from './previewBridge'

export interface PreviewEntry {
  /** Project-relative path with forward slashes. */
  path: string
  /** File name shown on the tab. */
  title: string
  /** Changed while another file was on screen. */
  stale: boolean
}

export interface PreviewComment {
  /** Stable id the bridge inside the frame assigned. */
  cfId: string
  /** CSS selector of the annotated element. */
  selector: string
  tag: string
  /** Text of the element, so the agent can locate it in the source. */
  textSnippet: string
  body: string
}

export const previewState = reactive({
  entries: [] as PreviewEntry[],
  activePath: '',
  code: '',
  srcdoc: '',
  loading: false,
  error: '',
  /** Annotations the bridge has reported for the file on screen. */
  comments: [] as PreviewComment[]
})

/* Same directive the agent writes when it creates a file. The extension gate
   keeps prose about the directive ("@pebble-file: <path>") from registering a
   file called `path`. */
const FILE_RE = /@pebble-file:\s*([\w.\-/]+)/g
const PREVIEWABLE = /\.(html?|svg)$/i

const PREVIEW_CSP =
  "default-src 'none'; script-src 'unsafe-inline' 'unsafe-eval' https:; style-src 'unsafe-inline' https:; img-src data: blob: https: http:; font-src data: https:; connect-src 'none'; form-action 'none'; base-uri 'none'"

/* The bundled library ends with a source-map reference; keeping it would make
   every preview ask the gateway for a file that does not exist. */
const IMAGE_LIB = imageLibSource.replace(/\/\/#\s*sourceMappingURL=.*$/m, '')

/** Height capture, invoked inside the sandboxed frame. */
const BRIDGE = `(function(){
if (window.__pebbleStageBridge) return;
window.__pebbleStageBridge = true;
function post(type, data) {
  try { window.parent.postMessage(Object.assign({ type: type }, data || {}), '*') } catch (e) {}
}
function capture(id) {
  try {
    var lib = window.htmlToImage;
    if (!lib || !lib.toPng) { post('pebble-stage-capture', { id: id, ok: false, error: 'capture library missing' }); return }
    lib.toPng(document.body, { pixelRatio: 2 })
      .then(function (url) { post('pebble-stage-capture', { id: id, ok: true, dataUrl: url }) })
      .catch(function (err) { post('pebble-stage-capture', { id: id, ok: false, error: String((err && err.message) || err) }) });
  } catch (err) {
    post('pebble-stage-capture', { id: id, ok: false, error: String(err) })
  }
}
window.addEventListener('message', function (ev) {
  var d = ev.data || {};
  if (d.type === 'pebble-stage-capture-request') capture(d.id);
});
post('pebble-stage-ready');
})();`

function escapeScript(source: string): string {
  return source.replace(/<\/script/gi, '<\\/script')
}

function injectHead(html: string, head: string): string {
  const doctype = html.match(/^\s*<!doctype[^>]*>/i)
  if (doctype) return html.slice(0, doctype[0].length) + head + html.slice(doctype[0].length)
  const open = html.match(/<head[^>]*>/i)
  if (open) return html.replace(open[0], open[0] + head)
  return head + html
}

function injectTail(html: string, tail: string): string {
  const body = html.match(/<\/body\s*>/i)
  if (body && body.index !== undefined) return html.slice(0, body.index) + tail + html.slice(body.index)
  const end = html.match(/<\/html\s*>/i)
  if (end && end.index !== undefined) return html.slice(0, end.index) + tail + html.slice(end.index)
  return html + tail
}

function buildSrcdoc(raw: string): string {
  const head =
    '<meta http-equiv="Content-Security-Policy" content="' + PREVIEW_CSP + '">' +
    '<style>' + FEEDBACK_CSS + '</style>'
  const tail =
    '<script>' + escapeScript(IMAGE_LIB) + '<\/script>' +
    '<script>' + escapeScript(BRIDGE) + '<\/script>' +
    '<script>' + escapeScript(FEEDBACK_JS) + '<\/script>'
  return injectHead(injectTail(raw, tail), head)
}

function normalizePath(raw: string): string {
  return raw.replace(/\\/g, '/').replace(/^\/+/, '').trim()
}

function titleOf(path: string): string {
  return path.split('/').filter(Boolean).pop() || path
}

/** Files the conversation mentions, in first-seen order. Scanning `plain` keeps
    the dependency one-way: the transcript never learns about the preview. */
const discovered = computed(() => {
  const seen = new Set<string>()
  const scan = (text: string): void => {
    if (!text || !text.includes('@pebble-file')) return
    FILE_RE.lastIndex = 0
    let match: RegExpExecArray | null
    while ((match = FILE_RE.exec(text)) !== null) {
      const path = normalizePath(match[1])
      if (path && PREVIEWABLE.test(path)) seen.add(path)
    }
  }
  for (const item of streamState.items) scan(String(item.plain ?? ''))
  return seen
})

watch(
  () => Array.from(discovered.value).join('\n'),
  () => {
    const active = previewState.activePath
    previewState.entries = Array.from(discovered.value).map(path => ({
      path,
      title: titleOf(path),
      // A brand-new file only counts as pending when another one is on screen.
      stale: Boolean(active) && path !== active && !previewState.entries.some(e => e.path === path)
    }))
    if (!previewState.activePath && previewState.entries.length) {
      void setActive(previewState.entries[previewState.entries.length - 1].path)
    }
  },
  { immediate: true }
)

/** Reads one file and remembers its source. Relative paths are tried first and
    then anchored to the project root, the same order the shell uses. */
export async function setActive(path: string): Promise<void> {
  const target = normalizePath(path)
  if (!target) return
  previewState.activePath = target
  // Selectors belong to one document; carrying them across a switch would send
  // the previous page's annotations with the next edit.
  previewState.comments = []
  previewState.entries.forEach(e => { if (e.path === target) e.stale = false })
  previewState.loading = true
  previewState.error = ''
  const candidates = [target]
  const project = sessionState.projectPath.replace(/\\/g, '/').replace(/\/+$/, '')
  if (project) candidates.push(project + '/' + target)
  for (const candidate of candidates) {
    try {
      const res = await gateway.request<{ ok?: boolean; text?: string }>('file.read_text', { path: candidate })
      if (res?.ok && typeof res.text === 'string') {
        previewState.code = res.text
        previewState.srcdoc = buildSrcdoc(res.text)
        previewState.loading = false
        return
      }
    } catch { /* try the next candidate */ }
  }
  previewState.code = ''
  previewState.srcdoc = ''
  previewState.loading = false
  previewState.error = `Could not read ${target}`
}

export function reloadPreview(): void {
  if (previewState.activePath) void setActive(previewState.activePath)
}

let reloadTimer = 0

/** Marks a file dirty without stealing the tab from what is on screen. A file
    the agent is still writing emits several change events in a row, so the
    reload waits for the burst to settle instead of re-reading per event. */
export function markPreviewChanged(path: string): void {
  const target = normalizePath(path)
  const entry = previewState.entries.find(e => e.path === target)
  if (!entry) return
  if (target !== previewState.activePath) {
    entry.stale = true
    return
  }
  window.clearTimeout(reloadTimer)
  reloadTimer = window.setTimeout(() => { void setActive(target) }, 400)
}

let captureSeq = 0
let pendingCapture:
  | { id: string; resolve: (url: string) => void; reject: (error: Error) => void; timer: number }
  | null = null

/** Asks the sandboxed frame to rasterise itself, which is the only way a
    cross-origin document can be captured. */
export function capturePreview(): Promise<string> {
  return new Promise((resolve, reject) => {
    const frame = document.getElementById('stageFrame') as HTMLIFrameElement | null
    if (!frame?.contentWindow) {
      reject(new Error('No preview is open'))
      return
    }
    const id = 'cap_' + (++captureSeq)
    const timer = window.setTimeout(() => {
      if (pendingCapture?.id === id) {
        pendingCapture = null
        reject(new Error('Capture timed out'))
      }
    }, 15000)
    pendingCapture = { id, resolve, reject, timer }
    frame.contentWindow.postMessage({ type: 'pebble-stage-capture-request', id }, '*')
  })
}

window.addEventListener('message', event => {
  const data = event.data as
    | {
        type?: string
        id?: string
        ok?: boolean
        dataUrl?: string
        error?: string
        comments?: unknown
      }
    | null
  if (!data) return

  if (data.type === 'pebble-feedback-update') {
    const list = Array.isArray(data.comments) ? data.comments : []
    previewState.comments = list.map(raw => {
      const row = (raw ?? {}) as Record<string, unknown>
      return {
        cfId: String(row.cfId ?? ''),
        selector: String(row.selector ?? ''),
        tag: String(row.tag ?? ''),
        textSnippet: String(row.textSnippet ?? ''),
        body: String(row.body ?? '')
      }
    })
    return
  }

  /* The bridge announces itself once the document is parsed; that is the only
     reliable moment to hand the picker its theme colours. */
  if (data.type === 'pebble-stage-ready') {
    activateComments()
    return
  }

  if (data.type !== 'pebble-stage-capture') return
  const pending = pendingCapture
  if (!pending || pending.id !== data.id) return
  pendingCapture = null
  window.clearTimeout(pending.timer)
  if (data.ok && data.dataUrl) pending.resolve(data.dataUrl)
  else pending.reject(new Error(data.error || 'Capture failed'))
})

/* ---- comment layer ---- */

/** Switches the frame into picker mode. The bridge inside owns the hover ring,
    the editor and the numbering; this only supplies the palette so the editor
    matches the app instead of guessing at colours. */
export function activateComments(): void {
  const frame = document.getElementById('stageFrame') as HTMLIFrameElement | null
  const win = frame?.contentWindow
  if (!win) return
  const styles = getComputedStyle(document.documentElement)
  const read = (name: string, fallback: string): string =>
    styles.getPropertyValue(name).trim() || fallback
  const vars = {
    '--fb-accent': read('--accent', '#d64e2a'),
    '--fb-text': read('--ink', '#17130f'),
    '--fb-text-body': read('--muted', '#7a6f62'),
    '--fb-bg-card': read('--surface', '#fffdf9'),
    '--fb-bg-input': read('--surface-2', '#f6f1e8'),
    '--fb-bg-hover': read('--surface-3', '#efe7da'),
    '--fb-border': read('--line', 'rgba(23,19,15,.1)'),
    '--fb-font': read('--font-mono', 'ui-monospace, monospace')
  }
  win.postMessage({ type: 'pebble-feedback-activate', vars }, '*')
}

export function clearComments(): void {
  previewState.comments = []
}

/** Comments ride on a clarify answer rather than a channel of their own: when
    the picked option asks for a change, the annotations replace that answer and
    the agent reads them as the instruction. Without such an option the comments
    stay put, so a decision they were not meant for never carries them away. */
export function mergePreviewComments(answers: Record<string, string>): Record<string, string> {
  const comments = previewState.comments
  if (!comments.length) return answers
  const key = Object.keys(answers).find(name => /修改|modify/i.test(answers[name] || ''))
  if (!key) return answers
  const lines = comments.map(c =>
    `- Location: ${c.selector}\n  Element: <${c.tag}>\n  Original: "${c.textSnippet}"\n  Comment: ${c.body}`
  )
  const merged =
    `Modify\n\n[HTML Page Feedback] The user annotated ${comments.length} edits on the page:\n` +
    lines.join('\n\n')
  return { ...answers, [key]: merged }
}
