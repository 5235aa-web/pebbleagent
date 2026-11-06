/* GitHub repository panel state.

   Nothing here talks to api.github.com: the CSP keeps the renderer on
   localhost, so every call goes through the gateway's `github.*` domain and the
   token never leaves the backend. What this module owns is the read side — the
   repository rack, the open repository's README and languages, and the three
   secondary lists — plus the race guard that keeps a slow answer from an
   abandoned repository from landing on top of a newer one. */

import { reactive, watch } from 'vue'
import { gateway } from './gateway'
import { openExternalUrl } from './wire'
import { projects } from './store'

export interface GhRate {
  limit: number
  remaining: number
  reset: number
}

export interface GhRepo {
  name: string
  fullName: string
  private: boolean
  description: string
  language: string
  stars: number
  forks: number
  openIssues: number
  pushedAt: string
  branch: string
  url: string
  topics: string[]
  size: number
}

export interface GhCommit {
  /** Full forty-character sha, the value `github.commit` accepts. */
  sha: string
  /** First seven characters, the value the row prints. */
  shortSha: string
  message: string
  author: string
  date: string
  avatar: string
}

/** One commit's diff, fetched on demand: the listing endpoint carries no stats
    and no patches, so a row shows its numbers only after it is opened. */
export interface GhCommitDiff {
  sha: string
  stats: { additions: number; deletions: number; total: number }
  files: GhPullFile[]
  truncated: boolean
  loading: boolean
  error: string
}

export interface GhEntry {
  name: string
  path: string
  type: 'dir' | 'file'
  size: number
}

export interface GhFile {
  path: string
  name: string
  size: number
  binary: boolean
  text: string
}

export interface GhLabel {
  name: string
  color: string
}

export interface GhIssue {
  number: number
  title: string
  state: string
  user: string
  comments: number
  updatedAt: string
  url: string
  labels: GhLabel[]
  draft: boolean
}

export interface GhDetail {
  repo: GhRepo
  readme: string
  languages: Record<string, number>
}

export type GhView = 'readme' | 'files' | 'file' | 'commits' | 'activity'

export interface GhPullFile {
  filename: string
  status: string
  additions: number
  deletions: number
  /** Unified diff hunk text. Empty when GitHub refuses to render one. */
  patch: string
  /** GitHub omits `patch` for binary files and for diffs past its own limit. */
  noPatch: boolean
}

export interface GhPullFiles {
  files: GhPullFile[]
  /** True when the list is capped or any file arrived without a patch, which
      is what the panel means by "some changes are not shown". */
  truncated: boolean
  loading: boolean
  error: string
}

export const gh = reactive({
  /** False until the first status answer, so the panel can tell "not loaded"
      from "no token". */
  ready: false,
  loading: false,
  error: '',
  configured: false,
  login: '',
  avatar: '',
  repoCount: 0,
  rate: { limit: 0, remaining: 0, reset: 0 } as GhRate,
  repos: [] as GhRepo[],
  query: '',
  /** Page of the last repository listing; 0 means nothing has been fetched. */
  reposPage: 0,
  /** True when GitHub reported another page. Without it the rack silently
      stopped at the first 100 repositories. */
  reposHasMore: false,
  reposLoadingMore: false,
  /** fullName of the repository on screen; empty means the rack is showing. */
  active: '',
  detail: null as GhDetail | null,
  detailLoading: false,
  detailError: '',
  entries: [] as GhEntry[],
  dirPath: '',
  commits: [] as GhCommit[],
  issues: [] as GhIssue[],
  pulls: [] as GhIssue[],
  view: 'readme' as GhView,
  sideLoading: false,
  /** The open file, or null while the tree is showing. `binary` files carry no
      text: the backend refuses to decode them rather than shipping mojibake. */
  file: null as GhFile | null,
  fileLoading: false,
  fileError: '',
  /** `owner/name` of the checkout the active project points at, or empty when
      it is not a GitHub clone. Drives the "current" badge on the rack. */
  localRepo: '',
  localBranch: '',
  localAhead: 0,
  /** Files with uncommitted work. `localAhead` only counts commits, which is 0
      for an afternoon of edits that were never committed — the exact case that
      made the panel claim "in sync" while twelve files were dirty. */
  localDirty: 0,
  /** 52 weeks x 7 days of commit counts, oldest first. Empty while GitHub is
      still computing the table (it answers 202 the first time). */
  weeks: [] as number[][],
  weeksPending: false,
  /** Pull request whose diff is expanded, or 0. */
  openPull: 0,
  pullFiles: {} as Record<number, GhPullFiles>,
  /** Commit whose diff is expanded, or empty. */
  openCommit: '',
  commitDiffs: {} as Record<string, GhCommitDiff>,
})

/* Two counters, not one: switching repositories and switching directories are
   independent races, and a single counter would make the directory answer of
   the new repository look stale. */
let repoSeq = 0
let dirSeq = 0
let fileSeq = 0

export function filteredRepos(): GhRepo[] {
  const q = gh.query.trim().toLowerCase()
  if (!q) return gh.repos
  return gh.repos.filter(r =>
    r.name.toLowerCase().includes(q) ||
    r.description.toLowerCase().includes(q) ||
    r.language.toLowerCase().includes(q) ||
    r.topics.some(t => t.toLowerCase().includes(q))
  )
}

export async function loadStatus(force = false): Promise<void> {
  if (gh.loading) return
  /* A rejected call sets `ready` so the panel can leave the skeleton, so the
     guard has to key on success rather than on the flag alone: otherwise the
     retry that the connection handler fires would be swallowed. */
  if (gh.ready && !gh.error && !force) return
  gh.loading = true
  gh.error = ''
  try {
    const res = await gateway.request<{
      configured: boolean
      login: string
      avatar: string
      repos: number
      rate: GhRate
      error?: string
    }>('github.status')
    gh.configured = res.configured
    gh.login = res.login
    gh.avatar = res.avatar
    gh.repoCount = res.repos
    gh.rate = res.rate || { limit: 0, remaining: 0, reset: 0 }
    gh.error = res.error || ''
    gh.ready = true
    /* Local first: the badge is the cheapest thing on the panel and it makes
       the rack answer "which one is mine" before any repository is opened. */
    void loadLocal()
  } catch (e) {
    /* `ready` has to be set on this path too. Leaving it false on a rejected
       call pinned the panel on the skeleton forever, which reads as "still
       loading" and hides the reason. */
    gh.error = e instanceof Error ? e.message : String(e)
    gh.ready = true
  } finally {
    gh.loading = false
  }
  if (gh.configured && !gh.repos.length && !gh.error) await loadRepos()
}

export async function loadRepos(more = false): Promise<void> {
  if (more && (!gh.reposHasMore || gh.reposLoadingMore)) return
  const page = more ? gh.reposPage + 1 : 1
  if (more) gh.reposLoadingMore = true
  else {
    gh.loading = true
    gh.error = ''
  }
  try {
    const res = await gateway.request<{ repos: GhRepo[]; rate: GhRate; hasMore: boolean }>(
      'github.repos', { perPage: 100, page }
    )
    const incoming = res.repos || []
    if (more) {
      /* Pages are disjoint by construction, but a repository pushed between two
         requests can shift the window and repeat an entry. */
      const seen = new Set(gh.repos.map(r => r.fullName))
      gh.repos = [...gh.repos, ...incoming.filter(r => !seen.has(r.fullName))]
    } else {
      gh.repos = incoming
    }
    gh.reposPage = page
    gh.reposHasMore = !!res.hasMore
    if (res.rate) gh.rate = res.rate
  } catch (e) {
    if (!more) gh.error = e instanceof Error ? e.message : String(e)
  } finally {
    gh.loading = false
    gh.reposLoadingMore = false
  }
}

/* READMEs are written for github.com, not for the transcript renderer. Two
   shapes break it, and both are extremely common in real files:

   - a badge row (`[![alt](img)](link)`) collapses into a chain of empty
     anchors, because the renderer has no `<img>` path and keeps the links;
   - `> quoted` lines carry the marker as literal text, since blockquotes are
     not part of the transcript's markdown subset.

   Stripping both here keeps `renderMarkdown` shared with the conversation,
   where changing its output would alter every message already on screen. */
export function cleanReadme(raw: string): string {
  return String(raw || '')
    .split('\n')
    .filter(line => {
      const t = line.trim()
      if (!t) return true
      if (/^(\[!\[[^\]]*\]\([^)]*\)\]\([^)]*\)\s*)+$/.test(t)) return false
      if (/^(\[[^\]]*\]\([^)]*\)\s*)+$/.test(t)) return false
      return true
    })
    .map(line => line.replace(/^(\s*)>\s?/, '$1'))
    .join('\n')
}

export async function openRepo(fullName: string, force = false): Promise<void> {
  const seq = ++repoSeq
  gh.active = fullName
  gh.detail = null
  gh.detailError = ''
  gh.detailLoading = true
  gh.entries = []
  gh.dirPath = ''
  gh.commits = []
  gh.issues = []
  gh.pulls = []
  gh.view = 'readme'
  gh.openPull = 0
  gh.openCommit = ''
  gh.commitDiffs = {}
  gh.file = null
  gh.fileError = ''
  gh.fileLoading = false
  fileSeq += 1
  try {
    const res = await gateway.request<GhDetail>('github.repo', { repo: fullName, force })
    if (seq !== repoSeq) return
    gh.detail = { ...res, readme: cleanReadme(res.readme || '') }
  } catch (e) {
    if (seq !== repoSeq) return
    gh.detailError = e instanceof Error ? e.message : String(e)
  } finally {
    if (seq === repoSeq) gh.detailLoading = false
  }
  if (seq === repoSeq) void loadSide(fullName, force)
  /* The Files view is one click away and its list is the only thing it shows,
     so the root listing is fetched with the repository rather than when the
     tab is first opened. */
  if (seq === repoSeq) void loadDir('', force)
  if (seq === repoSeq) void loadActivity(fullName, 0, force)
}

/** Re-reads the open repository, keeping the view and directory the user was
    on. `force` skips the gateway's ETag cache, which is the only way to see a
    commit someone else pushed since the panel opened. */
export async function refreshRepo(): Promise<void> {
  const fullName = gh.active
  if (!fullName) return
  const view = gh.view
  const dirPath = gh.dirPath
  await openRepo(fullName, true)
  gh.view = view
  if (view === 'files' && dirPath) void loadDir(dirPath, true)
}

export function closeRepo(): void {
  repoSeq += 1
  dirSeq += 1
  gh.active = ''
  gh.detail = null
  gh.detailError = ''
  gh.detailLoading = false
  gh.entries = []
  gh.dirPath = ''
  gh.commits = []
  gh.issues = []
  gh.pulls = []
}

/** The three lists a repository screen needs below the fold. They load
    together because each is one request and the panel shows counts for all of
    them; a failure in one must not blank the other two. */
export async function loadSide(fullName: string, force = false): Promise<void> {
  if (!fullName) return
  const seq = repoSeq
  gh.sideLoading = true
  const settle = async <T>(method: string): Promise<T | null> => {
    try {
      return await gateway.request<T>(method, { repo: fullName, force })
    } catch {
      return null
    }
  }
  const [commits, issues, pulls] = await Promise.all([
    settle<{ commits: GhCommit[] }>('github.commits'),
    settle<{ issues: GhIssue[] }>('github.issues'),
    settle<{ pulls: GhIssue[] }>('github.pulls'),
  ])
  if (seq !== repoSeq) return
  gh.commits = commits?.commits || []
  gh.issues = issues?.issues || []
  gh.pulls = pulls?.pulls || []
  gh.sideLoading = false
}

export async function loadDir(path: string, force = false): Promise<void> {
  const fullName = gh.active
  if (!fullName) return
  /* Walking into a directory means leaving the file that was on screen. */
  if (gh.view === 'file') closeFile()
  const seq = ++dirSeq
  try {
    const res = await gateway.request<{ entries: GhEntry[]; path: string }>('github.contents', {
      repo: fullName,
      path,
      force,
    })
    if (seq !== dirSeq) return
    gh.entries = res.entries || []
    gh.dirPath = res.path || ''
  } catch (e) {
    if (seq !== dirSeq) return
    gh.detailError = e instanceof Error ? e.message : String(e)
  }
}

const LANG_BY_EXT: Record<string, string> = {
  ts: 'typescript', tsx: 'tsx', js: 'javascript', jsx: 'jsx', mjs: 'javascript',
  py: 'python', vue: 'vue', rs: 'rust', go: 'go', java: 'java', kt: 'kotlin',
  c: 'c', h: 'c', cpp: 'cpp', cc: 'cpp', hpp: 'cpp', cs: 'csharp',
  rb: 'ruby', php: 'php', swift: 'swift', dart: 'dart', lua: 'lua',
  sh: 'bash', bat: 'batch', cmd: 'batch', ps1: 'powershell',
  json: 'json', yaml: 'yaml', yml: 'yaml', toml: 'toml', ini: 'ini',
  md: 'markdown', html: 'html', htm: 'html', css: 'css', scss: 'scss',
  sql: 'sql', xml: 'xml', txt: 'text', lock: 'text',
}

/** Extension to highlighter name. An unknown or missing one becomes plain
    text, which `renderCodeBlock` still frames and numbers. */
export function guessLang(path: string): string {
  const name = path.split('/').pop() || ''
  const dot = name.lastIndexOf('.')
  if (dot <= 0) return 'text'
  return LANG_BY_EXT[name.slice(dot + 1).toLowerCase()] || 'text'
}

export async function loadFile(path: string): Promise<void> {
  const fullName = gh.active
  if (!fullName) return
  const seq = ++fileSeq
  gh.fileLoading = true
  gh.fileError = ''
  gh.file = null
  /* The view switches before the answer arrives: leaving the tree on screen
     while a request is in flight made a slow file look like a dead click. */
  gh.view = 'file'
  try {
    const res = await gateway.request<{ file: GhFile }>('github.file', { repo: fullName, path })
    if (seq !== fileSeq) return
    gh.file = res.file
  } catch (e) {
    if (seq !== fileSeq) return
    gh.fileError = e instanceof Error ? e.message : String(e)
  } finally {
    if (seq === fileSeq) gh.fileLoading = false
  }
}

export function closeFile(): void {
  fileSeq += 1
  gh.file = null
  gh.fileError = ''
  gh.fileLoading = false
  if (gh.view === 'file') gh.view = 'files'
}

export function openExternal(url: string): void {
  if (!url) return
  void openExternalUrl(url)
}

/* ------------------------------------------------------------------ display */

const LANGUAGE_COLORS: Record<string, string> = {
  Python: '#3572a5',
  JavaScript: '#f1e05a',
  TypeScript: '#3178c6',
  Vue: '#41b883',
  Rust: '#dea584',
  CSS: '#563d7c',
  SCSS: '#c6538c',
  HTML: '#e34c26',
  Shell: '#89e051',
  PowerShell: '#012456',
  Batchfile: '#c1f12e',
  Go: '#00add8',
  Java: '#b07219',
  'C++': '#f34b7d',
  C: '#555555',
  'C#': '#178600',
  Ruby: '#701516',
  PHP: '#4f5d95',
  Swift: '#f05138',
  Kotlin: '#a97bff',
  Dart: '#00b4ab',
  Lua: '#000080',
  'Jupyter Notebook': '#da5b0b',
  Markdown: '#083fa1',
  JSON: '#6b7280',
  YAML: '#cb171e',
  Dockerfile: '#384d54',
  NSIS: '#a6b4c8',
}

export function languageColor(name: string): string {
  return LANGUAGE_COLORS[name] || 'var(--accent)'
}

/** Stacked-bar shares, largest first, with the tail folded into "other" so a
    repository with forty languages still reads as one bar. */
export function languageShares(languages: Record<string, number>): { name: string; pct: number; color: string }[] {
  const rows = Object.entries(languages).sort((a, b) => b[1] - a[1])
  const total = rows.reduce((sum, [, v]) => sum + v, 0)
  if (!total) return []
  const head = rows.slice(0, 5).map(([name, v]) => ({
    name,
    pct: (v / total) * 100,
    color: languageColor(name),
  }))
  const rest = rows.slice(5).reduce((sum, [, v]) => sum + v, 0)
  if (rest > 0) head.push({ name: 'Other', pct: (rest / total) * 100, color: 'var(--line-strong)' })
  return head
}

/* ---- the checkout behind the active project ---- */

export async function loadLocal(): Promise<void> {
  try {
    const res = await gateway.request<{ repo: string; branch: string; ahead: number; dirty: number }>(
      'github.local', { project: projects.active }
    )
    gh.localRepo = res.repo || ''
    gh.localBranch = res.branch || ''
    gh.localAhead = res.ahead || 0
    gh.localDirty = res.dirty || 0
  } catch {
    /* The badge is decoration. A failure leaves the rack exactly as it was,
       which is also what happens when the project is not a git clone at all. */
  }
}

/* The project registry arrives after the socket connects, which can be later
   than the first status answer. Without this the badge stayed blank until the
   panel was reopened, because that answer was the only trigger. */
watch(() => projects.active, path => { if (path) void loadLocal() }, { immediate: true })

/* ---- commit activity ---- */

export async function loadActivity(fullName: string, attempt = 0, force = false): Promise<void> {
  if (!fullName) return
  try {
    const res = await gateway.request<{ weeks: number[][]; pending: boolean }>(
      'github.activity', { repo: fullName, force }
    )
    if (gh.active !== fullName) return
    if (res.pending) {
      gh.weeksPending = true
      /* GitHub builds this table on demand: the first request for a repository
         answers 202 with no body and the table is ready a few seconds later.
         Two retries cover a cold repository without polling forever. */
      if (attempt < 2) window.setTimeout(() => void loadActivity(fullName, attempt + 1, force), 5000)
      return
    }
    gh.weeksPending = false
    gh.weeks = res.weeks || []
  } catch {
    gh.weeksPending = false
  }
}

/* ---- pull request diff ---- */

export async function togglePull(number: number): Promise<void> {
  if (gh.openPull === number) {
    gh.openPull = 0
    return
  }
  gh.openPull = number
  const cached = gh.pullFiles[number]
  if (cached && !cached.error) return
  const fullName = gh.active
  if (!fullName) return
  gh.pullFiles[number] = { files: [], truncated: false, loading: true, error: '' }
  try {
    const res = await gateway.request<{ files: GhPullFile[]; truncated: boolean }>(
      'github.pullfiles', { repo: fullName, number }
    )
    if (gh.active !== fullName) return
    gh.pullFiles[number] = {
      files: res.files || [],
      truncated: !!res.truncated,
      loading: false,
      error: '',
    }
  } catch (e) {
    gh.pullFiles[number] = {
      files: [],
      truncated: false,
      loading: false,
      error: e instanceof Error ? e.message : String(e),
    }
  }
}

/* ---- hand-off to the composer ----

   The composer is a plain textarea the runtime reads on `input`; there is no
   draft state to write into. Appending to the value and dispatching the event
   is what makes its autosize and its send button follow along. */

function insertComposer(text: string): void {
  const ta = document.getElementById('composerTa') as HTMLTextAreaElement | null
  if (!ta) return
  const current = ta.value
  ta.value = current + (current && !current.endsWith('\n') ? '\n' : '') + text
  ta.dispatchEvent(new Event('input'))
  ta.focus()
  ta.setSelectionRange(ta.value.length, ta.value.length)
}

export function fileUrl(path: string): string {
  const repo = gh.detail?.repo
  if (!repo) return ''
  return `${repo.url}/blob/${repo.branch}/${path}`
}

export function askAboutFile(): void {
  const file = gh.file
  if (!file) return
  /* A file in the repository the active project is a clone of can be named by
     path and the agent will read it locally. Anything else only exists as a
     URL, and naming a path the agent cannot open would be a dead reference. */
  const local = !!gh.localRepo && gh.active === gh.localRepo
  insertComposer(local ? `@pebble-file: ${file.path}\n` : `GitHub file: ${fileUrl(file.path)}\n`)
}

export function askAboutIssue(issue: GhIssue): void {
  insertComposer(`GitHub issue #${issue.number} in ${gh.active}: ${issue.title}\n${issue.url}\n`)
}

export function askAboutPull(pull: GhIssue): void {
  insertComposer(`GitHub pull request #${pull.number} in ${gh.active}: ${pull.title}\n${pull.url}\n`)
}

/* ---- commit diff ----

   The listing endpoint carries no stats and no patches, so a row's numbers only
   exist after it is opened. Caching per sha means re-opening a commit costs
   nothing, and the ETag cache behind it makes the first open the only paid one. */

export async function toggleCommit(sha: string): Promise<void> {
  if (gh.openCommit === sha) {
    gh.openCommit = ''
    return
  }
  gh.openCommit = sha
  const cached = gh.commitDiffs[sha]
  if (cached && !cached.error) return
  const fullName = gh.active
  if (!fullName) return
  gh.commitDiffs[sha] = {
    sha,
    stats: { additions: 0, deletions: 0, total: 0 },
    files: [],
    truncated: false,
    loading: true,
    error: '',
  }
  try {
    const res = await gateway.request<Omit<GhCommitDiff, 'loading' | 'error'>>(
      'github.commit', { repo: fullName, sha }
    )
    if (gh.active !== fullName) return
    gh.commitDiffs[sha] = { ...res, sha, loading: false, error: '' }
  } catch (e) {
    gh.commitDiffs[sha] = {
      sha,
      stats: { additions: 0, deletions: 0, total: 0 },
      files: [],
      truncated: false,
      loading: false,
      error: e instanceof Error ? e.message : String(e),
    }
  }
}

export function compactNumber(value: number): string {
  if (!value) return '0'
  if (value < 1000) return String(value)
  if (value < 10000) return `${(value / 1000).toFixed(1)}k`
  return `${Math.round(value / 1000)}k`
}

export function repoSize(kb: number): string {
  if (!kb) return '0 KB'
  if (kb < 1024) return `${kb} KB`
  return `${(kb / 1024).toFixed(1)} MB`
}

/** "2h ago" rather than a timestamp: at this width the exact minute is noise
    and the distance is the question being asked. */
export function since(iso: string): string {
  if (!iso) return ''
  const then = new Date(iso).getTime()
  if (!Number.isFinite(then)) return ''
  const diff = Date.now() - then
  if (diff < 0) return 'now'
  const min = Math.floor(diff / 60000)
  if (min < 1) return 'now'
  if (min < 60) return `${min}m ago`
  const hour = Math.floor(min / 60)
  if (hour < 24) return `${hour}h ago`
  const day = Math.floor(hour / 24)
  if (day < 30) return `${day}d ago`
  const month = Math.floor(day / 30)
  if (month < 12) return `${month}mo ago`
  return `${Math.floor(month / 12)}y ago`
}

export function fileSize(bytes: number): string {
  if (!bytes) return ''
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`
}
