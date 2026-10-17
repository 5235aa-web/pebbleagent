/**
 * incrementalHighlight.ts — 流式高亮增量渲染引擎
 *
 * ## 背景
 * ToolExecutionCard 在流式阶段每个 chunk 都对"全量累积参数"做一次
 * 全量 hljs 高亮 + 全量 DOM 替换，成本 O(n²)。大文件工具参数场景下
 * （write_file 写几百 KB 文件）前端处理速率跌破 LLM 生成速率，事件无限
 * 积压，message.complete 永远排在队尾，表现为"前端一直 processing"。
 *
 * ## 原理（已实验验证）
 * hljs 是确定性线性扫描器：位置 i 的高亮输出只依赖 text[0..i]（前缀稳定性）。
 * 因此把文本切为两区：
 * - prefix 区：text[0..S]，完整高亮一次并缓存（仅 S 前移时重算）
 * - window 区：text[S..]，每 chunk 重算（O(W)）。高亮时用"伪造前缀"
 *   复现 S 处的结构状态（如伪造一个 `"` 表示"在字符串内"），输出剥掉
 *   前缀部分后与 prefix 区拼接——视觉结果与全量高亮严格一致
 *   （以"字符序列 + 颜色 class 链"为标准的视觉等价，已实验验证）。
 *
 * ## 安全设计（三重兜底）
 * 1. 语言不在白名单 → supported=false，调用方回退全量渲染（不劣化）
 * 2. 任何异常 / 剥前缀失败 → disabled=true，调用方回退全量渲染
 * 3. 完成态由调用方切回全量渲染（现有机制），最终 DOM 与旧行为完全一致
 *
 * ## 性能（实测）
 * 219KB 文本 / 5609 chunk：平均 0.83ms/chunk（现状同规模约 50ms/chunk）
 */

import hljs from 'highlight.js'

// ===== 类型定义 =====

/** 打开中的多行/单行结构 */
interface OpenStruct {
  /** 结构开始标记（用于伪造前缀复现状态） */
  begin: string
  /** 结构结束标记；null 表示只能由换行结束（行注释） */
  end: string | null
  /** 单行结构（换行会结束它）：字符串字面量 / 行注释 */
  isLine: boolean
}

/** 文本某位置处的扫描状态 */
export interface ScanState {
  /** 当前打开的结构；null = 干净（可安全作为切点） */
  open: OpenStruct | null
  /** 切点前存在未完成的转义（切点落在转义序列中间） */
  escapePending: boolean
}

/** 引擎每次更新的输出 */
export interface HighlightUpdate {
  /** prefix 区 HTML（仅在 prefixChanged=true 时需要写 DOM） */
  prefixHtml: string
  /** window 区 HTML（每 chunk 更新） */
  windowHtml: string
  /** prefix 区是否变化 */
  prefixChanged: boolean
}

/** 语言配置 */
interface LangConfig {
  /** 多行结构对：[开始, 结束] */
  blockPairs: Array<[string, string]>
  /** 行注释前缀（null = 无） */
  lineComment: string | null
  /** 单行字符串引号 */
  lineStrings: string[]
  /** 转义符 */
  escape: string
}

// ===== 语言白名单配置 =====
// 仅收录"字符串/注释为开-闭模式（begin/end 分离），可被状态机精确扫描"的语言。
// - 其余语言（vue/markdown/html/yaml 等）走全量渲染，不劣化。
// - css 不收录：hljs 的 css 字符串为"完整匹配式"（要求成对引号），
//   流式未闭合场景下"伪造前缀"无法复现其状态，会带来中间态视觉差异，
//   因此走全量渲染（正确性优先）。

const LANG_CONFIGS: Record<string, LangConfig> = {
  python: {
    blockPairs: [['"""', '"""'], ["'''", "'''"]],
    lineComment: '#',
    lineStrings: ['"', "'"],
    escape: '\\',
  },
  javascript: {
    blockPairs: [['/*', '*/'], ['`', '`']],
    lineComment: '//',
    lineStrings: ['"', "'"],
    escape: '\\',
  },
  typescript: {
    blockPairs: [['/*', '*/'], ['`', '`']],
    lineComment: '//',
    lineStrings: ['"', "'"],
    escape: '\\',
  },
  json: {
    blockPairs: [],
    lineComment: null,
    lineStrings: ['"'],
    escape: '\\',
  },
  bash: {
    blockPairs: [['`', '`']],
    lineComment: '#',
    lineStrings: ['"', "'"],
    escape: '\\',
  },
  plaintext: {
    blockPairs: [],
    lineComment: null,
    lineStrings: [],
    escape: '',
  },
}

const CLEAN_STATE: ScanState = { open: null, escapePending: false }

/** 计算 upto 处的转义悬置状态（从 upto-1 向左数连续转义符） */
function computeEscapePending(text: string, upto: number, escape: string): boolean {
  if (!escape || upto <= 0 || text[upto - 1] !== escape) return false
  let k = upto - 1
  let cnt = 0
  while (k >= 0 && text[k] === escape) {
    cnt++
    k--
  }
  return cnt % 2 === 1
}

/**
 * 从 from（状态已知，由 state 提供）扫描到 upto，返回 upto 处的状态。
 * 自愈式设计：不做跨 chunk 的状态延续假设，调用方负责提供正确的起始状态。
 */
export function scanStateAt(
  text: string,
  from: number,
  upto: number,
  cfg: LangConfig,
  state: ScanState,
): ScanState {
  let i = from
  let open = state.open

  // 跨窗口转义：上次扫描以"未配对的 \`"结束时，本窗口首字符是该转义的目标——
  // 必须跳过它，否则目标字符（如引号）会被误判为"未转义"导致结构错误终止。
  if (state.escapePending && i < upto) {
    i += 1
  }

  while (i < upto) {
    if (open) {
      const ch = text[i]
      if (ch === '\n') {
        if (open.isLine) {
          open = null
          i++
          continue
        }
        i++
        continue
      }
      if (cfg.escape && ch === cfg.escape) {
        i += 2
        continue
      }
      if (open.end !== null && text.startsWith(open.end, i)) {
        const endLen = open.end.length
        open = null
        i += endLen
        continue
      }
      i++
      continue
    }

    // ===== 干净态 =====
    // 行注释
    if (cfg.lineComment && text.startsWith(cfg.lineComment, i)) {
      const nl = text.indexOf('\n', i)
      if (nl === -1) {
        // 注释延伸到扫描边界 —— 状态 = 在注释内
        return {
          open: { begin: cfg.lineComment, end: null, isLine: true },
          escapePending: false,
        }
      }
      i = nl + 1
      continue
    }

    // 多行结构
    let matchedBlock = false
    for (const [begin, end] of cfg.blockPairs) {
      if (text.startsWith(begin, i)) {
        open = { begin, end, isLine: false }
        i += begin.length
        matchedBlock = true
        break
      }
    }
    if (matchedBlock) continue

    // 单行字符串
    let matchedStr = false
    for (const q of cfg.lineStrings) {
      if (text.startsWith(q, i)) {
        let j = i + q.length
        let closed = false
        let hitNL = false
        while (j < upto) {
          if (cfg.escape && text[j] === cfg.escape) {
            j += 2
            continue
          }
          if (text.startsWith(q, j)) {
            closed = true
            break
          }
          if (text[j] === '\n') {
            hitNL = true
            break
          }
          j++
        }
        if (closed) {
          i = j + q.length
          matchedStr = true
          break
        }
        if (hitNL) {
          i = j + 1
          matchedStr = true
          break
        }
        // 字符串延伸到扫描边界 —— 状态 = 在该字符串内
        return {
          open: { begin: q, end: q, isLine: true },
          escapePending: computeEscapePending(text, upto, cfg.escape),
        }
      }
    }
    if (matchedStr) continue

    i++
  }

  return {
    open,
    escapePending: computeEscapePending(text, upto, cfg.escape),
  }
}

/** 剥离 HTML 末尾的闭合标签序列（"伪闭合"，hljs 对未闭合结构的 flush 产物） */
function stripTrailingCloses(html: string): string {
  let out = html
  while (out.endsWith('</span>')) out = out.slice(0, -7)
  return out
}

/** 伪造前缀：复现切点处结构状态的最小文本 */
function fakePrefixFor(state: ScanState): string {
  return state.open ? state.open.begin : ''
}

// ===== 引擎 =====

export interface IncrementalHighlighterOptions {
  /** 窗口大小（字符数）。越大：单次成本越高、prefix 重算越少。默认 8K */
  windowSize?: number
}

/**
 * 增量高亮引擎。
 *
 * 使用方式（DOM 侧配合两个容器元素）：
 * ```ts
 * const engine = new IncrementalHighlighter(language)
 * const res = engine.update(accumulatedText)
 * if (res) {
 *   if (res.prefixChanged) prefixEl.innerHTML = res.prefixHtml
 *   windowEl.innerHTML = res.windowHtml
 * } else {
 *   // 回退全量渲染
 * }
 * ```
 */
export class IncrementalHighlighter {
  private lang: string
  private cfg: LangConfig | null
  private windowSize: number

  /** 窗口起点（prefix 区与 window 区的分界） */
  private S = 0
  /** prefix 区 HTML（已按需剥离伪闭合） */
  private prefixHtml = ''
  /** S 处的扫描状态 */
  private stateAtS: ScanState = CLEAN_STATE
  /** 扫描进度：已扫描到的位置及其状态 */
  private scanPos = 0
  private scanState: ScanState = CLEAN_STATE
  /** 上次接收的文本长度（检测缩短） */
  private lastLen = 0
  /** 引擎被禁用（异常/剥离失败 → 调用方回退全量） */
  private disabled = false

  constructor(language: string, opts?: IncrementalHighlighterOptions) {
    this.lang = language
    this.cfg = LANG_CONFIGS[language] ?? null
    this.windowSize = opts?.windowSize ?? 8 * 1024
  }

  /** 引擎是否可用（语言白名单内且未禁用） */
  get supported(): boolean {
    return this.cfg !== null && !this.disabled
  }

  /** 重置（用于组件复用/重建） */
  reset(): void {
    this.S = 0
    this.prefixHtml = ''
    this.stateAtS = CLEAN_STATE
    this.scanPos = 0
    this.scanState = CLEAN_STATE
    this.lastLen = 0
    this.disabled = false
  }

  /** 增量推进扫描进度到 to */
  private advanceScan(text: string, to: number): void {
    if (!this.cfg || to <= this.scanPos) return
    this.scanState = scanStateAt(text, this.scanPos, to, this.cfg, this.scanState)
    this.scanPos = to
  }

  /** 查询位置 pos 的状态（要求 pos <= scanPos） */
  private stateAtPos(pos: number): ScanState {
    // S 始终等于扫描进度上最近一次前移的目标，因此直接返回缓存
    if (pos === this.S) return this.stateAtS
    return this.scanState
  }

  /**
   * 用新的累积文本更新。
   * @returns 更新结果；null 表示引擎不可用（调用方应回退全量渲染）
   */
  update(text: string): HighlightUpdate | null {
    if (!this.cfg || this.disabled) return null

    try {
      // 文本缩短（非流式场景的防御）→ 重置后从头处理
      if (text.length < this.lastLen) this.reset()
      this.lastLen = text.length

      let prefixChanged = false

      // ===== 1. S 前移检查 =====
      if (text.length - this.S > this.windowSize) {
        let targetS = text.length - Math.floor(this.windowSize / 2)
        this.advanceScan(text, targetS)
        if (this.scanState.escapePending) {
          targetS += 1
          this.advanceScan(text, targetS)
        }
        if (targetS > text.length) targetS = text.length
        this.S = targetS
        this.stateAtS = { ...this.scanState }

        // prefix 区重算（含伪闭合剥离：S 处若有打开结构，hljs 会 flush 出伪闭合标签）
        const ph = hljs.highlight(text.slice(0, this.S), {
          language: this.lang,
          ignoreIllegals: true,
        }).value
        this.prefixHtml = this.stateAtS.open ? stripTrailingCloses(ph) : ph
        prefixChanged = true
      }

      // ===== 2. 窗口高亮 =====
      const st = this.stateAtPos(this.S)
      const fakePrefix = fakePrefixFor(st)
      const seg = text.slice(this.S)
      const winRaw = hljs.highlight(fakePrefix + seg, {
        language: this.lang,
        ignoreIllegals: true,
      }).value

      let winHtml = winRaw
      if (fakePrefix) {
        const pOpen = stripTrailingCloses(
          hljs.highlight(fakePrefix, { language: this.lang, ignoreIllegals: true }).value,
        )
        if (winRaw.startsWith(pOpen)) {
          // 剥掉伪造前缀的"字符部分"，保留其开放标签：
          // 组件侧 prefix 容器末尾存在未闭合结构（被解析器自动闭合），
          // window 容器以相同标签"重开"该结构，使窗口内容在视觉上延续
          // 前缀末尾的结构颜色（相邻同色 span，视觉签名归一化后与全量一致）。
          const lastGt = pOpen.lastIndexOf('>')
          const tags = lastGt === -1 ? '' : pOpen.slice(0, lastGt + 1)
          winHtml = tags + winRaw.slice(pOpen.length)
        } else {
          // 剥前缀失败（扫描状态与 hljs 不一致）→ 保守禁用，调用方回退全量
          this.disabled = true
          return null
        }
      }

      return { prefixHtml: this.prefixHtml, windowHtml: winHtml, prefixChanged }
    } catch {
      this.disabled = true
      return null
    }
  }
}
