<template>
<div class="overlay" id="sheet-mcp" data-live="1">
  <div class="sheet mc">
    <header class="mc-hdr">
      <span class="mc-ico">
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7"><rect x="3" y="3" width="7" height="7" rx="1.5"/><rect x="14" y="3" width="7" height="7" rx="1.5"/><rect x="3" y="14" width="7" height="7" rx="1.5"/><rect x="14" y="14" width="7" height="7" rx="1.5"/></svg>
      </span>
      <div class="mc-title">MCP servers<small>model context protocol</small></div>
      <div class="mc-sum">
        <span class="mc-pill on"><i></i>{{ mcp.running }} running</span>
        <span class="mc-pill"><i></i>{{ mcp.idle }} idle</span>
        <span class="mc-pill">{{ mcp.tools }} tools</span>
      </div>
      <button class="sheet-x" data-close>&#215;</button>
    </header>

    <div class="mc-body">
      <div class="section-sub">new servers are processes · each one waits here until you approve it</div>

      <div class="mc-pending" v-if="mcp.pendingProject">
        <span class="mc-pend-ico">!</span>
        <div class="mc-pend-main">
          <div class="mc-pend-t">Project config pending approval</div>
          <div class="mc-pend-p">{{ mcp.pendingProject.path }}</div>
          <div class="mc-pend-s">{{ mcp.pendingProject.servers.join(' · ') }}</div>
        </div>
        <button class="mc-approve" id="mcApprove" @click="trustMcpProject()">Approve</button>
      </div>

      <div class="mc-pending" v-for="p in mcp.pendingUser" :key="'pending-' + p.name">
        <span class="mc-pend-ico">!</span>
        <div class="mc-pend-main">
          <div class="mc-pend-t">{{ p.name }} wants to run</div>
          <div class="mc-pend-p">{{ p.target || 'no command declared' }}</div>
          <div class="mc-pend-s">from ~/.pebble/.mcp.json</div>
        </div>
        <button class="mc-approve" @click="approveMcpServer(p.name)">Approve</button>
        <button class="mc-reject" @click="rejectMcpServer(p.name)">Dismiss</button>
      </div>

      <div class="mc-rack">
        <article class="mc-srv" v-for="(srv, i) in mcp.servers" :key="srv.name || i" :class="[srv.on ? 'on' : '', srv.open ? 'open' : '', srv.state === 'err' ? 'err' : '']">
          <button class="mc-srv-head" @click="srv.open = !srv.open">
            <span class="mc-led"></span>
            <span class="mc-logo">{{ srv.logo }}</span>
            <span class="mc-srv-main">
              <span class="mc-name">{{ srv.name }}<span class="mc-tsp">{{ srv.transport }}</span></span>
              <span class="mc-cmd">{{ displayCmd(srv) }}</span>
            </span>
            <span class="mc-count">{{ srv.tools }}</span>
            <span class="mc-sw" :class="{ on: srv.on }" @click.stop="toggleMcpServer(srv.name, !srv.on)"><i></i></span>
            <span class="mc-caret">&#9662;</span>
          </button>

          <div class="mc-srv-body">
            <div class="mc-toolnames" v-if="srv.on && fullTools(srv).length">
              <span class="mc-tn-l">tools · {{ fullTools(srv).length }}</span>
              <div class="mc-tn-list">
                <span class="mc-tn" v-for="n in fullTools(srv)" :key="n">{{ n }}</span>
              </div>
            </div>

            <div class="mc-token" v-if="srv.requiresToken">
              <span class="mc-tk-l">{{ srv.tokenEnv || 'token' }}</span>
              <template v-if="editing === srv.name">
                <input class="mc-tk-in" v-model="secretDraft" placeholder="paste token…" spellcheck="false" @keydown.enter.prevent="saveSecret(srv)" />
                <button class="mc-tk-e" @click="saveSecret(srv)">Save</button>
                <button class="mc-tk-e" @click="editing = ''">Cancel</button>
              </template>
              <template v-else>
                <span class="mc-tk-v" :class="{ unset: !srv.tokenConfigured }">{{ srv.tokenConfigured ? 'configured' : 'not set' }}</span>
                <button class="mc-tk-e" @click="startEdit(srv.name)">{{ srv.tokenConfigured ? 'Edit' : 'Set' }}</button>
              </template>
            </div>

            <div class="mc-token" v-if="srv.installable && srv.installType === 'download'">
              <span class="mc-tk-l">{{ downloadTarget === srv.name ? 'install dir' : pathTarget === srv.name ? 'exe path' : (srv.ready ? 'binary' : 'needs') }}</span>
              <template v-if="downloadTarget === srv.name">
                <input
                  class="mc-tk-in"
                  v-model="installDirDraft"
                  placeholder="~/.pebble/mcp"
                  spellcheck="false"
                  :disabled="installing === srv.name"
                  @keydown.enter.prevent="runDownload(srv)"
                />
                <button class="mc-tk-e" :disabled="installing === srv.name" @click="runDownload(srv)">
                  {{ installing === srv.name ? 'Downloading…' : 'Confirm' }}
                </button>
                <button class="mc-tk-e" :disabled="installing === srv.name" @click="downloadTarget = ''">Cancel</button>
              </template>
              <template v-else-if="pathTarget === srv.name">
                <input
                  class="mc-tk-in"
                  v-model="pathDraft"
                  placeholder="C:\path\to\codebase-memory-mcp.exe or folder…"
                  spellcheck="false"
                  :disabled="installing === srv.name"
                  @keydown.enter.prevent="saveManualPath(srv)"
                />
                <button class="mc-tk-e" :disabled="installing === srv.name" @click="saveManualPath(srv)">
                  {{ installing === srv.name ? 'Saving…' : 'Save' }}
                </button>
                <button class="mc-tk-e" :disabled="installing === srv.name" @click="pathTarget = ''">Cancel</button>
              </template>
              <template v-else>
                <span class="mc-tk-v" :class="{ unset: !srv.ready }">{{ srv.ready ? srv.cmd : 'not installed' }}</span>
                <button class="mc-tk-e" :disabled="installing === srv.name" @click="startDownload(srv)">
                  {{ installing === srv.name ? 'Downloading…' : (srv.ready ? 'Redownload' : 'Download') }}
                </button>
                <button class="mc-tk-e" :disabled="installing === srv.name" @click="startPath(srv)">
                  {{ srv.ready ? 'Change path' : 'Set path' }}
                </button>
              </template>
            </div>

            <div class="mc-token" v-else-if="!srv.ready">
              <span class="mc-tk-l">needs</span>
              <span class="mc-tk-v unset">{{ missingLabel(srv.missing) }}</span>
              <a
                v-for="(link, key) in srv.prereqLinks"
                :key="key"
                class="mc-tk-e"
                :href="link.url"
                target="_blank"
                rel="noopener"
              >{{ link.label }}</a>
              <button class="mc-tk-e" v-if="srv.installable" :disabled="installing === srv.name" @click="runInstall(srv.name)">
                {{ installing === srv.name ? 'Installing…' : 'Install' }}
              </button>
            </div>

            <div class="mc-token mc-err-row" v-if="srv.state === 'err' && srv.error && srv.ready">
              <span class="mc-tk-l">error</span>
              <span class="mc-tk-v">{{ srv.error }}</span>
            </div>
          </div>
        </article>
      </div>

      <div class="mc-opt" v-if="mcp.available.length">
        <div class="mc-opt-h">Optional servers</div>
        <div class="mc-opt-row" v-for="opt in mcp.available" :key="opt.name">
          <span class="mc-opt-logo">{{ opt.name.slice(0, 1).toUpperCase() }}</span>
          <span class="mc-opt-main">
            <b>{{ opt.name }}</b>
            <small>{{ opt.desc }}<template v-if="opt.missing.length"> · needs {{ missingLabel(opt.missing) }}</template></small>
          </span>
          <button class="mc-install" :disabled="installing === opt.name" @click="runInstall(opt.name)">
            {{ installing === opt.name ? 'Installing…' : 'Install' }}
          </button>
        </div>
      </div>
    </div>

    <footer class="mc-ft">
      <span class="mc-hint">{{ mcp.servers.length }} installed · {{ mcp.running }} running · {{ mcp.tools }} tools</span>
      <button class="btn btn-ghost" data-close>Close</button>
    </footer>
  </div>
</div>
</template>

<script setup lang="ts">
import { ref } from 'vue'
import { mcp, type McpServer } from '../data/panels'
import { toggleMcpServer, installMcpServer, trustMcpProject, setMcpSecret, approveMcpServer, rejectMcpServer } from '../data/wire'

/* An enabled server lists every tool it exposes. `chips` only carries the first
   four for the collapsed row, so the full list comes from `toolNames`; a server
   that has not reported its tools yet falls back to the chips plus the tail
   count rather than showing an empty box. */
function fullTools(srv: McpServer): string[] {
  if (srv.toolNames?.length) return srv.toolNames
  const out = [...srv.chips]
  if (srv.more > 0) out.push(`+${srv.more} more`)
  return out
}

const editing = ref('')
const secretDraft = ref('')
const downloadTarget = ref('')
const installDirDraft = ref('')
const pathTarget = ref('')
const pathDraft = ref('')
const installing = ref('')

/* Prerequisites arrive as identifiers (node / uv / path); the row spells them
   the way a reader would search for them. */
const PREREQ_LABELS: Record<string, string> = {
  node: 'Node.js 18+',
  uv: 'uv',
  path: 'an executable'
}

function missingLabel(missing: string[]): string {
  return missing.map(m => PREREQ_LABELS[m] ?? m).join(', ')
}

function displayCmd(srv: McpServer): string {
  if (!srv.cmd || srv.cmd.includes('REPLACE_WITH')) {
    return 'not installed — expand to download or set path'
  }
  return srv.cmd
}

function startEdit(server: string): void {
  editing.value = server
  secretDraft.value = ''
}

function startDownload(srv: McpServer): void {
  pathTarget.value = ''
  downloadTarget.value = srv.name
  installDirDraft.value = srv.installDir || '~/.pebble/mcp'
}

function startPath(srv: McpServer): void {
  downloadTarget.value = ''
  pathTarget.value = srv.name
  pathDraft.value = srv.ready && srv.cmd ? srv.cmd : ''
}

async function runDownload(srv: McpServer): Promise<void> {
  if (installing.value) return
  installing.value = srv.name
  try {
    const ok = await installMcpServer(srv.name, { installDir: installDirDraft.value })
    if (ok) downloadTarget.value = ''
  } finally {
    installing.value = ''
  }
}

async function saveManualPath(srv: McpServer): Promise<void> {
  const value = pathDraft.value.trim()
  if (!value || installing.value) return
  installing.value = srv.name
  try {
    const ok = await installMcpServer(srv.name, { path: value })
    if (ok) pathTarget.value = ''
  } finally {
    installing.value = ''
  }
}

async function runInstall(name: string): Promise<void> {
  if (installing.value) return
  installing.value = name
  try {
    await installMcpServer(name)
  } finally {
    installing.value = ''
  }
}

/** Writes the credential into the variable that server actually reads. The
    gateway rejects names that are not plain identifiers, so the field keeps the
    identifier and the value is the only thing typed here. */
async function saveSecret(srv: McpServer): Promise<void> {
  const value = secretDraft.value.trim()
  if (!value) return
  const ok = await setMcpSecret(srv.name, srv.tokenEnv || 'MCP_TOKEN', value)
  if (ok) {
    editing.value = ''
    secretDraft.value = ''
  }
}
</script>
