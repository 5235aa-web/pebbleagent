import { reactive } from 'vue'

/* Attachment intake for the composer.

   Mirrors the old shell's `useFileDrop`: drag anywhere in the window or pick
   through the file input, cap the batch, decode text files into `text_content`
   and rasterise images into `data_base64` plus a preview URL. The state is a
   module singleton because three separate places need the same list: the tray
   renders it, the drop veil reads `over`, and the submit path serialises it. */

export const IMAGE_RE = /\.(png|jpe?g|gif|webp|bmp|svg)$/i

const TEXT_RE =
  /\.(txt|md|markdown|py|js|mjs|cjs|ts|tsx|jsx|vue|svelte|json|jsonl|xml|yaml|yml|html|htm|css|scss|sass|less|csv|tsv|log|sh|bash|zsh|bat|cmd|ps1|ini|toml|cfg|conf|env|sql|rs|go|java|c|h|cpp|hpp|cs|rb|php|swift|kt|scala|r|tex|lua|dart|nim|zig|v|fs|ml|mli|gradle|properties|dockerfile|makefile|cmake)$/i

/* 10 MB per file and 20 per turn: the gateway forwards these as base64 inside a
   single JSON frame, so an unbounded batch would blow the socket payload. */
const MAX_SIZE = 10 * 1024 * 1024
const MAX_COUNT = 20

export type ImageDetail = 'auto' | 'low' | 'orig'

export interface DroppedFile {
  /* Stable key for v-for; the name alone repeats when two files share it. */
  key: string
  name: string
  size: number
  type: string
  isImage: boolean
  data_base64: string
  text_content?: string
  dataUrl?: string
  detail: ImageDetail
}

interface DropState {
  over: boolean
  processing: boolean
  error: string | null
  files: DroppedFile[]
}

export const fileDrop = reactive<DropState>({
  over: false,
  processing: false,
  error: null,
  files: []
})

let keySeq = 0
const nextKey = () => `att-${++keySeq}`

export function fileSize(bytes: number): string {
  if (!Number.isFinite(bytes) || bytes < 0) return ''
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`
}

export function fileIcon(name: string): string {
  const ext = name.split('.').pop()?.toLowerCase() ?? ''
  const map: Record<string, string> = {
    py: 'PY', ts: 'TS', tsx: 'TX', js: 'JS', mjs: 'JS', cjs: 'JS', jsx: 'JX',
    vue: 'VU', svelte: 'SV', rs: 'RS', go: 'GO', java: 'JA', cs: 'C#',
    json: '{}', jsonl: '{ }', yaml: 'YM', yml: 'YM', xml: 'XM', toml: 'TO',
    md: 'MD', markdown: 'MD', css: 'CS', scss: 'SC', sass: 'SA', less: 'LE',
    html: 'HT', htm: 'HT', sql: 'SQ', sh: 'SH', bash: 'SH', zsh: 'SH',
    bat: 'BT', cmd: 'BT', ps1: 'PS', txt: 'TX', csv: 'CV', tsv: 'CV',
    log: 'LG', rb: 'RB', php: 'PH', swift: 'SW', kt: 'KT', scala: 'SC',
    c: 'C', h: 'H', cpp: 'C+', hpp: 'H+', r: 'R', tex: 'TX', lua: 'LU',
    dart: 'DA', nim: 'NI', zig: 'ZG', png: 'PNG', jpg: 'JPG', jpeg: 'JPG',
    gif: 'GIF', webp: 'WEBP', bmp: 'BMP', svg: 'SVG'
  }
  if (map[ext]) return map[ext]
  return ext ? ext.slice(0, 3).toUpperCase() : 'FILE'
}

function readAsBase64(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader()
    reader.onload = () => {
      const out = String(reader.result ?? '')
      // `result` is a data URL; the gateway wants the bare payload.
      const comma = out.indexOf(',')
      resolve(comma >= 0 ? out.slice(comma + 1) : out)
    }
    reader.onerror = () => reject(reader.error ?? new Error('read failed'))
    reader.readAsDataURL(file)
  })
}

function decodeText(base64: string): string | undefined {
  try {
    const bytes = Uint8Array.from(atob(base64), c => c.charCodeAt(0))
    return new TextDecoder('utf-8', { fatal: false }).decode(bytes)
  } catch {
    return undefined
  }
}

export function pushError(message: string): void {
  fileDrop.error = message
  window.setTimeout(() => {
    if (fileDrop.error === message) fileDrop.error = null
  }, 4000)
}

export async function ingest(items: (File | string)[]): Promise<void> {
  if (!items.length) return
  fileDrop.processing = true
  try {
    for (const raw of items) {
      if (fileDrop.files.length >= MAX_COUNT) {
        pushError(`At most ${MAX_COUNT} files per message`)
        break
      }
      if (typeof raw === 'string') continue
      const file = raw
      if (file.size > MAX_SIZE) {
        pushError(`${file.name} is larger than ${fileSize(MAX_SIZE)}`)
        continue
      }
      if (fileDrop.files.some(f => f.name === file.name && f.size === file.size)) {
        pushError(`${file.name} is already attached`)
        continue
      }
      const isImage = IMAGE_RE.test(file.name) || file.type.startsWith('image/')
      const base64 = await readAsBase64(file)
      const entry: DroppedFile = {
        key: nextKey(),
        name: file.name,
        size: file.size,
        type: file.type || (isImage ? 'image/*' : 'text/plain'),
        isImage,
        data_base64: base64,
        detail: 'auto'
      }
      if (isImage) {
        entry.dataUrl = `data:${file.type || 'image/png'};base64,${base64}`
      } else if (TEXT_RE.test(file.name) || file.type.startsWith('text/')) {
        entry.text_content = decodeText(base64)
      }
      fileDrop.files.push(entry)
    }
  } catch (err) {
    pushError(err instanceof Error ? err.message : 'Could not read that file')
  } finally {
    fileDrop.processing = false
  }
}

export function removeFile(index: number): void {
  if (index < 0 || index >= fileDrop.files.length) return
  fileDrop.files.splice(index, 1)
}

export function clearFiles(): void {
  fileDrop.files.splice(0, fileDrop.files.length)
  fileDrop.error = null
  fileDrop.processing = false
}

export function setImageDetail(detail: ImageDetail): void {
  for (const file of fileDrop.files) {
    if (file.isImage) file.detail = detail
  }
}

/* Wire shape for `prompt.submit`; mirrors what the old shell put on the socket. */
export interface WireAttachment {
  name: string
  type: string
  size: number
  text_content?: string
  data_base64: string
  detail?: ImageDetail
}

export function wireAttachments(): WireAttachment[] {
  return fileDrop.files.map(f => ({
    name: f.name,
    type: f.type,
    size: f.size,
    text_content: f.text_content,
    data_base64: f.data_base64,
    detail: f.isImage ? f.detail : undefined
  }))
}

let dragging = 0
let installed = false

/* The window listens once: dragenter/dragleave fire for every child element, so
   the counter keeps the veil from flickering as the pointer crosses rows. Two
   components call this, hence the guard. */
export function installDropZone(): void {
  if (installed) return
  installed = true
  const hasFiles = (e: DragEvent) =>
    Array.from(e.dataTransfer?.types ?? []).includes('Files')

  const onEnter = (e: DragEvent) => {
    if (!hasFiles(e)) return
    e.preventDefault()
    dragging += 1
    fileDrop.over = true
  }
  const onOver = (e: DragEvent) => {
    if (!hasFiles(e)) return
    e.preventDefault()
    if (e.dataTransfer) e.dataTransfer.dropEffect = 'copy'
  }
  const onLeave = (e: DragEvent) => {
    if (!hasFiles(e)) return
    dragging = Math.max(0, dragging - 1)
    if (dragging === 0) fileDrop.over = false
  }
  const onDrop = (e: DragEvent) => {
    if (!hasFiles(e)) return
    e.preventDefault()
    dragging = 0
    fileDrop.over = false
    const files = Array.from(e.dataTransfer?.files ?? [])
    if (files.length) void ingest(files)
  }

  document.addEventListener('dragenter', onEnter, true)
  document.addEventListener('dragover', onOver, true)
  document.addEventListener('dragleave', onLeave, true)
  document.addEventListener('drop', onDrop, true)
}
