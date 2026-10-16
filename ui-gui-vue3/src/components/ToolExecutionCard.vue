<script setup lang="ts">
import { ref, computed, watch, nextTick, onUnmounted } from 'vue'
import type { ToolStatus } from '../types/agentChat'
import { diffLines } from 'diff'
import hljs from 'highlight.js'
import { IncrementalHighlighter, type HighlightUpdate } from '../utils/incrementalHighlight'

const p = defineProps<{
  toolName: string
  status: ToolStatus
  files?: string[]
  input?: string
  output?: string
  error?: string
  duration?: number
  defaultOpen?: boolean
}>()

const e = ref(p.defaultOpen || false)
const br = ref<HTMLDivElement | null>(null)
const bh = ref('auto')
const showDiff = ref(false)

const ds = computed(() => p.status)
const ic = computed(() =>
  ds.value === 'failed' || ds.value === 'cancelled'
    ? 'var(--error)'
    : ds.value === 'completed'
      ? 'var(--success)'
      : 'var(--accent)'
)

// ===== 语言映射 =====
const EXT_LANG: Record<string, string> = {
  ts: 'typescript', tsx: 'typescript', js: 'javascript', jsx: 'javascript',
  vue: 'vue', py: 'python', java: 'java', go: 'go', rs: 'rust',
  cpp: 'cpp', c: 'c', h: 'c', css: 'css', scss: 'scss',
  html: 'html', json: 'json', yaml: 'yaml', yml: 'yaml',
  md: 'markdown', sql: 'sql', sh: 'bash', xml: 'xml',
}

// ===== 解析 edit_file / write_file 的 JSON input =====
interface ParsedFileInput {
  filePath: string
  content: string
  language: string
  otherFields: Record<string, string>
}

function parseFileInput(input: string, toolName: string, status: ToolStatus): ParsedFileInput | null {
  if (toolName !== 'edit_file' && toolName !== 'write_file') return null
  if (!input) return null
  try {
    const parsed = JSON.parse(input)
    const filePath = parsed.file_path || parsed.path || ''
    const content = parsed.content || parsed.new_string || ''
    if (!filePath && !content) return null
    const ext = filePath.split('.').pop()?.toLowerCase() || ''
    const language = EXT_LANG[ext] || ext || 'plaintext'
    const otherFields: Record<string, string> = {}
    for (const [k, v] of Object.entries(parsed)) {
      if (!['file_path', 'path', 'content', 'new_string', 'old_string'].includes(k)) {
        otherFields[k] = String(v)
      }
    }
    return { filePath, content, language, otherFields }
  } catch {
    if (status === 'streaming' || status === 'running') {
      const fm = input.match(/"file_path"\s*:\s*"([^"]*)"/)
      const filePath = fm ? fm[1] : ''
      const ext = filePath.split('.').pop()?.toLowerCase() || ''
      const language = EXT_LANG[ext] || ext || 'plaintext'
      return { filePath, content: input, language, otherFields: {} }
    }
    return null
  }
}

// ===== Diff 计算 =====
interface DiffLine {
  type: 'added' | 'removed' | 'unchanged'
  content: string
  oldLine: string
  newLine: string
}

function computeDiff(input: string, output: string, toolName: string): DiffLine[] | null {
  if (toolName !== 'edit_file') return null
  if (!input) return null
  try {
    const parsed = JSON.parse(input)
    const oldStr = parsed.old_string || parsed.old_text || ''
    const newStr = parsed.new_string || parsed.new_text || parsed.content || ''
    if (!oldStr && !newStr) return null

    // 从 tool output 中提取真实行号
    let startLine = 1
    if (output) {
      const m = output.match(/Line:\s*(\d+)/)
      if (m) startLine = parseInt(m[1], 10)
    }

    const changes = diffLines(oldStr, newStr)
    const lines: DiffLine[] = []

    // 先计算总行数，确定 padding 宽度
    let totalLines = 0
    for (const change of changes) {
      totalLines += change.value.replace(/\n$/, '').split('\n').length
    }
    const pad = Math.max(3, String(startLine + totalLines).length)

    let oldLineNum = startLine
    let newLineNum = startLine

    for (const change of changes) {
      const lineStrs = change.value.replace(/\n$/, '').split('\n')
      for (const val of lineStrs) {
        if (change.added) {
          lines.push({ type: 'added', content: val, oldLine: ''.padStart(pad), newLine: String(newLineNum++).padStart(pad) })
        } else if (change.removed) {
          lines.push({ type: 'removed', content: val, oldLine: String(oldLineNum++).padStart(pad), newLine: ''.padStart(pad) })
        } else {
          lines.push({ type: 'unchanged', content: val, oldLine: String(oldLineNum++).padStart(pad), newLine: String(newLineNum++).padStart(pad) })
        }
      }
    }
    return lines
  } catch {
    return null
  }
}

// ===== 语法高亮 =====
function highlightCode(code: string, language: string): string {
  try {
    if (language && hljs.getLanguage(language)) {
      return hljs.highlight(code, { language, ignoreIllegals: true }).value
    }
    return hljs.highlightAuto(code).value
  } catch {
    return EscapeHtml(code)
  }
}

function EscapeHtml(s: string): string {
  return s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
}

// ===== 解析结果 =====
const parsedInput = computed(() => parseFileInput(p.input || '', p.toolName, p.status))
const diff = computed(() => {
  if (!showDiff.value) return null
  return computeDiff(p.input || '', p.output || '', p.toolName)
})
const highlighted = computed(() => {
  if (!parsedInput.value?.content) return ''
  return highlightCode(parsedInput.value.content, parsedInput.value.language)
})

// ===== 流式增量高亮（大参数场景性能优化） =====
// 背景：流式阶段每个 chunk 曾对"全量累积参数"做一次全量高亮 + 全量 DOM 替换，
// 成本 O(n²)。大文件工具参数（write_file 写几百 KB）场景下前端处理速率跌破
// LLM 生成速率，事件无限积压，message.complete 排在队尾——界面一直 processing。
// 方案：流式阶段由增量引擎接管 code-body 渲染——prefix 区缓存（仅 S 前移时重算）
// + window 区（O(W)）每 chunk 重算，视觉结果与全量渲染严格一致（差分测试保障）。
// 完成态 / 不支持的语言 / 任何异常 → 自动回退全量路径，最终显示与旧行为一致。

const incActive = ref(false)
const incBodyEl = ref<HTMLPreElement | null>(null)
let incEngine: IncrementalHighlighter | null = null
let incEngineLang = ''
let incPrefixEl: HTMLElement | null = null
let incWindowEl: HTMLElement | null = null
let incLastInput = ''

function applyIncDom(res: HighlightUpdate): void {
  const host = incBodyEl.value
  if (!host) return
  let rebuilt = false
  if (!incPrefixEl || incPrefixEl.parentNode !== host || !incWindowEl || incWindowEl.parentNode !== host) {
    host.innerHTML = ''
    incPrefixEl = document.createElement('span')
    incWindowEl = document.createElement('span')
    host.appendChild(incPrefixEl)
    host.appendChild(incWindowEl)
    rebuilt = true
  }
  if (res.prefixChanged || rebuilt) incPrefixEl!.innerHTML = res.prefixHtml
  incWindowEl!.innerHTML = res.windowHtml
}

function teardownIncremental(): void {
  incActive.value = false
  incEngine = null
  incEngineLang = ''
  incLastInput = ''
  incPrefixEl = null
  incWindowEl = null
}

async function updateIncremental(): Promise<void> {
  const input = p.input || ''
  const isStreaming = p.status === 'streaming' || p.status === 'running'
  const lang = parsedInput.value?.language || ''

  if (!isStreaming || !lang || !input) {
    if (incActive.value) teardownIncremental()
    return
  }

  if (!incEngine || incEngineLang !== lang) {
    incEngine = new IncrementalHighlighter(lang)
    incEngineLang = lang
    incLastInput = ''
    if (!incEngine.supported) {
      teardownIncremental()
      return
    }
    incActive.value = true
  }
  if (!incEngine) return
  if (input === incLastInput) return

  const res = incEngine.update(input)
  if (!res) {
    teardownIncremental()
    return
  }
  incLastInput = input
  await nextTick()
  applyIncDom(res)
}

watch(
  [() => p.input, () => p.status],
  () => { void updateIncremental() },
  { flush: 'post' },
)

onUnmounted(() => { teardownIncremental() })

// ===== 状态文本 =====
function st(n: string, s: ToolStatus): string {
  if (s === 'running') {
    switch (n) {
      case 'web_search': return 'searching...'
      case 'browser_navigate': return 'loading...'
      case 'fetch_url': return 'fetching...'
      default: return 'running'
    }
  }
  if (s === 'completed') {
    switch (n) {
      case 'web_search': return 'done'
      case 'browser_navigate': return 'loaded'
      default: return 'completed'
    }
  }
  switch (s) {
    case 'streaming': return 'streaming'
    case 'failed': return 'failed'
    case 'cancelled': return 'cancelled'
    default: return ''
  }
}

function fd(ms: number): string {
  if (ms < 1000) return `${ms}ms`
  if (ms < 60000) return `${(ms / 1000).toFixed(1)}s`
  return `${Math.floor(ms / 60000)}m ${Math.floor((ms % 60000) / 1000)}s`
}

// ===== 折叠动画 =====
function tg() {
  const b = br.value
  if (!b) { e.value = !e.value; return }
  if (e.value) {
    const h = b.scrollHeight
    bh.value = h + 'px'
    requestAnimationFrame(() => bh.value = '0px')
    setTimeout(() => e.value = false, 350)
  } else {
    bh.value = b.scrollHeight + 'px'
    e.value = true
  }
}
</script>

<template>
  <div class="tec" :class="ds">
    <div class="tec-inner" :class="{ 'is-open': e }">
      <!-- Header -->
      <div class="tec-hdr" @click="tg">
        <div class="tec-icon" :style="{ borderColor: ic, color: ic }">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"
            stroke-linejoin="round">
            <path
              d="M14.7 6.3a1 1 0 0 0 0 1.4l1.6 1.6a1 1 0 0 0 1.4 0l3.77-3.77a6 6 0 0 1-7.94 7.94l-6.91 6.91a2.12 2.12 0 0 1-3-3l6.91-6.91a6 6 0 0 1 7.94-7.94l-3.76 3.76z" />
          </svg>
        </div>
        <div class="tec-info">
          <span class="tec-name">{{ toolName }}</span>
          <span v-if="ds === 'streaming'" class="tec-dur">streaming params...</span>
          <span v-else-if="ds === 'running'" class="tec-dur tec-dur-live">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
              <circle cx="12" cy="12" r="10" />
              <polyline points="12 6 12 12 16 14" />
            </svg>
            {{ duration ? fd(duration) : 'running' }}
          </span>
          <span v-else-if="duration" class="tec-dur">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
              <circle cx="12" cy="12" r="10" />
              <polyline points="12 6 12 12 16 14" />
            </svg>
            {{ fd(duration) }}
          </span>
        </div>
        <span class="tec-badge" :class="ds"><span class="tec-dot" />{{ st(toolName, ds) }}</span>
        <span class="tec-arrow" :class="{ open: e }">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
            <polyline points="6 9 12 15 18 9" />
          </svg>
        </span>
      </div>

      <!-- Body -->
      <div ref="br" class="tec-body" :style="{ maxHeight: e ? bh : '0px' }">
        <div class="tec-body-inner">
          <div class="tec-divider" />

          <!-- Files -->
          <div v-if="files && files.length > 0" class="tec-sec">
            <div class="tec-sec-label">
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                <path d="M14 2H6a2 2 0 00-2 2v16a2 2 0 002 2h12a2 2 0 002-2V8z" />
                <polyline points="14 2 14 8 20 8" />
              </svg>
              Files
            </div>
            <div class="tec-code">
              <div v-for="(f, i) in files" :key="i" class="tec-file-item">
                <span class="tec-file-icon">{{ f.split('.').pop()?.toUpperCase() || '?' }}</span>
                <span>{{ f }}</span>
              </div>
            </div>
          </div>

          <!-- Input (structured for edit_file / write_file) -->
          <template v-if="parsedInput">
            <div class="tec-sec">
              <div class="tec-sec-label">
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                  <rect x="3" y="3" width="18" height="18" rx="2" />
                  <line x1="3" y1="9" x2="21" y2="9" />
                  <line x1="9" y1="21" x2="9" y2="9" />
                </svg>
                Input
              </div>
              <div class="tec-code">
                <!-- NEW FILE badge -->
                <div v-if="toolName === 'write_file'" class="tec-newfile"><span>&#10022;</span><span>NEW FILE</span></div>
                <!-- file_path -->
                <div class="tec-input-block">
                  <div class="tec-input-field">
                    <span class="tec-input-label">file_path:</span>
                    <span class="tec-input-val">{{ parsedInput.filePath }}</span>
                  </div>
                  <div v-for="(v, k) in parsedInput.otherFields" :key="k" class="tec-input-field">
                    <span class="tec-input-label">{{ k }}:</span>
                    <span class="tec-input-val">{{ v }}</span>
                  </div>
                </div>
                <!-- content with highlighting -->
                <div v-if="parsedInput.content" class="tec-code-sec">
                  <div class="tec-code-hdr">
                    <span>content</span>
                    <span class="tec-code-lang">{{ parsedInput.language }}</span>
                  </div>
                  <!-- 流式增量渲染：prefix 区 + window 区双容器（手动管理内部 DOM） -->
                  <pre v-if="incActive" ref="incBodyEl" class="tec-code-body" />
                  <!-- 全量渲染（完成态 / 不支持的语言 / 引擎异常回退） -->
                  <pre v-else class="tec-code-body" v-html="highlighted" />
                </div>
              </div>
            </div>

            <!-- Diff (only for edit_file) -->
            <div v-if="toolName === 'edit_file'" class="tec-sec">
              <div class="tec-sec-label">
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                  <path d="M12 3v18M3 12h18" />
                  <polyline points="8 8 12 4 16 8" />
                  <polyline points="8 16 12 20 16 16" />
                </svg>
                Diff
                <button class="tec-diff-toggle" @click="(e: MouseEvent) => { e.stopPropagation(); showDiff = !showDiff }">
                  {{ showDiff ? 'Hide' : 'View Diff' }}
                </button>
              </div>
              <div v-if="diff" class="tec-code tec-diff-view">
                <div v-for="(line, i) in diff" :key="i" class="tec-diff-row" :class="line.type">
                  <span class="tec-diff-old">{{ line.oldLine }}</span>
                  <span class="tec-diff-new">{{ line.newLine }}</span>
                  <span class="tec-diff-prefix">{{ line.type === 'added' ? '+' : line.type === 'removed' ? '-' : ' ' }}</span>
                  <span class="tec-diff-text">{{ line.content }}</span>
                </div>
              </div>
            </div>
          </template>

          <!-- Input (plain, for other tools) -->
          <div v-else-if="input" class="tec-sec">
            <div class="tec-sec-label">
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                <rect x="3" y="3" width="18" height="18" rx="2" />
                <line x1="3" y1="9" x2="21" y2="9" />
                <line x1="9" y1="21" x2="9" y2="9" />
              </svg>
              Input
            </div>
            <pre class="tec-code">{{ input }}</pre>
          </div>

          <!-- Output -->
          <div v-if="output || ds === 'running' || ds === 'streaming'" class="tec-sec">
            <div class="tec-sec-label">
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                <polyline points="16 3 21 3 21 8" />
                <line x1="4" y1="20" x2="21" y2="3" />
                <polyline points="21 16 21 21 16 21" />
                <line x1="15" y1="15" x2="21" y2="21" />
                <line x1="4" y1="4" x2="9" y2="9" />
              </svg>
              Output
            </div>
            <pre v-if="output" class="tec-code tec-out">{{ p.toolName === 'read_file' ? 'Content read' : output }}</pre>
            <div v-else class="tec-waiting">
              <span>waiting for output</span>
              <span class="tec-wait-dots"><span /><span /><span /></span>
            </div>
          </div>

          <!-- Error -->
          <div v-if="error" class="tec-sec">
            <div class="tec-sec-label">
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                <circle cx="12" cy="12" r="10" />
                <line x1="12" y1="8" x2="12" y2="12" />
                <line x1="12" y1="16" x2="12.01" y2="16" />
              </svg>
              Error
            </div>
            <pre class="tec-code tec-err">{{ error }}</pre>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>
