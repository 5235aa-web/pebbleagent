<script setup lang="ts">
import { ref, computed, onMounted, onUnmounted } from 'vue'
import { useGatewayStore } from '../stores/gateway'
import { useChatStore, type Conversation } from '../stores/chat'
import ConfirmDialog from './ConfirmDialog.vue'
import Tooltip from './Tooltip.vue'

interface SearchResult { message_id: number; role: 'user' | 'assistant' | 'tool'; snippet: string; created_at: number; session_id: string; session_title: string }

const g = useGatewayStore()
const cs = useChatStore()
const sq = ref('')
const sr = ref<SearchResult[]>([])
const sg = ref(false)
let st: ReturnType<typeof setTimeout> | undefined
const menuOpen = ref(false)
const menuPos = ref({ x: 0, y: 0 })
const renaming = ref(false)
const renameValue = ref('')
const renameTarget = ref('')
const deleteTarget = ref<string | null>(null)
const menuRef = ref<HTMLDivElement | null>(null)

function handleGlobalMousedown(e: MouseEvent) {
  if (menuOpen.value && menuRef.value && !menuRef.value.contains(e.target as Node)) {
    menuOpen.value = false
  }
}
onMounted(() => document.addEventListener('mousedown', handleGlobalMousedown))
onUnmounted(() => document.removeEventListener('mousedown', handleGlobalMousedown))

function openMenu(e: MouseEvent, conv: Conversation) {
  e.stopPropagation()
  const rect = (e.target as HTMLElement).getBoundingClientRect()
  menuPos.value = { x: rect.left - 4, y: rect.top + rect.height / 2 }
  menuOpen.value = true
  renameTarget.value = conv.id
  renameValue.value = conv.title
}

function handlePin(e: MouseEvent) {
  e.stopPropagation()
  cs.togglePin(renameTarget.value)
  menuOpen.value = false
}

function handleRenameStart(e: MouseEvent) {
  e.stopPropagation()
  renaming.value = true
  menuOpen.value = false
}

async function handleRenameSubmit() {
  const t = renameValue.value.trim()
  if (t && renameTarget.value) {
    cs.renameConv(renameTarget.value, t)
  }
  renaming.value = false
  renameTarget.value = ''
}

function handleDelete(e: MouseEvent) {
  e.stopPropagation()
  deleteTarget.value = renameTarget.value
  menuOpen.value = false
}

function handleDeleteConfirm() {
  if (deleteTarget.value) {
    cs.deleteConv(deleteTarget.value)
  }
  deleteTarget.value = null
}

async function handleExport(e: MouseEvent) {
  e.stopPropagation()
  menuOpen.value = false
  try {
    const r = await g.request<{ filepath: string }>('session.export_save', { session_id: renameTarget.value })
    if (r?.filepath) {
      const sp = r.filepath.split('\\').pop()
      window.dispatchEvent(new CustomEvent('gateway-toast', { detail: { message: `Exported: ${sp}` } }))
    }
  } catch { /* ignore */ }
}

function onSearch() {
  if (st) clearTimeout(st)
  const q = sq.value.trim()
  if (!q) { sr.value = []; sg.value = false; return }
  sg.value = true
  st = setTimeout(async () => {
    try {
      const r = await g.request<{ results: SearchResult[] }>('session.search', { q, limit: 30 })
      sr.value = r.results || []
    } catch { sr.value = [] }
    finally { sg.value = false }
  }, 300)
}

function sel(id: string, mid?: number) {
  sq.value = ''; sr.value = []
  if (!id) return
  cs.selectConversation(id)
  if (mid) {
    setTimeout(() => {
      window.dispatchEvent(new CustomEvent('gateway:scroll-to-message', { detail: { messageId: mid } }))
    }, 600)
  }
}

function handleNew() { window.dispatchEvent(new CustomEvent('gateway:create-session')) }

const pnd = computed(() => cs.conversations.filter(c => c.pinned))
const unp = computed(() => cs.conversations.filter(c => !c.pinned))
const is = computed(() => sq.value.trim().length > 0)

// ===== 未 pinned 会话的时间分组 =====
// 桶从近到远（互斥、覆盖全部时间；顺序判定天然处理重叠，如周一的"昨天"=上周日）：
//   today → yesterday → this week（周一 00:00 起）→ this month（1 日 00:00 起）→ last month →
//   earlier（上月之前、今年之内的更早）→ last year（今年之前，兜底）
const TIME_GROUPS = ['today', 'yesterday', 'this week', 'this month', 'last month', 'earlier', 'last year'] as const

function bucketOf(ts: number | undefined, now: Date): string {
  const t = (ts ?? 0) * 1000
  const y = now.getFullYear(), m = now.getMonth(), d = now.getDate()
  const todayStart = new Date(y, m, d).getTime()
  const yesterdayStart = new Date(y, m, d - 1).getTime()
  const weekStart = new Date(y, m, d - ((now.getDay() + 6) % 7)).getTime() // getDay: 0=周日，距周一 = (getDay+6)%7
  const monthStart = new Date(y, m, 1).getTime()
  const lastMonthStart = new Date(y, m - 1, 1).getTime()
  const yearStart = new Date(y, 0, 1).getTime()
  if (t >= todayStart) return 'today'
  if (t >= yesterdayStart) return 'yesterday'
  if (t >= weekStart) return 'this week'
  if (t >= monthStart) return 'this month'
  if (t >= lastMonthStart) return 'last month'
  if (t >= yearStart) return 'earlier'
  return 'last year'
}

const timeGroups = computed(() => {
  const now = new Date()
  const buckets = new Map<string, Conversation[]>()
  for (const c of unp.value) {
    const label = bucketOf(c.lastActivityAt ?? c.startedAt, now)
    if (!buckets.has(label)) buckets.set(label, [])
    buckets.get(label)!.push(c)
  }
  return TIME_GROUPS.filter(l => buckets.has(l)).map(l => ({ label: l as string, items: buckets.get(l)! }))
})
</script>

<template>
  <div class="sidebar">
    <div class="sb-search">
      <span class="sb-search-icon">
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/></svg>
      </span>
      <input type="text" placeholder="search conversations..." v-model="sq" @input="onSearch" @keydown.escape="sq='';sr=[];($event.target as HTMLInputElement).blur()"/>
      <span v-if="sg" class="sb-search-spinner"/>
      <button v-if="sq&&!sg" class="sb-search-clear" @click="sq='';sr=[]">&times;</button>
    </div>

    <div class="sb-list">
      <template v-if="is">
        <template v-if="sr.length>0">
          <div class="sb-label">search results &middot; {{ sr.length }}</div>
          <div v-for="(r,i) in sr" :key="`${r.session_id}-${r.message_id}-${i}`" class="sr-item" @click="sel(r.session_id, r.message_id)">
            <div class="sr-header">
              <span class="sr-title">{{ r.session_title }}</span>
              <span class="sr-role" :class="'sr-role-'+(r.role==='user'?'user':'agent')">{{ r.role==='user'?'user':'agent' }}</span>
            </div>
            <div class="sr-snippet">{{ r.snippet }}</div>
          </div>
        </template>
        <template v-else>
          <div class="sb-empty">{{ sg?'searching...':'no results found' }}</div>
        </template>
      </template>
      <template v-else>
        <template v-if="pnd.length>0">
          <div class="sb-label">pinned</div>
          <div v-for="c in pnd" :key="c.id" class="conv" :class="{active:c.id===g.sessionId, pinned:c.pinned}" @click="cs.selectConversation(c.id)">
            <div class="inf">
              <div class="ttl">
                <span v-if="c.pinned" class="sb-pin-icon"><svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><path d="M12 2L15.09 8.26L22 9.27L17 14.14L18.18 21.02L12 17.77L5.82 21.02L7 14.14L2 9.27L8.91 8.26L12 2z"/></svg></span>
                {{ c.title }}
              </div>
              <div class="meta">{{ c.meta }}</div>
            </div>
            <Tooltip text="More">
              <button class="sb-dot-btn" @click="(e:MouseEvent)=>{renameTarget=c.id;renameValue=c.title;openMenu(e,c)}">
                <svg width="14" height="14" viewBox="0 0 24 24" fill="currentColor"><circle cx="12" cy="5" r="2"/><circle cx="12" cy="12" r="2"/><circle cx="12" cy="19" r="2"/></svg>
              </button>
            </Tooltip>
          </div>
        </template>
        <template v-for="(grp, gi) in timeGroups" :key="grp.label">
          <div class="sb-label" :style="{marginTop:gi===0&&pnd.length>0?4:0}">{{ grp.label }}</div>
          <div v-for="c in grp.items" :key="c.id" class="conv" :class="{active:c.id===g.sessionId}" @click="cs.selectConversation(c.id)">
            <div class="inf">
              <div class="ttl">{{ c.title }}</div>
              <div class="meta">{{ c.meta }}</div>
            </div>
            <Tooltip text="More">
              <button class="sb-dot-btn" @click="(e:MouseEvent)=>{renameTarget=c.id;renameValue=c.title;openMenu(e,c)}">
                <svg width="14" height="14" viewBox="0 0 24 24" fill="currentColor"><circle cx="12" cy="5" r="2"/><circle cx="12" cy="12" r="2"/><circle cx="12" cy="19" r="2"/></svg>
              </button>
            </Tooltip>
          </div>
        </template>
        <div v-if="cs.conversations.length===0" class="sb-empty">no conversations yet</div>
      </template>
    </div>

    <div class="sb-ft">
      <button class="btn-n" @click="handleNew">
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/></svg>
        new conversation
      </button>
    </div>

    <!-- Context menu -->
    <div v-if="menuOpen" ref="menuRef" class="ctx-menu" :style="{left:menuPos.x+'px',top:menuPos.y+'px'}">
      <button class="ctx-item" @click="handlePin">
        <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><path d="M12 2L15.09 8.26L22 9.27L17 14.14L18.18 21.02L12 17.77L5.82 21.02L7 14.14L2 9.27L8.91 8.26L12 2z"/></svg>
        Pin
      </button>
      <button class="ctx-item" @click="handleRenameStart">
        <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><path d="M11 4H4a2 2 0 00-2 2v14a2 2 0 002 2h14a2 2 0 002-2v-7"/><path d="M18.5 2.5a2.121 2.121 0 013 3L12 15l-4 1 1-4 9.5-9.5z"/></svg>
        Rename
      </button>
      <button class="ctx-item" @click="handleExport">
        <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><path d="M14 2H6a2 2 0 00-2 2v16a2 2 0 002 2h12a2 2 0 002-2V8z"/><polyline points="14 2 14 8 20 8"/><line x1="16" y1="13" x2="8" y2="13"/><line x1="16" y1="17" x2="8" y2="17"/></svg>
        Export MD
      </button>
      <div class="ctx-divider"/>
      <button class="ctx-item danger" @click="handleDelete">
        <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><polyline points="3 6 5 6 21 6"/><path d="M19 6v14a2 2 0 01-2 2H7a2 2 0 01-2-2V6m3 0V4a2 2 0 012-2h4a2 2 0 012 2v2"/></svg>
        Delete
      </button>
    </div>

    <!-- Rename overlay -->
    <div v-if="renaming" class="cfm-overlay" @click.self="renaming=false">
      <div class="cfm-dialog">
        <div class="cfm-hdr cfm-hdr-primary"><span class="cfm-title">Rename</span></div>
        <div class="cfm-body"><input class="sb-rename-input" style="width:100%" v-model="renameValue" @keydown.enter="handleRenameSubmit" @keydown.escape="renaming=false" autofocus/></div>
        <div class="cfm-actions"><button class="cfm-btn cfm-btn-cancel" @click="renaming=false">Cancel</button><button class="cfm-btn cfm-btn-primary" @click="handleRenameSubmit">OK</button></div>
      </div>
    </div>

    <ConfirmDialog :open="deleteTarget!==null" title="Delete conversation" :message="`Are you sure you want to delete this conversation? This action cannot be undone.`" confirmLabel="Delete" cancelLabel="Cancel" :danger="true" @confirm="handleDeleteConfirm" @cancel="deleteTarget=null"/>
  </div>
</template>
