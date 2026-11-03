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
        <article class="mc-srv" v-for="(srv, i) in mcp.servers" :key="i" :class="[srv.on ? 'on' : '', srv.open ? 'open' : '', srv.state === 'err' ? 'err' : '']">
          <button class="mc-srv-head" @click="srv.open = !srv.open">
            <span class="mc-led"></span>
            <span class="mc-logo">{{ srv.logo }}</span>
            <span class="mc-srv-main">
              <span class="mc-name">{{ srv.name }}<span class="mc-tsp">{{ srv.transport }}</span></span>
              <span class="mc-cmd">{{ srv.cmd }}</span>
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
            <div class="mc-token">
              <span class="mc-tk-l">token</span>
              <template v-if="editing === srv.name">
                <input class="mc-tk-in" v-model="secretDraft" placeholder="paste token…" spellcheck="false" @keydown.enter.prevent="saveSecret(srv.name)" />
                <button class="mc-tk-e" @click="saveSecret(srv.name)">Save</button>
                <button class="mc-tk-e" @click="editing = ''">Cancel</button>
              </template>
              <template v-else>
                <span class="mc-tk-v" :class="{ unset: !srv.token }">{{ srv.token || 'not set' }}</span>
                <button class="mc-tk-e" @click="startEdit(srv.name)">{{ srv.token ? 'Edit' : 'Set' }}</button>
              </template>
            </div>
          </div>
        </article>
      </div>

      <div class="mc-opt">
        <div class="mc-opt-h">Optional servers</div>
        <div class="mc-opt-row" v-for="opt in mcp.optional" :key="opt.name">
          <span class="mc-opt-logo">{{ opt.logo }}</span>
          <span class="mc-opt-main"><b>{{ opt.name }}</b><small>{{ opt.desc }}</small></span>
          <button class="mc-install" @click="installMcpServer(opt.name)">Install</button>
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

function startEdit(server: string): void {
  editing.value = server
  secretDraft.value = ''
}

/** Writes one env var into the server's `.mcp.json` entry. The gateway rejects
    names that are not plain identifiers, so the field keeps the identifier and
    the value is the only thing typed here. */
async function saveSecret(server: string): Promise<void> {
  const value = secretDraft.value.trim()
  if (!value) return
  const ok = await setMcpSecret(server, 'MCP_TOKEN', value)
  if (ok) {
    editing.value = ''
    secretDraft.value = ''
  }
}
</script>
