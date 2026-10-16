import { ref, onMounted, onUnmounted } from 'vue'

// ─── types ────────────────────────────────────────────
export interface FileItem {
  name: string
  size: number
  data_base64: string
  text_content?: string
  dataUrl?: string
  isImage?: boolean
}

const IMG_RE = /\.(png|jpe?g|gif|webp|bmp|svg)$/i

interface DropState {
  over: boolean
  processing: boolean
  error: string | null
  files: FileItem[]
}

const MAX_SIZE = 10 * 1024 * 1024
const MAX_COUNT = 20
const TEXT_RE = /\.(txt|md|py|js|ts|jsx|tsx|vue|svelte|json|xml|yaml|yml|html|htm|css|scss|less|csv|log|sh|bash|bat|cmd|ps1|ini|toml|cfg|conf|env|sql|rs|go|java|c|cpp|h|hpp|rb|php|swift|kt|scala|r|tex|lua|dart|nim|zig|v|fs|fsx|ml|mli|makefile|cmake|dockerfile|gradle|lock|editorconfig|gitignore|properties)$/i

// ─── helpers ──────────────────────────────────────────
function toText(b64: string): string | undefined {
  try { const u = Uint8Array.from(atob(b64), c => c.charCodeAt(0)); return new TextDecoder('utf-8', { fatal: false }).decode(u) } catch { return undefined }
}

export function fileIcon(n: string): string {
  const e = n.split('.').pop()?.toLowerCase() || ''
  const m: Record<string, string> = { py: 'PY', ts: 'TS', tsx: 'TX', js: 'JS', jsx: 'JX', vue: 'VU', rs: 'RS', go: 'GO', java: 'JA', json: '{}', yaml: 'YM', yml: 'YM', xml: 'XM', toml: 'TO', md: 'MD', css: 'CS', html: 'HT', scss: 'SC', sql: 'SQ', sh: 'SH', bat: 'BT', txt: 'TX', csv: 'CV', log: 'LG', rb: 'RB', php: 'PH', swift: 'SW', kt: 'KT', lua: 'LU', dart: 'DA', png: 'IM', jpg: 'IM', jpeg: 'IM', gif: 'IM', svg: 'SV', pdf: 'PD', zip: 'ZP', gz: 'GZ', rar: 'RA', '7z': '7Z', mp3: 'A3', wav: 'WV', mp4: 'M4' }
  return m[e] || e.toUpperCase().slice(0, 2)
}

export function fileSize(b: number): string {
  if (b < 1024) return `${b}B`
  if (b < 1048576) return `${(b / 1024).toFixed(1)}KB`
  return `${(b / 1048576).toFixed(1)}MB`
}

// ─── FileReader ────────────────────────────────────────
function readFileObject(f: File): Promise<FileItem | null> {
  return new Promise(resolve => {
    const r = new FileReader()
    r.onload = () => {
      const dataUrl = r.result as string
      const b64 = dataUrl.split(',')[1] || ''
      const isImg = IMG_RE.test(f.name)
      resolve({ name: f.name, size: f.size, data_base64: b64, dataUrl, isImage: isImg, text_content: TEXT_RE.test(f.name) ? toText(b64) : undefined })
    }
    r.onerror = () => resolve(null)
    r.readAsDataURL(f)
  })
}

// ─── composable ───────────────────────────────────────
export function useFileDrop() {
  const s = ref<DropState>({ over: false, processing: false, error: null, files: [] })

  async function ingest(items: (File | string)[]) {
    const errs: string[] = []
    const ok: FileItem[] = []
    const src = items.slice(0, MAX_COUNT)
    const results = await Promise.all(src.map(x => typeof x === 'string' ? null : readFileObject(x as File)))
    for (let i = 0; i < results.length; i++) {
      const r = results[i]
      if (!r) { errs.push(`${(src[i] as File).name}: read failed`); continue }
      if (r.size > MAX_SIZE) { errs.push(`${r.name}: ${fileSize(r.size)} → skip`); continue }
      ok.push(r)
    }
    s.value = { ...s.value, files: [...s.value.files, ...ok], processing: false, error: errs.length ? errs.join('; ') : null }
    if (ok.length) (document.querySelector('.ia textarea') as HTMLTextAreaElement | null)?.focus()
  }

  // ─── HTML5 drag-drop ───
  let dc = 0
  function onEnter(e: DragEvent) { e.preventDefault(); dc++; s.value = { ...s.value, over: true } }
  function onOver(e: DragEvent) { e.preventDefault(); if (e.dataTransfer) e.dataTransfer.dropEffect = 'copy' }
  function onLeave() { dc--; if (dc <= 0) { dc = 0; s.value = { ...s.value, over: false } } }
  function onDrop(e: DragEvent) {
    e.preventDefault(); dc = 0; s.value = { ...s.value, over: false, processing: true, error: null }
    if (e.dataTransfer?.files?.length) ingest(Array.from(e.dataTransfer.files))
    else s.value = { ...s.value, processing: false }
  }

  onMounted(() => {
    document.addEventListener('dragenter', onEnter, true)
    document.addEventListener('dragover', onOver, true)
    document.addEventListener('dragleave', onLeave, true)
    document.addEventListener('drop', onDrop, true)
  })

  onUnmounted(() => {
    document.removeEventListener('dragenter', onEnter, true)
    document.removeEventListener('dragover', onOver, true)
    document.removeEventListener('dragleave', onLeave, true)
    document.removeEventListener('drop', onDrop, true)
  })

  function removeFile(i: number) { const f = [...s.value.files]; f.splice(i, 1); s.value = { ...s.value, files: f } }
  function clearFiles() { s.value = { over: false, processing: false, error: null, files: [] } }

  return { state: s, removeFile, clearFiles, ingest, fileIcon, fileSize }
}
