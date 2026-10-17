<script setup lang="ts">
import { ref, computed, watch } from 'vue'
import { useMemoryStore } from '../stores/memory'
import type { MemoryEntry, MemoryKind } from '../api/memory'
import CreateMemoryDialog from './CreateMemoryDialog.vue'
import ConfirmDialog from './ConfirmDialog.vue'

const st = useMemoryStore()
const sq = ref('')
const sm = ref<MemoryEntry | null>(null)
const sd = ref(false)
const sv = ref(false)
const draft = ref({ kind: 'correction' as MemoryKind, statement: '', rationale: '', confidence: 0.8 })
const eg = ref<Record<string, boolean>>({
  correction: true, constraint: true, preference: true, pitfall: true, recipe: true, reference: true,
})
const notice = ref('')
const confirmState = ref({
  open: false, title: '', message: '', danger: false, action: (() => {}) as () => void,
})

const KIND_META: Record<string, { label: string; color: string; hint: string }> = {
  correction: { label: 'Corrections', color: 'var(--accent)', hint: '被纠正过的做法' },
  constraint: { label: 'Constraints', color: 'var(--cyan)', hint: '长期生效的硬性要求' },
  preference: { label: 'Preferences', color: 'var(--green)', hint: '稳定的偏好与习惯' },
  pitfall: { label: 'Pitfalls', color: 'var(--red)', hint: '失败过的操作方式' },
  recipe: { label: 'Recipes', color: 'var(--accent)', hint: '可复用的解法' },
  reference: { label: 'References', color: 'var(--fg-dim)', hint: '外部资料位置' },
}

const kindKeys = Object.keys(KIND_META)

const grouped = computed(() => {
  const q = sq.value.toLowerCase()
  const match = (m: MemoryEntry) =>
    !q || m.statement.toLowerCase().includes(q) || (m.rationale || '').toLowerCase().includes(q)
  const out: Record<string, MemoryEntry[]> = {}
  for (const key of kindKeys) out[key] = []
  for (const m of st.memories) {
    if (!match(m)) continue
    if (!out[m.kind]) out[m.kind] = []
    out[m.kind].push(m)
  }
  return out
})

const summary = computed(() => [
  { key: 'correction', label: 'CORR', count: st.kindCounts.correction || 0, pct: st.kindPercent('correction') },
  { key: 'constraint', label: 'CONS', count: st.kindCounts.constraint || 0, pct: st.kindPercent('constraint') },
  { key: 'preference', label: 'PREF', count: st.kindCounts.preference || 0, pct: st.kindPercent('preference') },
  { key: 'pitfall', label: 'PITF', count: st.kindCounts.pitfall || 0, pct: st.kindPercent('pitfall') },
])

function flash(text: string) {
  notice.value = text
  window.setTimeout(() => { if (notice.value === text) notice.value = '' }, 2400)
}

async function selectEntry(m: MemoryEntry) {
  sm.value = m
  draft.value = {
    kind: m.kind,
    statement: m.statement,
    rationale: m.rationale || '',
    confidence: m.confidence,
  }
  try {
    const full = await st.fetchMemoryById(m.id)
    if (full) {
      sm.value = full
      draft.value = {
        kind: full.kind,
        statement: full.statement,
        rationale: full.rationale || '',
        confidence: full.confidence,
      }
    }
  } catch { /* 保持列表中的内容 */ }
}

async function saveEntry() {
  if (!sm.value || !draft.value.statement.trim()) return
  sv.value = true
  try {
    const entry = await st.updateMemoryById(sm.value.id, {
      statement: draft.value.statement.trim(),
      rationale: draft.value.rationale,
      kind: draft.value.kind,
      confidence: draft.value.confidence,
    })
    if (entry) {
      sm.value = entry
      flash('Saved')
    } else {
      flash('Save failed')
    }
  } finally {
    sv.value = false
  }
}

function askDelete() {
  if (!sm.value) return
  const target = sm.value
  confirmState.value = {
    open: true,
    title: 'Delete entry',
    message: `删除这条经验记录？此操作不可撤销。`,
    danger: true,
    action: async () => {
      const ok = await st.deleteMemoryById(target.id)
      if (ok) {
        sm.value = null
        draft.value = { kind: 'correction', statement: '', rationale: '', confidence: 0.8 }
        flash('Deleted')
      } else {
        flash('Delete failed')
      }
    },
  }
}

async function saveAgentNote() {
  const ok = await st.saveAgentMemoryContent()
  flash(ok ? 'Saved' : 'Save failed')
}

function askClearAgentNote() {
  confirmState.value = {
    open: true,
    title: 'Clear agent note',
    message: '清空该 Agent 的笔记？此操作不可撤销。',
    danger: true,
    action: async () => {
      const ok = await st.clearAgentMemoryContent()
      flash(ok ? 'Cleared' : 'Clear failed')
    },
  }
}

function onConfirm() {
  const action = confirmState.value.action
  confirmState.value.open = false
  action()
}

function formatTime(value: string): string {
  if (!value) return ''
  const d = new Date(value)
  if (Number.isNaN(d.getTime())) return ''
  const diff = Date.now() - d.getTime()
  const hours = Math.floor(diff / 3600000)
  const days = Math.floor(diff / 86400000)
  if (hours < 1) return 'Just now'
  if (hours < 24) return `${hours}h ago`
  if (days < 7) return `${days}d ago`
  return d.toLocaleDateString('en-US', { month: 'short', day: 'numeric' })
}

function createdLabel(value: string): string {
  if (!value) return ''
  const d = new Date(value)
  return Number.isNaN(d.getTime()) ? '' : d.toLocaleString('en-US', { hour: '2-digit', minute: '2-digit', hour12: true })
}

watch(() => st.agentMemoryScope, () => {
  if (st.selectedAgentType) st.fetchAgentMemoryContent()
})
</script>

<template>
  <Transition name="mem-fade">
    <div v-if="st.panelOpen" class="mem-overlay" @click.self="st.setPanelOpen(false)">
      <div class="mem-panel" @click.stop>
        <div class="mem-hdr">
          <div class="mem-hdr-row">
            <div class="mem-hdr-logo">
              <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5">
                <path d="M4 19.5A2.5 2.5 0 016.5 17H20" />
                <path d="M6.5 2H20v20H6.5A2.5 2.5 0 014 19.5v-15A2.5 2.5 0 016.5 2z" />
              </svg>
              <span class="mem-hdr-title">Experience Ledger</span>
            </div>
            <div class="mem-segment">
              <button :class="['mem-seg-btn', { active: st.mode === 'ledger' }]" @click="st.switchMode('ledger')">Ledger</button>
              <button :class="['mem-seg-btn', { active: st.mode === 'agent' }]" @click="st.switchMode('agent')">Agent Notes</button>
            </div>
            <button class="mem-close" @click="st.setPanelOpen(false)">
              <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                <line x1="18" y1="6" x2="6" y2="18" />
                <line x1="6" y1="6" x2="18" y2="18" />
              </svg>
            </button>
          </div>
          <div v-if="st.mode === 'ledger'" class="mem-stats">
            <div class="mem-stats-total">
              <span class="mem-stats-lbl">TOTAL</span>
              <span class="mem-stats-val">{{ st.totalMemories }}</span>
            </div>
            <div class="mem-stats-break">
              <div v-for="item in summary" :key="item.key" class="mem-stat-item">
                <div class="mem-stat-hdr"><span>{{ item.label }}</span><span>{{ item.count }}</span></div>
                <div class="mem-stat-track">
                  <div class="mem-stat-fill" :style="{ width: item.pct + '%', background: KIND_META[item.key].color }" />
                </div>
              </div>
            </div>
          </div>
          <div v-else class="mem-agent-info">
            <div class="mem-agent-info-left">
              <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="var(--accent)" stroke-width="1.5">
                <circle cx="12" cy="12" r="8" />
                <path d="M12 8v4l3 3" />
              </svg>
              <div>
                <div class="mem-agent-info-label">Agent Notes</div>
                <div class="mem-agent-info-desc">Agent 专属工作笔记，随会话注入</div>
              </div>
            </div>
            <div class="mem-agent-stats"><span>{{ st.wordCount }} words</span></div>
          </div>
        </div>

        <div v-if="st.mode === 'ledger'" class="mem-body">
          <div class="mem-sidebar">
            <div class="mem-sb-hdr">
              <div class="mem-search">
                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" class="mem-search-icon">
                  <circle cx="11" cy="11" r="8" />
                  <line x1="21" y1="21" x2="16.65" y2="16.65" />
                </svg>
                <input v-model="sq" class="mem-search-input" placeholder="Search experiences..." type="text" />
              </div>
              <button class="mem-new-btn" @click="sd = true">
                <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                  <line x1="12" y1="5" x2="12" y2="19" />
                  <line x1="5" y1="12" x2="19" y2="12" />
                </svg>
                New Entry
              </button>
            </div>
            <div class="mem-sb-body custom-scrollbar">
              <template v-for="key in kindKeys" :key="key">
                <div v-if="grouped[key] && grouped[key].length > 0" class="mem-group">
                  <button class="mem-group-hdr" @click="eg[key] = !eg[key]">
                    <span class="mem-group-title">{{ KIND_META[key].label }}</span>
                    <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" :class="{ rotated: !eg[key] }">
                      <polyline points="6 9 12 15 18 9" />
                    </svg>
                  </button>
                  <div v-show="eg[key]" class="mem-group-items">
                    <div
                      v-for="mem in grouped[key]"
                      :key="mem.id"
                      class="mem-item"
                      :class="{ active: sm?.id === mem.id }"
                      @click="selectEntry(mem)"
                    >
                      <div class="mem-item-name">{{ mem.statement }}</div>
                      <div class="mem-item-time">
                        <span v-if="mem.scope === 'global'" class="mem-item-scope">Global</span>
                        <span class="mem-item-conf" :style="{ color: KIND_META[key].color }">{{ mem.confidence.toFixed(2) }}</span>
                        <span>{{ formatTime(mem.updated_at) }}</span>
                      </div>
                    </div>
                  </div>
                </div>
              </template>
              <div v-if="st.memories.length === 0 && !st.loadingMemories" class="mem-empty">
                <svg width="32" height="32" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1" opacity="0.3">
                  <path d="M4 19.5A2.5 2.5 0 016.5 17H20" />
                  <path d="M6.5 2H20v20H6.5A2.5 2.5 0 014 19.5v-15A2.5 2.5 0 016.5 2z" />
                </svg>
                <span>No experiences yet</span>
              </div>
              <div v-if="st.loadingMemories" class="mem-loading"><span class="mem-spin" /><span>Loading...</span></div>
            </div>
          </div>

          <div class="mem-editor">
            <div class="mem-ed-hdr">
              <div class="mem-ed-title-row">
                <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="var(--accent)" stroke-width="1.5" style="opacity: .7">
                  <path d="M12 20h9" />
                  <path d="M16.5 3.5a2.1 2.1 0 013 3L7 19l-4 1 1-4z" />
                </svg>
                <div>
                  <h2 class="mem-ed-title">{{ sm ? 'Experience Details' : 'Select an Entry' }}</h2>
                  <p class="mem-ed-subtitle">{{ sm ? KIND_META[sm.kind]?.hint : 'Choose from the list' }}</p>
                </div>
              </div>
              <div v-if="sm" class="mem-ed-actions">
                <span v-if="notice" class="mem-notice">{{ notice }}</span>
                <button class="mem-ed-delete" @click="askDelete">Delete</button>
                <button class="mem-ed-save" :disabled="sv" @click="saveEntry">{{ sv ? 'Saving...' : 'Save' }}</button>
              </div>
            </div>
            <div class="mem-ed-body custom-scrollbar">
              <div v-if="sm" class="mem-ed-form">
                <div class="mem-field">
                  <label class="mem-field-lbl primary">Statement</label>
                  <textarea v-model="draft.statement" class="mem-statement" rows="3" placeholder="一句话结论" />
                </div>
                <div class="mem-row">
                  <div class="mem-field mem-field-half">
                    <label class="mem-field-lbl">Kind</label>
                    <select v-model="draft.kind" class="mem-select">
                      <option v-for="key in kindKeys" :key="key" :value="key">{{ KIND_META[key].label }}</option>
                    </select>
                  </div>
                  <div class="mem-field mem-field-half">
                    <label class="mem-field-lbl">Confidence</label>
                    <div class="mem-confidence">
                      <input v-model.number="draft.confidence" class="mem-range" type="range" min="0" max="1" step="0.05" />
                      <span class="mem-confidence-val">{{ draft.confidence.toFixed(2) }}</span>
                    </div>
                  </div>
                </div>
                <div class="mem-field">
                  <label class="mem-field-lbl">Rationale</label>
                  <div class="mem-content-box">
                    <textarea v-model="draft.rationale" class="mem-textarea" placeholder="为什么这样做，或失败的具体表现" />
                  </div>
                </div>
                <div class="mem-field">
                  <label class="mem-field-lbl">Scope</label>
                  <div class="mem-tags">
                    <span class="mem-tag"><span class="mem-tag-dot" />{{ sm.scope === 'global' ? 'Global — 所有项目' : 'Project — 仅当前项目' }}</span>
                  </div>
                </div>
                <div class="mem-field">
                  <label class="mem-field-lbl">Applies To</label>
                  <div class="mem-tags">
                    <span v-for="(tag, i) in (sm.applies_to?.tools || [])" :key="'t' + i" class="mem-tag">
                      <span class="mem-tag-dot" />{{ tag }}
                    </span>
                    <span v-for="(tag, i) in (sm.applies_to?.paths || [])" :key="'p' + i" class="mem-tag">
                      <span class="mem-tag-dot" />{{ tag }}
                    </span>
                    <span v-if="!(sm.applies_to?.tools || []).length && !(sm.applies_to?.paths || []).length" class="mem-field-hint">未限定工具或路径</span>
                  </div>
                </div>
                <div class="mem-field">
                  <label class="mem-field-lbl">Evidence</label>
                  <div class="mem-evidence">
                    <div v-if="!(sm.evidence || []).length" class="mem-field-hint">无来源记录</div>
                    <div v-for="(ev, i) in sm.evidence" :key="i" class="mem-evidence-item">
                      <span class="mem-evidence-meta">{{ (ev.conversation_uuid || 'history').slice(0, 8) }} · {{ createdLabel(sm.created_at) }}</span>
                      <span v-if="ev.quote" class="mem-evidence-quote">{{ ev.quote }}</span>
                    </div>
                  </div>
                </div>
                <div class="mem-meta">
                  <span>Hits: {{ sm.hits }}</span>
                  <span>Status: {{ sm.status }}</span>
                  <span>Last Sync: {{ formatTime(sm.updated_at) }}</span>
                </div>
              </div>
              <div v-else class="mem-no-sel">
                <svg width="40" height="40" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1" opacity="0.2">
                  <path d="M4 19.5A2.5 2.5 0 016.5 17H20" />
                  <path d="M6.5 2H20v20H6.5A2.5 2.5 0 014 19.5v-15A2.5 2.5 0 016.5 2z" />
                </svg>
                <h3>Select an entry to view</h3>
              </div>
            </div>
          </div>
        </div>

        <div v-else class="mem-body">
          <div class="mem-sidebar">
            <div class="mem-sb-hdr">
              <h3 class="mem-agent-sb-title">Select Agent</h3>
              <div class="mem-scope-row">
                <label class="mem-scope-lbl">Scope:</label>
                <select v-model="st.agentMemoryScope" class="mem-scope-select">
                  <option value="user">User</option>
                  <option value="project">Project</option>
                </select>
              </div>
            </div>
            <div class="mem-sb-body custom-scrollbar">
              <div
                v-for="ag in st.agents"
                :key="ag.name"
                class="mem-agent-item"
                :class="{ active: st.selectedAgentType === ag.name }"
                @click="st.selectAgentForMemory(ag.name)"
              >
                <div class="mem-agent-item-icon">
                  <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5">
                    <rect x="3" y="3" width="18" height="14" rx="2" />
                    <line x1="8" y1="21" x2="16" y2="21" />
                    <line x1="12" y1="17" x2="12" y2="21" />
                  </svg>
                </div>
                <div class="mem-agent-item-info">
                  <div class="mem-agent-item-name">{{ ag.name }}</div>
                  <div class="mem-agent-item-desc">{{ ag.description || 'AI Agent' }}</div>
                </div>
              </div>
              <div v-if="st.agents.length === 0" class="mem-empty"><span>No agents</span></div>
            </div>
          </div>
          <div class="mem-editor">
            <div class="mem-ed-hdr">
              <div class="mem-ed-title-row">
                <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="var(--accent)" stroke-width="1.5" style="opacity: .7">
                  <circle cx="12" cy="12" r="8" />
                  <path d="M12 8v4l3 3" />
                </svg>
                <div>
                  <h2 class="mem-ed-title">{{ st.selectedAgentType ? `${st.selectedAgentType} Note` : 'Select an Agent' }}</h2>
                  <p class="mem-ed-subtitle">Scope: <span class="mem-scope-badge">{{ st.agentMemoryScope }}</span></p>
                </div>
              </div>
              <div v-if="st.selectedAgentType" class="mem-ed-actions">
                <span v-if="notice" class="mem-notice">{{ notice }}</span>
                <button class="mem-ed-delete" :disabled="!st.agentMemoryContent" @click="askClearAgentNote">Clear</button>
                <button class="mem-ed-save" :disabled="st.savingAgentMemory" @click="saveAgentNote">{{ st.savingAgentMemory ? 'Saving...' : 'Save' }}</button>
              </div>
            </div>
            <div class="mem-ed-body custom-scrollbar">
              <div v-if="st.selectedAgentType" class="mem-ed-form">
                <div class="mem-field">
                  <label class="mem-field-lbl primary">Agent Note</label>
                  <p class="mem-field-hint">User 档跨项目注入，Project 档仅当前项目注入。</p>
                </div>
                <div class="mem-field">
                  <div class="mem-content-box large">
                    <textarea v-model="st.agentMemoryContent" class="mem-textarea large" placeholder="Enter persistent notes for this agent..." />
                  </div>
                </div>
                <div class="mem-agent-footer">
                  <span>{{ st.agentMemoryContent.length }} chars</span>
                  <span>{{ st.wordCount }} words</span>
                  <span class="mem-agent-hint">Click "Save" to persist changes</span>
                </div>
              </div>
              <div v-else class="mem-no-sel">
                <svg width="40" height="40" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1" opacity="0.2">
                  <circle cx="12" cy="12" r="8" />
                  <path d="M12 8v4l3 3" />
                </svg>
                <h3>Select an Agent</h3>
              </div>
            </div>
          </div>
        </div>
      </div>
      <CreateMemoryDialog v-if="sd" @close="sd = false" @created="selectEntry" />
      <ConfirmDialog
        :open="confirmState.open"
        :title="confirmState.title"
        :message="confirmState.message"
        confirm-label="Delete"
        cancel-label="Cancel"
        :danger="confirmState.danger"
        @confirm="onConfirm"
        @cancel="confirmState.open = false"
      />
    </div>
  </Transition>
</template>

<style scoped>
.mem-fade-enter-active,.mem-fade-leave-active{transition:opacity .2s}.mem-fade-enter-from,.mem-fade-leave-to{opacity:0}
.mem-overlay{position:fixed;inset:0;z-index:8000;background:rgba(0,0,0,.5);backdrop-filter:blur(2px);display:flex;align-items:center;justify-content:center;padding:24px}
.mem-panel{background:var(--bg-surface);border:1px solid var(--border);border-radius:8px;box-shadow:0 8px 40px rgba(0,0,0,.4);width:100%;max-width:1100px;max-height:85vh;display:flex;flex-direction:column;overflow:hidden;font-family:var(--font-mono);font-size:12px;color:var(--text)}
.mem-hdr{padding:20px 24px 16px;border-bottom:1px solid var(--border)}
.mem-hdr-row{display:flex;align-items:center;justify-content:space-between;margin-bottom:16px}
.mem-hdr-logo{display:flex;align-items:center;gap:8px;color:var(--accent)}
.mem-hdr-title{font-size:14px;font-weight:600;letter-spacing:.5px}
.mem-segment{display:flex;background:var(--bg-input);border-radius:20px;padding:2px}
.mem-seg-btn{padding:5px 14px;border-radius:20px;border:none;background:transparent;color:var(--fg-dim);cursor:pointer;font-size:11px;font-family:var(--font-mono);transition:all .15s}
.mem-seg-btn.active{background:var(--accent);color:#000}
.mem-close{background:none;border:none;color:var(--fg-dim);cursor:pointer;padding:4px;border-radius:4px}
.mem-close:hover{background:var(--bg-hover);color:var(--text)}
.mem-stats{display:flex;gap:32px;align-items:center}
.mem-stats-total{display:flex;flex-direction:column}
.mem-stats-lbl{font-size:9px;text-transform:uppercase;color:var(--fg-dim);letter-spacing:1px}
.mem-stats-val{font-size:22px;font-weight:700;color:var(--accent)}
.mem-stats-break{flex:1;display:flex;gap:12px}
.mem-stat-item{flex:1}
.mem-stat-hdr{display:flex;justify-content:space-between;font-size:8px;text-transform:uppercase;color:var(--fg-dim);margin-bottom:4px}
.mem-stat-track{height:4px;background:var(--bg-input);border-radius:2px;overflow:hidden}
.mem-stat-fill{height:100%;border-radius:2px;transition:width .3s}
.mem-agent-info{display:flex;align-items:center;justify-content:space-between}
.mem-agent-info-left{display:flex;align-items:center;gap:10px}
.mem-agent-info-label{font-size:13px;font-weight:600;color:var(--text)}
.mem-agent-info-desc{font-size:10px;color:var(--fg-dim)}
.mem-agent-stats{font-size:11px;color:var(--accent)}
.mem-body{flex:1;display:grid;grid-template-columns:280px 1fr;overflow:hidden;min-height:0}
.mem-sidebar{display:flex;flex-direction:column;border-right:1px solid var(--border);background:var(--bg-raised);overflow:hidden}
.mem-sb-hdr{padding:12px;border-bottom:1px solid var(--border)}
.mem-search{position:relative;margin-bottom:10px}
.mem-search-icon{position:absolute;left:8px;top:50%;transform:translateY(-50%);color:var(--fg-dim)}
.mem-search-input{width:100%;background:var(--bg-input);border:1px solid var(--border);border-radius:4px;padding:6px 10px 6px 28px;font-size:11px;color:var(--text);outline:none;font-family:var(--font-mono)}
.mem-search-input:focus{border-color:var(--accent)}
.mem-new-btn{width:100%;background:var(--accent);color:#000;border:none;border-radius:4px;padding:6px 10px;font-size:11px;font-weight:600;cursor:pointer;display:flex;align-items:center;justify-content:center;gap:6px;font-family:var(--font-mono)}
.mem-new-btn:hover{opacity:.85}
.mem-sb-body{flex:1;overflow-y:auto;padding:8px}
.mem-group{margin-bottom:6px}
.mem-group-hdr{width:100%;display:flex;align-items:center;justify-content:space-between;padding:6px 10px;background:transparent;border:none;cursor:pointer;color:var(--fg-dim);font-family:var(--font-mono);font-size:10px;text-transform:uppercase;letter-spacing:.5px}
.mem-group-hdr:hover{color:var(--text)}
.mem-group-hdr svg{transition:transform .15s}
.mem-group-hdr svg.rotated{transform:rotate(-90deg)}
.mem-group-items{margin-top:2px}
.mem-item{padding:8px 10px;border-radius:4px;cursor:pointer;transition:background .15s;border-left:3px solid transparent}
.mem-item:hover{background:var(--bg-hover)}
.mem-item.active{background:var(--bg-input);border-left-color:var(--accent)}
.mem-item-name{font-size:11px;color:var(--text);overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.mem-item-time{font-size:9px;color:var(--fg-dim);margin-top:2px;display:flex;gap:8px;align-items:center}
.mem-item-conf{font-weight:600}
.mem-item-scope{font-size:9px;color:var(--cyan);border:1px solid var(--border-subtle);border-radius:6px;padding:0 4px;line-height:1.5}
.mem-empty,.mem-loading{padding:24px 16px;text-align:center;color:var(--fg-dim);font-size:11px;display:flex;flex-direction:column;align-items:center;gap:8px}
.mem-spin{width:14px;height:14px;border:2px solid var(--fg-dim);border-top-color:var(--accent);border-radius:50%;animation:mem-spin .6s linear infinite;display:inline-block}
@keyframes mem-spin{to{transform:rotate(360deg)}}
.mem-editor{display:flex;flex-direction:column;overflow:hidden}
.mem-ed-hdr{padding:14px 24px;display:flex;align-items:center;justify-content:space-between;border-bottom:1px solid var(--border)}
.mem-ed-title-row{display:flex;align-items:center;gap:10px}
.mem-ed-title{font-size:13px;font-weight:600;color:var(--text);margin:0}
.mem-ed-subtitle{font-size:10px;color:var(--fg-dim);margin:2px 0 0}
.mem-ed-actions{display:flex;gap:8px;align-items:center}
.mem-notice{font-size:10px;color:var(--green);margin-right:4px}
.mem-ed-delete{font-size:10px;color:var(--fg-dim);background:none;border:none;padding:5px 12px;cursor:pointer;font-family:var(--font-mono);border-radius:4px}
.mem-ed-delete:hover{color:var(--red);background:var(--bg-hover)}
.mem-ed-delete:disabled{opacity:.4;cursor:not-allowed}
.mem-ed-save{font-size:10px;font-weight:600;background:var(--accent);color:#000;border:none;padding:5px 16px;border-radius:4px;cursor:pointer;font-family:var(--font-mono)}
.mem-ed-save:hover{opacity:.85}
.mem-ed-save:disabled{opacity:.4;cursor:not-allowed}
.mem-ed-body{flex:1;overflow-y:auto;padding:24px 32px}
.mem-ed-form{max-width:620px}
.mem-field{margin-bottom:24px}
.mem-row{display:flex;gap:20px}
.mem-field-half{flex:1}
.mem-field-lbl{display:block;font-size:8px;font-weight:700;text-transform:uppercase;letter-spacing:1.5px;color:var(--fg-dim);margin-bottom:6px}
.mem-field-lbl.primary{color:var(--accent)}
.mem-field-hint{font-size:10px;color:var(--fg-dim);margin:4px 0 0;font-style:italic}
.mem-statement{width:100%;background:var(--bg-input);border:1px solid var(--border);border-radius:6px;padding:12px;font-size:13px;color:var(--text);line-height:1.6;resize:vertical;outline:none;font-family:var(--font-mono)}
.mem-statement:focus{border-color:var(--accent)}
.mem-select{width:100%;background:var(--bg-input);border:1px solid var(--border);border-radius:4px;padding:7px 10px;font-size:11px;color:var(--text);outline:none;font-family:var(--font-mono);cursor:pointer}
.mem-select:focus{border-color:var(--accent)}
.mem-confidence{display:flex;align-items:center;gap:10px}
.mem-range{flex:1;accent-color:var(--accent)}
.mem-confidence-val{font-size:11px;color:var(--accent);min-width:32px}
.mem-tags{display:flex;flex-wrap:wrap;gap:6px}
.mem-tag{display:flex;align-items:center;gap:5px;padding:4px 10px;border-radius:12px;background:var(--bg-input);font-size:10px;color:var(--text)}
.mem-tag-dot{width:5px;height:5px;border-radius:50%;background:var(--accent)}
.mem-content-box{background:var(--bg-input);border:1px solid var(--border);border-radius:6px;padding:16px;min-height:120px}
.mem-content-box.large{min-height:300px}
.mem-textarea{width:100%;background:transparent;border:none;padding:0;font-size:12px;color:var(--text);line-height:1.6;min-height:100px;resize:vertical;outline:none;font-family:var(--font-mono)}
.mem-textarea.large{min-height:280px}
.mem-evidence{display:flex;flex-direction:column;gap:8px}
.mem-evidence-item{background:var(--bg-input);border-left:2px solid var(--border);border-radius:4px;padding:8px 10px;display:flex;flex-direction:column;gap:4px}
.mem-evidence-meta{font-size:9px;color:var(--fg-dim)}
.mem-evidence-quote{font-size:10px;color:var(--text);line-height:1.5}
.mem-meta{padding-top:16px;border-top:1px solid var(--border);font-size:9px;color:var(--fg-dim);display:flex;gap:16px;font-style:italic}
.mem-agent-footer{padding-top:16px;border-top:1px solid var(--border);display:flex;gap:16px;font-size:10px;color:var(--fg-dim)}
.mem-agent-hint{font-style:italic;margin-left:auto}
.mem-no-sel{text-align:center;padding:60px 0;color:var(--fg-dim)}
.mem-no-sel h3{font-size:14px;font-weight:400;margin:12px 0 0}
.mem-agent-sb-title{font-size:12px;font-weight:600;color:var(--text);margin:0 0 10px}
.mem-scope-row{display:flex;align-items:center;gap:8px}
.mem-scope-lbl{font-size:10px;color:var(--fg-dim)}
.mem-scope-select{flex:1;background:var(--bg-input);border:1px solid var(--border);border-radius:4px;padding:5px 8px;font-size:10px;color:var(--text);cursor:pointer;outline:none;font-family:var(--font-mono)}
.mem-scope-select:focus{border-color:var(--accent)}
.mem-agent-item{display:flex;align-items:center;gap:10px;padding:10px;border-radius:4px;cursor:pointer;transition:background .15s;border-left:3px solid transparent}
.mem-agent-item:hover{background:var(--bg-hover)}
.mem-agent-item.active{background:var(--bg-input);border-left-color:var(--accent)}
.mem-agent-item-icon{width:28px;height:28px;border-radius:50%;background:var(--bg-input);display:flex;align-items:center;justify-content:center;color:var(--accent);flex-shrink:0}
.mem-agent-item-info{flex:1;min-width:0}
.mem-agent-item-name{font-size:11px;font-weight:500;color:var(--text);overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.mem-agent-item-desc{font-size:9px;color:var(--fg-dim);overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.mem-scope-badge{display:inline-block;padding:1px 6px;background:var(--bg-input);border-radius:3px;font-size:9px;font-weight:600;text-transform:uppercase;color:var(--accent)}
</style>
