import { useGatewayStore } from '../stores/gateway'
export type MemoryType = 'user'|'feedback'|'project'|'reference'
export type AgentMemoryScope = 'user'|'project'|'local'
export interface MemoryEntry { name:string;memory_type:MemoryType;description:string;content:string;tags:string[];created_at:string;updated_at:string;file_path?:string }
export interface Agent { name:string;description:string;current:boolean }
function gw(){return useGatewayStore()}
export async function loadMemories(mt?:MemoryType){return await gw().request<any>('memory.list',mt?{memory_type:mt}:{})}
export async function getMemoryByName(name:string):Promise<MemoryEntry|null>{const r=await gw().request<any>('memory.get',{name});return r.success?r.memory:null}
export async function createMemory(name:string,mt:MemoryType,desc:string,content:string,tags?:string[]):Promise<MemoryEntry|null>{const r=await gw().request<any>('memory.create',{name,memory_type:mt,description:desc,content,tags:tags||[]});return r.success?r.memory:null}
export async function updateMemory(name:string,data:{description?:string;content?:string;tags?:string[]}):Promise<MemoryEntry|null>{const r=await gw().request<any>('memory.update',{name,...data});return r.success?r.memory:null}
export async function deleteMemory(name:string):Promise<boolean>{const r=await gw().request<any>('memory.delete',{name});return r.success as boolean}
export async function loadAgentMemory(at:string,s:AgentMemoryScope):Promise<string>{const r=await gw().request<any>('agent-memory.get',{agent_type:at,scope:s});return r.success?r.content:''}
export async function saveAgentMemory(at:string,c:string,s:AgentMemoryScope):Promise<boolean>{const r=await gw().request<any>('agent-memory.set',{agent_type:at,content:c,scope:s});return r.success as boolean}
export async function clearAgentMemory(at:string,s:AgentMemoryScope):Promise<boolean>{const r=await gw().request<any>('agent-memory.clear',{agent_type:at,scope:s});return r.success as boolean}
export async function loadAgents():Promise<Agent[]>{const r=await gw().request<any>('agent.list');return r.agents||[]}
