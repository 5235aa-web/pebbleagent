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
import type { ClarifyAnswer } from './panels'
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

export type PreviewMode = 'interact' | 'annotate'

export const previewState = reactive({
  entries: [] as PreviewEntry[],
  activePath: '',
  /** Candidate path that succeeded in file.read_text (used for OS open). */
  resolvedPath: '',
  code: '',
  srcdoc: '',
  loading: false,
  error: '',
  /** 'interact' lets the user click and test the HTML page normally;
      'annotate' intercepts clicks to leave element-level feedback comments. */
  mode: 'interact' as PreviewMode,
  /** Annotations the bridge has reported for the file on screen. */
  comments: [] as PreviewComment[]
})

/* Same directive the agent writes when it creates a file. Supports Unicode
   filenames, subpaths and `./` prefixes while stopping before quotes,
   backticks and brackets so prose or markdown wrappers are left behind. */
const FILE_RE = /@pebble-file:\s*([^\s"'`<>()\[\]{}]+?\.(?:html?|svg))/gi
const PREVIEWABLE = /\.(html?|svg)$/i

const PREVIEW_CSP =
  "default-src 'none'; script-src 'unsafe-inline' 'unsafe-eval' https:; style-src 'unsafe-inline' https:; img-src data: blob: https: http:; font-src data: https:; media-src data: blob: https: http:; connect-src 'none'; form-action 'none'; base-uri 'none'"

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
  const open = html.match(/<head[^>]*>/i)
  if (open && open.index !== undefined) {
    return html.slice(0, open.index + open[0].length) + head + html.slice(open.index + open[0].length)
  }
  const htmlTag = html.match(/<html[^>]*>/i)
  if (htmlTag && htmlTag.index !== undefined) {
    return html.slice(0, htmlTag.index + htmlTag[0].length) + `<head>${head}</head>` + html.slice(htmlTag.index + htmlTag[0].length)
  }
  const doctype = html.match(/^\s*<!doctype[^>]*>/i)
  if (doctype) return html.slice(0, doctype[0].length) + `<head>${head}</head>` + html.slice(doctype[0].length)
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

export function normalizePreviewPath(raw: string): string {
  let p = raw.replace(/\\/g, '/').trim()
  while (p.startsWith('./')) p = p.slice(2)
  return p.replace(/^\/+/, '')
}

function titleOf(path: string): string {
  return path.split('/').filter(Boolean).pop() || path
}

function samePreviewPath(entryPath: string, changedRaw: string): boolean {
  const normEntry = normalizePreviewPath(entryPath).toLowerCase()
  let normChanged = normalizePreviewPath(changedRaw).toLowerCase()
  if (!normEntry || !normChanged) return false
  if (normEntry === normChanged) return true
  const project = sessionState.projectPath.replace(/\\/g, '/').replace(/\/+$/, '').replace(/^\/+/, '').toLowerCase()
  if (project && normChanged.startsWith(project + '/')) {
    normChanged = normChanged.slice(project.length + 1)
    if (normEntry === normChanged) return true
  }
  return normChanged.endsWith('/' + normEntry)
}

/** Files the conversation mentions, in first-seen order. Scanning `plain` and
    collapsed `archivedPlain` keeps the dependency one-way: the transcript
    never learns about the preview. */
const discovered = computed(() => {
  const seen = new Set<string>()
  const scan = (text: string): void => {
    if (!text || !text.includes('@pebble-file')) return
    FILE_RE.lastIndex = 0
    let match: RegExpExecArray | null
    while ((match = FILE_RE.exec(text)) !== null) {
      const path = normalizePreviewPath(match[1])
      if (path && PREVIEWABLE.test(path)) seen.add(path)
    }
  }
  for (const text of streamState.archivedPlain) scan(text)
  for (const item of streamState.items) scan(String(item.plain ?? ''))
  return seen
})

let loadSeq = 0

function resetPreviewState(): void {
  loadSeq += 1
  previewState.entries = []
  previewState.activePath = ''
  previewState.resolvedPath = ''
  previewState.code = ''
  previewState.srcdoc = ''
  previewState.loading = false
  previewState.error = ''
  previewState.comments = []
}

/* Switching sessions must drop the previous session's document, code and
   annotations instead of carrying them over. */
watch(
  () => streamState.sessionId || sessionState.activeId,
  () => { resetPreviewState() }
)

watch(
  () => Array.from(discovered.value).join('\n'),
  () => {
    const prevEntries = previewState.entries
    const active = previewState.activePath
    const nextList = Array.from(discovered.value)
    previewState.entries = nextList.map(path => {
      const prev = prevEntries.find(e => e.path === path)
      return {
        path,
        title: titleOf(path),
        // Preserve existing stale flag; a brand-new file is stale only when another one is already open.
        stale: prev ? prev.stale : (Boolean(active) && path !== active)
      }
    })
    if (active && !nextList.includes(active)) {
      previewState.activePath = ''
      previewState.resolvedPath = ''
      previewState.code = ''
      previewState.srcdoc = ''
      previewState.comments = []
    }
    if (!previewState.activePath && previewState.entries.length) {
      void setActive(previewState.entries[previewState.entries.length - 1].path)
    }
  },
  { immediate: true }
)

/** Reads one file and remembers its source. Relative paths are tried first and
    then anchored to the project root, the same order the shell uses. */
export async function setActive(path: string): Promise<void> {
  const target = normalizePreviewPath(path)
  if (!target) return
  const seq = ++loadSeq
  previewState.activePath = target
  previewState.resolvedPath = ''
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
      if (seq !== loadSeq) return
      if (res?.ok && typeof res.text === 'string') {
        previewState.resolvedPath = candidate
        previewState.code = res.text
        previewState.srcdoc = buildSrcdoc(res.text)
        previewState.loading = false
        return
      }
    } catch {
      if (seq !== loadSeq) return
      /* try the next candidate */
    }
  }
  if (seq !== loadSeq) return
  previewState.code = ''
  previewState.srcdoc = ''
  previewState.loading = false
  previewState.error = `Could not read ${target}`
}

export function reloadPreview(): void {
  if (previewState.activePath) void setActive(previewState.activePath)
}

export function openPreviewStage(path?: string): void {
  if (path) void setActive(path)
  const stage = document.getElementById('previewStage')
  if (!stage) return
  stage.classList.add('open')
  stage.setAttribute('aria-hidden', 'false')
}

let reloadTimer = 0

/** Marks a file dirty without stealing the tab from what is on screen. A file
    the agent is still writing emits several change events in a row, so the
    reload waits for the burst to settle instead of re-reading per event. */
export function markPreviewChanged(path: string): void {
  const entry = previewState.entries.find(e => samePreviewPath(e.path, path))
  if (!entry) return
  if (entry.path !== previewState.activePath) {
    entry.stale = true
    return
  }
  const activeTarget = entry.path
  window.clearTimeout(reloadTimer)
  reloadTimer = window.setTimeout(() => { void setActive(activeTarget) }, 400)
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

  /* The bridge announces itself once the document is parsed; apply the active
     mode so switching pages preserves whether the user is interacting or annotating. */
  if (data.type === 'pebble-stage-ready') {
    if (previewState.mode === 'annotate') activateComments()
    else deactivateComments()
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

export function deactivateComments(): void {
  const frame = document.getElementById('stageFrame') as HTMLIFrameElement | null
  const win = frame?.contentWindow
  if (!win) return
  win.postMessage({ type: 'pebble-feedback-deactivate' }, '*')
}

export function setPreviewMode(mode: PreviewMode): void {
  previewState.mode = mode
  if (mode === 'annotate') activateComments()
  else deactivateComments()
}

export function clearComments(): void {
  previewState.comments = []
  const frame = document.getElementById('stageFrame') as HTMLIFrameElement | null
  frame?.contentWindow?.postMessage({ type: 'pebble-feedback-clear' }, '*')
}

const MODIFY_RE = /修改|modify|adjust|revise|change|edit|fix|update|优化|调整|完善|改/i
const ACCEPT_ONLY_RE = /^(accept|approve|approved|ok|done|lgtm|接受|确认|通过|完成|不用改|满意)$/i

/** Comments ride on a clarify answer rather than a channel of their own: when
    the picked option asks for a change (or is any non-accept reply), the
    annotations attach to that answer so the agent receives them reliably. */
export function mergePreviewComments(answers: Record<string, ClarifyAnswer>): Record<string, ClarifyAnswer> {
  const comments = previewState.comments
  const keys = Object.keys(answers)
  if (!comments.length || !keys.length) return answers
  let key = keys.find(name => {
    const answer = answers[name]
    return MODIFY_RE.test([...answer.picked, answer.custom].join(' '))
  })
  if (!key) {
    key = keys.find(name => {
      const answer = answers[name]
      const text = [...answer.picked, answer.custom].join(' ').trim()
      return Boolean(text) && !ACCEPT_ONLY_RE.test(text)
    })
  }
  if (!key) return answers
  const lines = comments.map(c =>
    `- Location: ${c.selector}\n  Element: <${c.tag}>\n  Original: "${c.textSnippet}"\n  Comment: ${c.body}`
  )
  const feedbackBlock =
    `[HTML Page Feedback] The user annotated ${comments.length} edits on the page:\n` +
    lines.join('\n\n')
  const prev = answers[key]
  const isPureModifyLabel = prev.picked.length > 0 && prev.picked.every(p => MODIFY_RE.test(p)) && !prev.custom.trim()
  if (isPureModifyLabel) {
    return { ...answers, [key]: { picked: [], custom: `Modify\n\n${feedbackBlock}` } }
  }
  const combinedCustom = prev.custom.trim() ? `${prev.custom.trim()}\n\n${feedbackBlock}` : feedbackBlock
  return { ...answers, [key]: { picked: prev.picked, custom: combinedCustom } }
}
