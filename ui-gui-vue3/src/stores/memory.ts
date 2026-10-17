import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import type { MemoryKind, AgentMemoryScope, MemoryEntry, Agent, MemoryStats } from '../api/memory'
import {
  loadMemories, loadMemoryStats, getMemoryById, createMemory, updateMemory, deleteMemory,
  loadAgentMemory, saveAgentMemory, clearAgentMemory, loadAgents,
} from '../api/memory'

export const useMemoryStore = defineStore('memory', () => {
  const panelOpen = ref(false)
  const mode = ref<'ledger' | 'agent'>('ledger')
  const memories = ref<MemoryEntry[]>([])
  const stats = ref<MemoryStats | null>(null)
  const loadingMemories = ref(false)
  const agents = ref<Agent[]>([])
  const loadingAgents = ref(false)
  const selectedAgentType = ref<string | null>(null)
  const agentMemoryScope = ref<AgentMemoryScope>('project')
  const agentMemoryContent = ref('')
  const savingAgentMemory = ref(false)

  function togglePanel() {
    panelOpen.value = !panelOpen.value
    if (panelOpen.value) {
      if (mode.value === 'ledger') fetchMemories()
      else fetchAgents()
    }
  }

  function setPanelOpen(v: boolean) {
    panelOpen.value = v
    if (v) {
      if (mode.value === 'ledger') fetchMemories()
      else fetchAgents()
    }
  }

  function switchMode(m: 'ledger' | 'agent') {
    mode.value = m
    if (m === 'ledger') fetchMemories()
    else fetchAgents()
  }

  function selectAgentForMemory(name: string) {
    selectedAgentType.value = name
    fetchAgentMemoryContent()
  }

  async function fetchMemories() {
    loadingMemories.value = true
    try {
      const res = await loadMemories()
      if (res.success) memories.value = res.memories
      stats.value = await loadMemoryStats()
    } catch (e) {
      console.warn('Failed to load experiences:', e)
    } finally {
      loadingMemories.value = false
    }
  }

  async function fetchMemoryById(id: string): Promise<MemoryEntry | null> {
    return await getMemoryById(id)
  }

  async function createNewMemory(
    kind: MemoryKind,
    statement: string,
    rationale: string,
    appliesTo?: { tools?: string[]; paths?: string[]; keywords?: string[] },
    confidence?: number,
    scope?: 'project' | 'global',
  ): Promise<MemoryEntry | null> {
    const entry = await createMemory(kind, statement, rationale, appliesTo, confidence, scope)
    if (entry) {
      memories.value.unshift(entry)
      stats.value = await loadMemoryStats()
    }
    return entry
  }

  async function updateMemoryById(
    id: string,
    data: { statement?: string; rationale?: string; kind?: MemoryKind; applies_to?: any; confidence?: number },
  ): Promise<MemoryEntry | null> {
    const entry = await updateMemory(id, data)
    if (entry) {
      const idx = memories.value.findIndex(m => m.id === id)
      if (idx >= 0) memories.value[idx] = entry
    }
    return entry
  }

  async function deleteMemoryById(id: string): Promise<boolean> {
    const ok = await deleteMemory(id)
    if (ok) {
      memories.value = memories.value.filter(m => m.id !== id)
      stats.value = await loadMemoryStats()
    }
    return ok
  }

  async function fetchAgents() {
    loadingAgents.value = true
    try {
      agents.value = await loadAgents()
    } catch (e) {
      console.warn('Failed to load agents:', e)
    } finally {
      loadingAgents.value = false
    }
  }

  async function fetchAgentMemoryContent() {
    if (!selectedAgentType.value) return
    try {
      agentMemoryContent.value = await loadAgentMemory(selectedAgentType.value, agentMemoryScope.value)
    } catch (e) {
      console.warn('Failed to load agent note:', e)
      agentMemoryContent.value = ''
    }
  }

  async function saveAgentMemoryContent(): Promise<boolean> {
    if (!selectedAgentType.value) return false
    const a = selectedAgentType.value
    savingAgentMemory.value = true
    try {
      return await saveAgentMemory(a, agentMemoryContent.value, agentMemoryScope.value)
    } catch (e) {
      console.warn('Failed to save agent note:', e)
      return false
    } finally {
      savingAgentMemory.value = false
    }
  }

  async function clearAgentMemoryContent(): Promise<boolean> {
    if (!selectedAgentType.value) return false
    const a = selectedAgentType.value
    try {
      const ok = await clearAgentMemory(a, agentMemoryScope.value)
      if (ok) agentMemoryContent.value = ''
      return ok
    } catch (e) {
      console.warn('Failed to clear agent note:', e)
      return false
    }
  }

  const totalMemories = computed(() => memories.value.length)

  const kindCounts = computed(() => {
    const counts: Record<string, number> = {}
    for (const m of memories.value) counts[m.kind] = (counts[m.kind] || 0) + 1
    return counts
  })

  const kindPercent = (kind: string) => {
    const t = totalMemories.value || 1
    return Math.round(((kindCounts.value[kind] || 0) / t) * 100)
  }

  const wordCount = computed(() =>
    agentMemoryContent.value ? agentMemoryContent.value.trim().split(/\s+/).filter(w => w.length > 0).length : 0,
  )

  return {
    panelOpen, mode, memories, stats, loadingMemories, agents, loadingAgents,
    selectedAgentType, agentMemoryScope, agentMemoryContent, savingAgentMemory,
    totalMemories, kindCounts, kindPercent, wordCount,
    togglePanel, setPanelOpen, switchMode, selectAgentForMemory,
    fetchMemories, fetchMemoryById, createNewMemory, updateMemoryById, deleteMemoryById,
    fetchAgents, fetchAgentMemoryContent, saveAgentMemoryContent, clearAgentMemoryContent,
  }
})
