import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import type { MemoryType, AgentMemoryScope, MemoryEntry, Agent } from '../api/memory'
import { loadMemories, getMemoryByName, createMemory, updateMemory, deleteMemory, loadAgentMemory, saveAgentMemory, clearAgentMemory, loadAgents } from '../api/memory'

export const useMemoryStore = defineStore('memory',()=>{
  const panelOpen=ref(false);const mode=ref<'auto'|'agent'>('auto')
  const memories=ref<MemoryEntry[]>([]);const loadingMemories=ref(false)
  const agents=ref<Agent[]>([]);const loadingAgents=ref(false)
  const selectedAgentType=ref<string|null>(null);const agentMemoryScope=ref<AgentMemoryScope>('project');const agentMemoryContent=ref('');const savingAgentMemory=ref(false)
  function togglePanel(){panelOpen.value=!panelOpen.value;if(panelOpen.value){if(mode.value==='auto')fetchMemories();else fetchAgents()}}
  function setPanelOpen(v:boolean){panelOpen.value=v;if(v){if(mode.value==='auto')fetchMemories();else fetchAgents()}}
  function switchMode(m:'auto'|'agent'){mode.value=m;if(m==='auto')fetchMemories();else fetchAgents()}
  function selectAgentForMemory(name:string){selectedAgentType.value=name;fetchAgentMemoryContent()}
  async function fetchMemories(){loadingMemories.value=true;try{const res=await loadMemories();if(res.success)memories.value=res.memories}catch(e){console.warn('Failed to load memories:',e)}finally{loadingMemories.value=false}}
  async function fetchMemoryByName(name:string):Promise<MemoryEntry|null>{return await getMemoryByName(name)}
  async function createNewMemory(name:string,mt:MemoryType,desc:string,content:string,tags?:string[]):Promise<MemoryEntry|null>{const entry=await createMemory(name,mt,desc,content,tags);if(entry)memories.value.push(entry);return entry}
  async function updateMemoryByName(name:string,data:{description?:string;content?:string;tags?:string[]}):Promise<MemoryEntry|null>{const entry=await updateMemory(name,data);if(entry){const idx=memories.value.findIndex(m=>m.name===name);if(idx>=0)memories.value[idx]=entry};return entry}
  async function deleteMemoryByName(name:string):Promise<boolean>{const ok=await deleteMemory(name);if(ok)memories.value=memories.value.filter(m=>m.name!==name);return ok}
  async function fetchAgents(){loadingAgents.value=true;try{agents.value=await loadAgents()}catch(e){console.warn('Failed to load agents:',e)}finally{loadingAgents.value=false}}
  async function fetchAgentMemoryContent(){if(!selectedAgentType.value)return;try{agentMemoryContent.value=await loadAgentMemory(selectedAgentType.value,agentMemoryScope.value)}catch(e){console.warn('Failed to load agent memory:',e);agentMemoryContent.value=''}}
  async function saveAgentMemoryContent(){if(!selectedAgentType.value)return;const a=selectedAgentType.value;savingAgentMemory.value=true;try{const ok=await saveAgentMemory(a,agentMemoryContent.value,agentMemoryScope.value);alert(ok?'Agent memory saved!':'Failed to save agent memory.');return ok}catch(e){console.warn('Failed to save:',e);alert('Failed to save agent memory.');return false}finally{savingAgentMemory.value=false}}
  async function clearAgentMemoryContent(){if(!selectedAgentType.value)return;if(!confirm('Clear this agent memory? This cannot be undone.'))return false;const a=selectedAgentType.value;try{const ok=await clearAgentMemory(a,agentMemoryScope.value);if(ok){agentMemoryContent.value='';alert('Agent memory cleared.')}else{alert('Failed to clear agent memory.')};return ok}catch(e){console.warn('Failed to clear:',e);alert('Failed to clear agent memory.');return false}}
  const totalMemories=computed(()=>memories.value.length)
  const memoryStats=computed(()=>{const u=memories.value.filter(m=>m.memory_type==='user').length;const f=memories.value.filter(m=>m.memory_type==='feedback').length;const p=memories.value.filter(m=>m.memory_type==='project').length;const r=memories.value.filter(m=>m.memory_type==='reference').length;const t=totalMemories.value||1;return{user:u,feedback:f,project:p,reference:r,userPercent:Math.round((u/t)*100),feedbackPercent:Math.round((f/t)*100),projectPercent:Math.round((p/t)*100),referencePercent:Math.round((r/t)*100)}})
  const wordCount=computed(()=>agentMemoryContent.value?agentMemoryContent.value.trim().split(/\s+/).filter(w=>w.length>0).length:0)
  return {panelOpen,mode,memories,loadingMemories,agents,loadingAgents,selectedAgentType,agentMemoryScope,agentMemoryContent,savingAgentMemory,totalMemories,memoryStats,wordCount,togglePanel,setPanelOpen,switchMode,selectAgentForMemory,fetchMemories,fetchMemoryByName,createNewMemory,updateMemoryByName,deleteMemoryByName,fetchAgents,fetchAgentMemoryContent,saveAgentMemoryContent,clearAgentMemoryContent}
})
