import { useGatewayStore } from '../stores/gateway'

export type MemoryKind = 'correction' | 'constraint' | 'preference' | 'pitfall' | 'recipe' | 'reference'
export type AgentMemoryScope = 'user' | 'project'
export type MemoryScope = 'project' | 'global'

export interface MemoryAppliesTo {
  tools?: string[]
  paths?: string[]
  keywords?: string[]
}

export interface MemoryEvidence {
  conversation_uuid?: string
  message_id?: string
  quote?: string
  timestamp?: number
}

export interface MemoryEntry {
  id: string
  kind: MemoryKind
  scope: MemoryScope
  statement: string
  rationale: string
  applies_to: MemoryAppliesTo
  evidence: MemoryEvidence[]
  confidence: number
  status: string
  hits: number
  created_at: string
  updated_at: string
}

export interface MemoryStats {
  success: boolean
  total: number
  active: number
  superseded: number
  retired: number
  by_kind: Record<string, number>
}

export interface Agent {
  name: string
  description: string
  current: boolean
}

function gw() {
  return useGatewayStore()
}

export async function loadMemories(kind?: MemoryKind) {
  return await gw().request<any>('memory.list', kind ? { kind } : {})
}

export async function loadMemoryStats(): Promise<MemoryStats | null> {
  const r = await gw().request<any>('memory.stats')
  return r?.success ? r : null
}

export async function getMemoryById(id: string): Promise<MemoryEntry | null> {
  const r = await gw().request<any>('memory.get', { id })
  return r.success ? r.memory : null
}

export async function createMemory(
  kind: MemoryKind,
  statement: string,
  rationale: string,
  appliesTo?: MemoryAppliesTo,
  confidence?: number,
  scope?: MemoryScope,
): Promise<MemoryEntry | null> {
  const r = await gw().request<any>('memory.create', {
    kind, statement, rationale, applies_to: appliesTo || {}, confidence, scope,
  })
  return r.success ? r.memory : null
}

export async function updateMemory(
  id: string,
  data: { statement?: string; rationale?: string; kind?: MemoryKind; applies_to?: MemoryAppliesTo; confidence?: number },
): Promise<MemoryEntry | null> {
  const r = await gw().request<any>('memory.update', { id, ...data })
  return r.success ? r.memory : null
}

export async function deleteMemory(id: string): Promise<boolean> {
  const r = await gw().request<any>('memory.delete', { id })
  return r.success as boolean
}

export async function loadAgentMemory(at: string, s: AgentMemoryScope): Promise<string> {
  const r = await gw().request<any>('agent-memory.get', { agent_type: at, scope: s })
  return r.success ? r.content : ''
}

export async function saveAgentMemory(at: string, c: string, s: AgentMemoryScope): Promise<boolean> {
  const r = await gw().request<any>('agent-memory.set', { agent_type: at, content: c, scope: s })
  return r.success as boolean
}

export async function clearAgentMemory(at: string, s: AgentMemoryScope): Promise<boolean> {
  const r = await gw().request<any>('agent-memory.clear', { agent_type: at, scope: s })
  return r.success as boolean
}

export async function loadAgents(): Promise<Agent[]> {
  const r = await gw().request<any>('agent.list')
  return r.agents || []
}
