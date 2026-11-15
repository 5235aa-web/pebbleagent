<template>
  <div
    class="overlay"
    id="forwardSessionDialog"
    :class="{ open: forwardState.pickerOpen }"
    :aria-hidden="forwardState.pickerOpen ? 'false' : 'true'"
    @click.self="closeForwardPicker"
  >
    <div class="sheet forward-sheet" role="dialog" aria-modal="true" aria-label="Forward to session">
      <div class="sheet-hdr">
        <span class="sheet-ico">
          <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9">
            <polyline points="15 14 20 9 15 4" />
            <path d="M4 20v-7a4 4 0 0 1 4-4h12" />
          </svg>
        </span>
        <div class="sheet-title">
          Forward to session
          <small>{{ forwardState.selectedIds.length }} {{ forwardState.selectedIds.length === 1 ? 'message' : 'messages' }} selected · Merged and sent immediately to the target session Agent (current project only)</small>
        </div>
        <button class="sheet-x" aria-label="Close" :disabled="forwardState.submitting" @click="closeForwardPicker">&#215;</button>
      </div>

      <div class="fwd-modal-body">
        <div class="fwd-search-row">
          <div class="fwd-search-box">
            <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="11" cy="11" r="7.5"/><line x1="21" y1="21" x2="16.5" y2="16.5"/></svg>
            <input v-model="query" type="text" placeholder="Search sessions in current project…" />
          </div>
          <button
            class="btn btn-ghost btn-sm"
            type="button"
            :disabled="creating || forwardState.submitting"
            @click="createTargetSession"
          >{{ creating ? 'Creating…' : '+ New session' }}</button>
        </div>

        <div class="fwd-session-list">
          <button
            v-for="s in filteredSessions"
            :key="s.id"
            type="button"
            class="fwd-session-item"
            :class="{
              active: forwardState.targetSessionId === s.id,
              current: s.id === sessionState.activeId
            }"
            :disabled="s.id === sessionState.activeId || forwardState.submitting"
            @click="pickSession(s.id)"
          >
            <span class="fsi-radio" :class="{ checked: forwardState.targetSessionId === s.id }"></span>
            <span class="fsi-main">
              <span class="fsi-title">{{ s.title || 'Untitled session' }}</span>
              <span class="fsi-meta">
                <span>{{ s.msgs }} msgs</span>
                <span>·</span>
                <span>{{ s.time }}</span>
                <span v-if="s.pinned">· Pinned</span>
              </span>
            </span>
            <span v-if="s.id === sessionState.activeId" class="fsi-tag">Current</span>
          </button>

          <div v-if="!filteredSessions.length" class="fwd-empty">
            {{ query.trim() ? 'No matching sessions found' : 'No other sessions in this project — click "+ New session" above' }}
          </div>
        </div>
      </div>

      <div class="sheet-foot">
        <span class="sf-hint">
          {{ selectedTarget ? `Switch to "${selectedTarget.title || 'Untitled session'}" and send immediately` : 'Select a target session' }}
        </span>
        <button class="btn btn-ghost btn-sm" type="button" :disabled="forwardState.submitting" @click="closeForwardPicker">Cancel</button>
        <button
          class="btn btn-primary btn-sm"
          type="button"
          :disabled="!forwardState.targetSessionId || forwardState.submitting"
          @click="confirmForward"
        >{{ forwardState.submitting ? 'Sending…' : 'Confirm & Send' }}</button>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { sessions, sessionState, projects } from '../data/store'
import { gateway } from '../data/gateway'
import { forwardState, closeForwardPicker } from '../data/forward'
import { forwardMessages, refreshSessionList, toast } from '../data/wire'

const query = ref('')
const creating = ref(false)

watch(
  () => forwardState.pickerOpen,
  open => {
    if (open) query.value = ''
  }
)

const filteredSessions = computed(() => {
  const q = query.value.trim().toLowerCase()
  if (!q) return sessions
  return sessions.filter(s => (s.title || '').toLowerCase().includes(q))
})

const selectedTarget = computed(() =>
  sessions.find(s => s.id === forwardState.targetSessionId) || null
)

function pickSession(id: string): void {
  if (id === sessionState.activeId || forwardState.submitting) return
  forwardState.targetSessionId = id
}

async function createTargetSession(): Promise<void> {
  if (!gateway.connected || creating.value || forwardState.submitting) return
  creating.value = true
  try {
    const projectPath = projects.active || sessionState.projectPath || ''
    const res = await gateway.request<{ session_id?: string; id?: string }>('session.create', {
      title: 'New conversation',
      project_path: projectPath
    })
    const sid = String(res?.session_id ?? res?.id ?? '')
    if (!sid) {
      toast('Create failed', 'Unable to create target session.', 'err')
      return
    }
    await refreshSessionList()
    forwardState.targetSessionId = sid
  } catch (e) {
    toast('Create failed', e instanceof Error ? e.message : 'The gateway refused to create the session.', 'err')
  } finally {
    creating.value = false
  }
}

async function confirmForward(): Promise<void> {
  if (!forwardState.targetSessionId || !forwardState.selectedIds.length) return
  await forwardMessages(forwardState.targetSessionId, [...forwardState.selectedIds])
}

function onKey(e: KeyboardEvent): void {
  if (!forwardState.pickerOpen) return
  if (e.key === 'Escape') closeForwardPicker()
}

onMounted(() => window.addEventListener('keydown', onKey))
onBeforeUnmount(() => window.removeEventListener('keydown', onKey))
</script>
