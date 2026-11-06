<template>
  <div class="dr-pane gh" data-pane="github">
    <!-- No token: the panel explains where the credential already lives rather
         than growing a second field for it. -->
    <div v-if="gh.ready && !gh.configured && !gh.error" class="gh-intro">
      <span class="gh-mark"><GhMark :size="26" /></span>
      <div class="gh-intro-t">No GitHub token</div>
      <p class="gh-intro-p">
        The repository panel borrows the token your MCP github server already carries.
        Fill <code>GITHUB_PERSONAL_ACCESS_TOKEN</code> once in the MCP panel and this tab
        fills itself.
      </p>
      <button class="gh-cta" type="button" @click="openMcp">Open MCP panel</button>
    </div>

    <!-- The first answer has not arrived yet. -->
    <div v-else-if="!gh.ready" class="gh-skel">
      <div class="gh-skel-id"></div>
      <div v-for="n in 4" :key="n" class="gh-skel-card"></div>
    </div>

    <div v-else-if="gh.error && !gh.active" class="gh-fail">
      <span class="gh-fail-t">GitHub did not answer</span>
      <p class="gh-fail-p">{{ gh.error }}</p>
      <button class="gh-cta" type="button" @click="retry">Try again</button>
    </div>

    <!-- ------------------------------------------------ repository rack -->
    <template v-else-if="!gh.active">
      <header class="gh-id">
        <span class="gh-mark"><GhMark :size="18" /></span>
        <div class="gh-id-main">
          <b>{{ gh.login ? '@' + gh.login : 'signed in' }}</b>
          <span>{{ gh.repos.length }} {{ gh.repos.length === 1 ? 'repository' : 'repositories' }}</span>
        </div>
        <div class="gh-rate" :class="{ low: rateLow }">
          <span class="gh-rate-n">{{ gh.rate.remaining }}</span>
          <span class="gh-rate-l">/{{ gh.rate.limit || '—' }}</span>
        </div>
      </header>

      <label class="gh-search">
        <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round"><circle cx="11" cy="11" r="7"/><line x1="16.5" y1="16.5" x2="21" y2="21"/></svg>
        <input v-model="gh.query" type="text" placeholder="filter repositories" spellcheck="false" />
        <button v-if="gh.query" type="button" class="gh-search-x" @click="gh.query = ''">&#215;</button>
      </label>

      <div v-if="gh.loading && !gh.repos.length" class="gh-skel">
        <div v-for="n in 3" :key="n" class="gh-skel-card"></div>
      </div>

      <p v-else-if="!visible.length" class="gh-none">
        {{ gh.repos.length ? 'No repository matches that filter.' : 'This account has no repositories yet.' }}
      </p>

      <div v-else class="gh-rack">
        <article
          v-for="(repo, i) in visible"
          :key="repo.fullName"
          class="gh-card"
          :style="{ '--i': Math.min(i, 12) }"
          role="button"
          tabindex="0"
          @click="open(repo.fullName)"
          @keydown.enter="open(repo.fullName)"
        >
          <span class="gh-card-edge" :style="{ background: languageColor(repo.language) }"></span>
          <div class="gh-card-top">
            <span class="gh-card-name">{{ repo.name }}</span>
            <span v-if="repo.fullName === gh.localRepo" class="gh-tag gh-tag-cur">current</span>
            <span v-if="repo.private" class="gh-tag">private</span>
          </div>
          <p class="gh-card-desc">{{ repo.description || 'No description' }}</p>
          <div class="gh-card-meta">
            <span v-if="repo.language" class="gh-lang">
              <i :style="{ background: languageColor(repo.language) }"></i>{{ repo.language }}
            </span>
            <span class="gh-num">
              <svg width="9" height="9" viewBox="0 0 24 24" fill="currentColor"><path d="M12 2l2.9 6.3 6.9.8-5.1 4.7 1.4 6.8L12 17.3 5.9 20.6l1.4-6.8L2.2 9.1l6.9-.8z"/></svg>{{ compactNumber(repo.stars) }}
            </span>
            <span class="gh-num">
              <svg width="9" height="9" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2"><circle cx="6" cy="5" r="2.2"/><circle cx="18" cy="5" r="2.2"/><circle cx="12" cy="19" r="2.2"/><path d="M6 7.2v3.3a2 2 0 002 2h8a2 2 0 002-2V7.2"/><path d="M12 12.5v4.3"/></svg>{{ compactNumber(repo.forks) }}
            </span>
            <span class="gh-when">{{ since(repo.pushedAt) }}</span>
          </div>
          <div v-if="repo.topics.length" class="gh-topics">
            <span v-for="t in repo.topics.slice(0, 3)" :key="t" class="gh-topic">{{ t }}</span>
          </div>
        </article>

        <button
          v-if="gh.reposHasMore"
          type="button"
          class="gh-more"
          :disabled="gh.reposLoadingMore"
          @click="loadRepos(true)"
        >{{ gh.reposLoadingMore ? 'Loading' : 'Load more' }}</button>
      </div>
    </template>

    <!-- ------------------------------------------------ repository detail -->
    <template v-else>
      <button class="gh-back" type="button" @click="closeRepo">
        <svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.6" stroke-linecap="round" stroke-linejoin="round"><polyline points="15 18 9 12 15 6"/></svg>
        all repositories
      </button>

      <header class="gh-head">
        <div class="gh-head-top">
          <h3 class="gh-title">{{ shortName }}</h3>
          <button class="gh-icon" type="button" aria-label="Refresh" @click="refreshRepo()">
            <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><polyline points="23 4 23 10 17 10"/><path d="M20.5 15a9 9 0 11-2.1-9.4L23 10"/></svg>
          </button>
          <button class="gh-icon" type="button" aria-label="Open on GitHub" @click="openExternal(gh.detail?.repo.url || '')">
            <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="M18 13v6a2 2 0 01-2 2H5a2 2 0 01-2-2V8a2 2 0 012-2h6"/><polyline points="15 3 21 3 21 9"/><line x1="10" y1="14" x2="21" y2="3"/></svg>
          </button>
        </div>
        <p class="gh-sub">{{ gh.detail?.repo.description || 'No description' }}</p>

        <div v-if="shares.length" class="gh-langbar" aria-hidden="true">
          <span v-for="s in shares" :key="s.name" :style="{ width: s.pct + '%', background: s.color }"></span>
        </div>
        <div v-if="shares.length" class="gh-langlist">
          <span v-for="s in shares.slice(0, 4)" :key="s.name" class="gh-langitem">
            <i :style="{ background: s.color }"></i>{{ s.name }}<em>{{ s.pct.toFixed(0) }}%</em>
          </span>
        </div>

        <div class="gh-facts">
          <span class="gh-fact"><b>{{ compactNumber(gh.detail?.repo.stars || 0) }}</b>stars</span>
          <span class="gh-fact"><b>{{ compactNumber(gh.detail?.repo.forks || 0) }}</b>forks</span>
          <span class="gh-fact"><b>{{ repoSize(gh.detail?.repo.size || 0) }}</b>size</span>
          <span class="gh-fact"><b>{{ since(gh.detail?.repo.pushedAt || '') }}</b>pushed</span>
        </div>

        <div
          v-if="gh.localRepo === gh.active"
          class="gh-local"
          :class="{ 'has-pending': gh.localAhead || gh.localDirty }"
        >
          <span class="gh-local-dot"></span>
          <span class="gh-local-branch">{{ gh.localBranch || 'detached' }}</span>
          <span v-if="gh.localAhead" class="gh-local-ahead">{{ gh.localAhead }} unpushed</span>
          <span v-if="gh.localDirty" class="gh-local-dirty">{{ gh.localDirty }} changed</span>
          <span v-if="!gh.localAhead && !gh.localDirty" class="gh-local-sync">in sync</span>
        </div>

        <div v-if="gh.weeks.length" class="gh-heat">
          <span class="gh-heat-h">commit activity<em>{{ heatTotal }}</em></span>
          <div class="gh-heat-grid">
            <span v-for="(week, wi) in gh.weeks" :key="wi" class="gh-heat-col">
              <i v-for="(day, di) in week" :key="di" :class="heatLevel(day)"></i>
            </span>
          </div>
        </div>
        <p v-else-if="gh.weeksPending" class="gh-heat-pending">GitHub is still computing this repository's activity</p>
      </header>

      <nav class="gh-views">
        <button
          v-for="v in VIEWS"
          :key="v.id"
          type="button"
          class="gh-view"
          :class="{ on: gh.view === v.id || (v.id === 'files' && gh.view === 'file') }"
          @click="gh.view = v.id"
        >{{ v.label }}<em v-if="v.count !== null">{{ v.count }}</em></button>
      </nav>

      <div v-if="gh.detailLoading" class="gh-skel">
        <div v-for="n in 3" :key="n" class="gh-skel-card"></div>
      </div>

      <div v-else-if="gh.detailError" class="gh-fail">
        <span class="gh-fail-t">Could not load this repository</span>
        <p class="gh-fail-p">{{ gh.detailError }}</p>
      </div>

      <!-- README -->
      <div v-else-if="gh.view === 'readme'" class="gh-readme">
        <div v-if="excerpt" class="gh-md" v-html="excerpt"></div>
        <p v-else class="gh-none">This repository has no README.</p>
        <button class="gh-more" type="button" @click="openExternal(gh.detail?.repo.url || '')">
          Read the full repository on GitHub
        </button>
      </div>

      <!-- FILE TREE -->
      <div v-else-if="gh.view === 'files'" class="gh-files">
        <div class="gh-crumbs">
          <button type="button" class="gh-crumb" @click="loadDir('')">{{ shortName }}</button>
          <template v-for="(seg, i) in crumbSegs" :key="i">
            <span class="gh-crumb-sep">/</span>
            <button type="button" class="gh-crumb" @click="loadDir(crumbSegs.slice(0, i + 1).join('/'))">{{ seg }}</button>
          </template>
        </div>
        <p v-if="!gh.entries.length" class="gh-none">This directory is empty.</p>
        <button
          v-for="entry in gh.entries"
          :key="entry.path"
          type="button"
          class="gh-entry"
          :class="entry.type"
          @click="entry.type === 'dir' ? loadDir(entry.path) : loadFile(entry.path)"
        >
          <span class="gh-entry-ico">
            <svg v-if="entry.type === 'dir'" width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M22 19a2 2 0 01-2 2H4a2 2 0 01-2-2V5a2 2 0 012-2h5l2 3h9a2 2 0 012 2z"/></svg>
            <svg v-else width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M14 2H6a2 2 0 00-2 2v16a2 2 0 002 2h12a2 2 0 002-2V8z"/><polyline points="14 2 14 8 20 8"/></svg>
          </span>
          <span class="gh-entry-name">{{ entry.name }}</span>
          <span class="gh-entry-size">{{ entry.type === 'file' ? fileSize(entry.size) : '' }}</span>
        </button>
      </div>

      <!-- FILE -->
      <div v-else-if="gh.view === 'file'" class="gh-file">
        <div class="gh-file-bar">
          <button type="button" class="gh-file-back" @click="closeFile()">
            <svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.6" stroke-linecap="round" stroke-linejoin="round"><polyline points="15 18 9 12 15 6"/></svg>
            Files
          </button>
          <span class="gh-file-name">{{ gh.file?.name || 'opening' }}</span>
          <span class="gh-file-meta">{{ gh.file ? fileSize(gh.file.size) : '' }}</span>
          <button
            v-if="gh.file && !gh.file.binary"
            type="button"
            class="gh-ask"
            @click="askAboutFile()"
          >
            <svg width="9" height="9" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M21 15a2 2 0 01-2 2H7l-4 4V5a2 2 0 012-2h14a2 2 0 012 2z"/></svg>
            Ask
          </button>
        </div>

        <div v-if="gh.fileLoading" class="gh-skel">
          <div class="gh-skel-card"></div>
          <div class="gh-skel-card"></div>
        </div>

        <div v-else-if="gh.fileError" class="gh-fail">
          <span class="gh-fail-t">Could not open the file</span>
          <p class="gh-fail-p">{{ gh.fileError }}</p>
          <button class="gh-cta" type="button" @click="closeFile()">Back to files</button>
        </div>

        <div v-else-if="gh.file && gh.file.binary" class="gh-file-bin">
          <span class="gh-file-bin-ico">
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"><path d="M14 2H6a2 2 0 00-2 2v16a2 2 0 002 2h12a2 2 0 002-2V8z"/><polyline points="14 2 14 8 20 8"/></svg>
          </span>
          <p class="gh-file-bin-p">Binary file · {{ fileSize(gh.file.size) }}</p>
          <button class="gh-cta" type="button" @click="openExternal(fileUrl(gh.file.path))">Open on GitHub</button>
        </div>

        <div v-else-if="gh.file" class="gh-code" @click="onFileClick" v-html="renderedFile"></div>
      </div>

      <!-- COMMITS -->
      <div v-else-if="gh.view === 'commits'" class="gh-timeline">
        <p v-if="!gh.commits.length" class="gh-none">No commits on the default branch.</p>
        <div v-for="c in gh.commits" :key="c.sha" class="gh-commit-wrap">
          <button
            type="button"
            class="gh-commit"
            :class="{ open: gh.openCommit === c.sha }"
            @click="toggleCommit(c.sha)"
          >
            <span class="gh-commit-dot"></span>
            <img v-if="c.avatar" class="gh-avatar" :src="c.avatar" alt="" loading="lazy" />
            <div class="gh-commit-main">
              <span class="gh-commit-msg">{{ c.message }}</span>
              <span class="gh-commit-meta"><code>{{ c.shortSha || c.sha }}</code>{{ c.author }} · {{ since(c.date) }}</span>
            </div>
          </button>

          <div v-if="gh.openCommit === c.sha" class="gh-diff">
            <p v-if="gh.commitDiffs[c.sha]?.loading" class="gh-diff-p">Loading diff</p>
            <p v-else-if="gh.commitDiffs[c.sha]?.error" class="gh-diff-p err">{{ gh.commitDiffs[c.sha].error }}</p>
            <template v-else-if="gh.commitDiffs[c.sha]">
              <div class="gh-commit-sum">
                <b>+{{ gh.commitDiffs[c.sha].stats.additions }}</b>
                <i>-{{ gh.commitDiffs[c.sha].stats.deletions }}</i>
                <span>{{ gh.commitDiffs[c.sha].files.length }} files</span>
              </div>
              <div v-for="f in gh.commitDiffs[c.sha].files" :key="f.filename" class="gh-diff-file">
                <div class="gh-diff-head">
                  <span class="gh-diff-name">{{ f.filename }}</span>
                  <span class="gh-diff-stat"><b>+{{ f.additions }}</b><i>-{{ f.deletions }}</i></span>
                </div>
                <pre v-if="f.patch" class="gh-diff-body">{{ f.patch }}</pre>
                <p v-else class="gh-diff-none">No diff shown</p>
              </div>
              <p v-if="gh.commitDiffs[c.sha].truncated" class="gh-diff-note">
                Some changes are not shown. GitHub omits the diff for binary and oversized files, and this list stops at 300 files.
              </p>
            </template>
          </div>
        </div>
      </div>

      <!-- ACTIVITY -->
      <div v-else class="gh-activity">
        <div class="gh-sec">
          <span class="gh-sec-h">Pull requests<em>{{ gh.pulls.length }}</em></span>
          <p v-if="!gh.pulls.length" class="gh-none">Nothing open.</p>
          <div v-for="p in gh.pulls" :key="p.number" class="gh-item-wrap">
            <button
              type="button"
              class="gh-item"
              :class="{ open: gh.openPull === p.number }"
              @click="togglePull(p.number)"
            >
              <span class="gh-item-num">#{{ p.number }}</span>
              <span class="gh-item-title">{{ p.title }}</span>
              <span class="gh-item-meta">{{ p.user }} · {{ since(p.updatedAt) }}</span>
            </button>
            <div class="gh-item-acts">
              <button type="button" class="gh-ask-ico" aria-label="Ask the agent about this pull request" @click="askAboutPull(p)">
                <svg width="9" height="9" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"><path d="M21 15a2 2 0 01-2 2H7l-4 4V5a2 2 0 012-2h14a2 2 0 012 2z"/></svg>
              </button>
              <button type="button" class="gh-ask-ico" aria-label="Open on GitHub" @click="openExternal(p.url)">
                <svg width="9" height="9" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"><path d="M18 13v6a2 2 0 01-2 2H5a2 2 0 01-2-2V8a2 2 0 012-2h6"/><polyline points="15 3 21 3 21 9"/><line x1="10" y1="14" x2="21" y2="3"/></svg>
              </button>
            </div>
            <div v-if="gh.openPull === p.number" class="gh-diff">
              <p v-if="gh.pullFiles[p.number]?.loading" class="gh-diff-p">Loading diff</p>
              <p v-else-if="gh.pullFiles[p.number]?.error" class="gh-diff-p err">{{ gh.pullFiles[p.number].error }}</p>
              <template v-else-if="gh.pullFiles[p.number]">
                <div v-for="f in gh.pullFiles[p.number].files" :key="f.filename" class="gh-diff-file">
                  <div class="gh-diff-head">
                    <span class="gh-diff-name">{{ f.filename }}</span>
                    <span class="gh-diff-stat"><b>+{{ f.additions }}</b><i>-{{ f.deletions }}</i></span>
                  </div>
                  <pre v-if="f.patch" class="gh-diff-body">{{ f.patch }}</pre>
                  <p v-else class="gh-diff-none">No diff shown</p>
                </div>
                <p v-if="gh.pullFiles[p.number].truncated" class="gh-diff-note">
                  Some changes are not shown. GitHub omits the diff for binary and oversized files, and this list stops at 100 files.
                </p>
              </template>
            </div>
          </div>
        </div>
        <div class="gh-sec">
          <span class="gh-sec-h">Issues<em>{{ gh.issues.length }}</em></span>
          <p v-if="!gh.issues.length" class="gh-none">Nothing open.</p>
          <div v-for="issue in gh.issues" :key="issue.number" class="gh-item-wrap">
            <button
              type="button"
              class="gh-item"
              @click="openExternal(issue.url)"
            >
              <span class="gh-item-num">#{{ issue.number }}</span>
              <span class="gh-item-title">{{ issue.title }}</span>
              <span class="gh-item-labels" v-if="issue.labels.length">
                <span
                  v-for="label in issue.labels"
                  :key="label.name"
                  class="gh-label"
                  :style="{ '--lab': '#' + (label.color || '888888') }"
                >{{ label.name }}</span>
              </span>
              <span class="gh-item-meta">{{ issue.user }} · {{ since(issue.updatedAt) }}</span>
            </button>
            <div class="gh-item-acts">
              <button type="button" class="gh-ask-ico" aria-label="Ask the agent about this issue" @click="askAboutIssue(issue)">
                <svg width="9" height="9" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"><path d="M21 15a2 2 0 01-2 2H7l-4 4V5a2 2 0 012-2h14a2 2 0 012 2z"/></svg>
              </button>
            </div>
          </div>
        </div>
      </div>
    </template>
  </div>
</template>

<script setup lang="ts">
import { computed, defineComponent, h, onBeforeUnmount, onMounted } from 'vue'
import {
  gh,
  filteredRepos,
  loadStatus,
  loadRepos,
  openRepo,
  closeRepo,
  loadDir,
  loadSide,
  loadFile,
  closeFile,
  refreshRepo,
  togglePull,
  toggleCommit,
  askAboutFile,
  askAboutIssue,
  askAboutPull,
  fileUrl,
  guessLang,
  openExternal,
  languageColor,
  languageShares,
  compactNumber,
  repoSize,
  since,
  fileSize,
} from '../data/github'
import { renderMarkdown, renderCodeBlock } from '../data/render'
import { gateway } from '../data/gateway'
import { toast } from '../data/wire'

/* The mark is the official GitHub octocat glyph, inlined so the panel needs no
   network fetch and inherits `currentColor` like every other icon here. */
const GhMark = defineComponent({
  props: { size: { type: Number, default: 16 } },
  setup(props) {
    return () => h('svg', {
      width: props.size,
      height: props.size,
      viewBox: '0 0 16 16',
      fill: 'currentColor',
      'aria-hidden': 'true',
    }, [
      h('path', {
        d: 'M8 0C3.58 0 0 3.58 0 8c0 3.54 2.29 6.53 5.47 7.59.4.07.55-.17.55-.38 0-.19-.01-.82-.01-1.49-2.01.37-2.53-.49-2.69-.94-.09-.23-.48-.94-.82-1.13-.28-.15-.68-.52-.01-.53.63-.01 1.08.58 1.23.82.72 1.21 1.87.87 2.33.66.07-.52.28-.87.51-1.07-1.78-.2-3.64-.89-3.64-3.95 0-.87.31-1.59.82-2.15-.08-.2-.36-1.02.08-2.12 0 0 .67-.21 2.2.82.64-.18 1.32-.27 2-.27.68 0 1.36.09 2 .27 1.53-1.04 2.2-.82 2.2-.82.44 1.1.16 1.92.08 2.12.51.56.82 1.27.82 2.15 0 3.07-1.87 3.75-3.65 3.95.29.25.54.73.54 1.48 0 1.07-.01 1.93-.01 2.2 0 .21.15.46.55.38A8.013 8.013 0 0016 8c0-4.42-3.58-8-8-8z',
      }),
    ])
  },
})

const visible = computed(() => filteredRepos())

const shortName = computed(() => gh.active.split('/').pop() || gh.active)

const shares = computed(() => languageShares(gh.detail?.languages || {}))

const rateLow = computed(() => gh.rate.limit > 0 && gh.rate.remaining / gh.rate.limit < 0.15)

const crumbSegs = computed(() => gh.dirPath.split('/').filter(Boolean))

/* The file body goes through the renderer the transcript already uses, so it
   arrives with the gutter, the highlight and the copy button in place. Only the
   copy click needs wiring, and it is delegated because that markup is a
   string. */
/* Heat levels are absolute rather than scaled to the repository's own maximum:
   a quiet repository should look quiet, and rescaling every repository to full
   contrast would make one commit a week look like a busy one. */
function heatLevel(commits: number): string {
  if (!commits) return 'l0'
  if (commits <= 2) return 'l1'
  if (commits <= 5) return 'l2'
  if (commits <= 10) return 'l3'
  return 'l4'
}

const heatTotal = computed(() =>
  gh.weeks.reduce((sum, week) => sum + week.reduce((a, b) => a + b, 0), 0)
)

const renderedFile = computed(() => {
  const f = gh.file
  if (!f || f.binary) return ''
  return renderCodeBlock(guessLang(f.path), f.text)
})

const VIEWS = computed(() => [
  { id: 'readme' as const, label: 'README', count: null },
  { id: 'files' as const, label: 'Files', count: null },
  { id: 'commits' as const, label: 'Commits', count: gh.commits.length || null },
  { id: 'activity' as const, label: 'Activity', count: (gh.issues.length + gh.pulls.length) || null },
])

/* A README is markdown with HTML mixed in, and the HTML half is usually a
   centred banner: rendering it verbatim fills the panel with tag soup before
   the first sentence. Tags and images go, the prose stays, and the tail is cut
   because the full document is one click away. */
const excerpt = computed(() => {
  const raw = gh.detail?.readme || ''
  if (!raw) return ''
  const stripped = raw
    .replace(/<!--[\s\S]*?-->/g, '')
    .replace(/!\[[^\]]*\]\([^)]*\)/g, '')
    .replace(/<[^>]+>/g, ' ')
    .replace(/\n{3,}/g, '\n\n')
    .trim()
  if (!stripped) return ''
  const cut = stripped.slice(0, 1400)
  return renderMarkdown(cut)
})

function open(fullName: string): void {
  void openRepo(fullName)
}

function retry(): void {
  void loadStatus(true)
}

function onFileClick(e: MouseEvent): void {
  const target = e.target as HTMLElement | null
  if (!target?.closest('[data-copy]')) return
  const text = gh.file?.text || ''
  if (!text) return
  void navigator.clipboard.writeText(text)
  toast('Copied', 'file contents on the clipboard', 'ok')
}

function openMcp(): void {
  const nav = (window as unknown as { __pebbleNav?: { openSheet: (id: string) => void } }).__pebbleNav
  nav?.openSheet('sheet-mcp')
}

/* The pane mounts with the app, long before the socket is up. Loading on mount
   alone rejected on every cold start and the failure then rendered as "no
   token", because that branch sits above the error branch. Wait for the
   connection when it is not there yet. */
let offStatus: (() => void) | null = null

onMounted(() => {
  if (gateway.connected) void loadStatus()
  else offStatus = gateway.onStatus(up => { if (up) void loadStatus() })
})

onBeforeUnmount(() => {
  offStatus?.()
  offStatus = null
})

defineExpose({ loadRepos, loadSide })
</script>
