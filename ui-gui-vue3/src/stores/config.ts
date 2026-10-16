import { defineStore } from 'pinia'
import { ref } from 'vue'
import type { LLMProvider, AgentInfo, ReasoningEffort } from '../types/agentChat'
import { getGateway } from '../services/gatewayClient'

const DEFAULT_PROVIDERS: LLMProvider[] = [
  { id:'default',provider:'deepseek',apiKey:'sk-xxxx',baseUrl:'https://api.deepseek.com/v1/',models:['deepseek-flash','deepseek-pro'],temperature:0.9,maxTokens:4096,topP:1.0,reasoningEffort:'max',thinkingEnabled:true },
  { id:'1774763858220kcj1dmnpb',provider:'xiaomimimo',apiKey:'sk-xxxx',baseUrl:'https://api.xiaomimimo.com/v1',models:['mimo-v2.6-flash','mimo-v2.6-pro'],temperature:0.3,maxTokens:4096,topP:1,thinkingEnabled:true },
]
const DEFAULT_AGENTS: AgentInfo[] = [
  { name:'code_agent',displayName:'Code Agent',description:'Code expert for programming & debugging',modelSource:'deepseek-flash' },
  { name:'chat_agent',displayName:'Chat Agent',description:'Everyday chat assistant for casual talk & Q&A',modelSource:'deepseek-flash' },
]
const LS_PROJECT_PATH='pebble:projectPath';const DEFAULT_PROJECT_PATH='E:\\workspace'
function loadProjectPath():string{try{return localStorage.getItem(LS_PROJECT_PATH)||DEFAULT_PROJECT_PATH}catch{return DEFAULT_PROJECT_PATH}}

export const useConfigStore = defineStore('config',()=>{
  const panelOpen=ref(false);const agents=ref<AgentInfo[]>(DEFAULT_AGENTS);const activeAgent=ref('code_agent')
  const providers=ref<LLMProvider[]>(DEFAULT_PROVIDERS);const activeProviderId=ref(DEFAULT_PROVIDERS[0].id)
  function createDefaultAM():Record<string,string>{const m:Record<string,string>={};for(const p of DEFAULT_PROVIDERS){if(p.models.length>0)m[p.id]=p.models[0]}return m}
  const activeModel=ref<Record<string,string>>(createDefaultAM());const projectPath=ref(loadProjectPath())
  function togglePanel(){panelOpen.value=!panelOpen.value}
  function setPanelOpen(o:boolean){panelOpen.value=o}
  function getActiveAgent(){return agents.value.find(a=>a.name===activeAgent.value)}
  function setActiveAgent(n:string){activeAgent.value=n;getGateway().request('agent.switch',{name:n}).catch(err=>console.warn('Agent switch failed:',err))}
  function getActiveProvider(){return providers.value.find(p=>p.id===activeProviderId.value)}
  function setActiveProviderId(id:string){activeProviderId.value=id}
  function updateProvider(id:string,d:Partial<LLMProvider>){const i=providers.value.findIndex(p=>p.id===id);if(i>=0)providers.value[i]={...providers.value[i],...d}}
  function addProvider(){const id='provider_'+Date.now();const p:LLMProvider={id,provider:'new-provider',apiKey:'',baseUrl:'https://api.example.com/v1',models:['model-1'],temperature:0.5,maxTokens:4096,topP:1};providers.value.push(p);activeProviderId.value=id;activeModel.value={...activeModel.value,[id]:'model-1'}}
  function deleteProvider(id:string){const idx=providers.value.findIndex(p=>p.id===id);if(idx<0)return;providers.value=providers.value.filter(p=>p.id!==id);if(activeProviderId.value===id&&providers.value.length>0)activeProviderId.value=providers.value[0].id;delete activeModel.value[id];getGateway().request('model.provider.delete',{index:idx}).catch(err=>console.warn('Delete provider failed:',err))}
  async function saveProviders(){const p=getActiveProvider();if(!p)return;try{const gw=getGateway();await gw.request('model.provider.save',{index:providers.value.findIndex(pp=>pp.id===activeProviderId.value),provider:{name:p.provider,slug:p.id,apiKey:p.apiKey,baseUrl:p.baseUrl,models:p.models,temperature:p.temperature,topP:p.topP,maxTokens:p.maxTokens,reasoning_effort:p.reasoningEffort||'max',thinking_enabled:p.thinkingEnabled!==false}});const am=activeModel.value[p.id];if(am)await gw.request('model.provider.set',{index:providers.value.findIndex(pp=>pp.id===activeProviderId.value),model:am});window.dispatchEvent(new CustomEvent('providers-saved',{detail:{providers:providers.value}}))}catch(err){console.error('Save providers failed:',err)}}
  function setActiveModel(pid:string,mn:string){activeModel.value={...activeModel.value,[pid]:mn};const i=providers.value.findIndex(p=>p.id===pid);if(i>=0)getGateway().request('model.provider.set',{index:i,model:mn}).catch(err=>console.warn('Auto-sync model failed:',err))}
  function addModel(pid:string,ai:number){const i=providers.value.findIndex(p=>p.id===pid);if(i>=0){const ms=[...providers.value[i].models];ms.splice(ai,0,'');providers.value[i]={...providers.value[i],models:ms}}}
  function updateModelName(pid:string,on:string,nn:string){const i=providers.value.findIndex(p=>p.id===pid);if(i>=0){providers.value[i]={...providers.value[i],models:providers.value[i].models.map(m=>m===on?nn:m)};if(activeModel.value[pid]===on)activeModel.value={...activeModel.value,[pid]:nn}}}
  function removeModel(pid:string,mn:string){const i=providers.value.findIndex(p=>p.id===pid);if(i>=0){let ms=providers.value[i].models.filter(m=>m!==mn);if(ms.length===0)ms=[''];providers.value[i]={...providers.value[i],models:ms};if(activeModel.value[pid]===mn){const r=providers.value[i].models.filter(m=>m!==mn)||[''];activeModel.value={...activeModel.value,[pid]:r[0]||''}}}}
  function setProjectPath(p:string){projectPath.value=p;try{localStorage.setItem(LS_PROJECT_PATH,p)}catch{}}
  // ===== 多项目 =====
  const projects=ref<string[]>([]);const activeProjectPath=ref(projectPath.value);const unassignedCount=ref(0)
  async function loadProjects(){try{const gw=getGateway();const r=await gw.request<{projects:string[];active:string}>('project.list',{});if(Array.isArray(r.projects)){projects.value=r.projects;if(r.active&&r.projects.includes(r.active)){activeProjectPath.value=r.active;projectPath.value=r.active;try{localStorage.setItem(LS_PROJECT_PATH,r.active)}catch{}}}}catch{}}
  async function addProject(path:string){const p=(path||'').trim();if(!p)return;try{const gw=getGateway();const r=await gw.request<{projects:string[];active:string}>('project.add',{path:p});if(Array.isArray(r.projects)){projects.value=r.projects;if(r.active&&r.projects.includes(r.active)){activeProjectPath.value=r.active;projectPath.value=r.active;try{localStorage.setItem(LS_PROJECT_PATH,r.active)}catch{}}}}catch{}}
  async function deleteProject(path:string){try{const gw=getGateway();const r=await gw.request<{projects:string[];active:string}>('project.delete',{path});if(Array.isArray(r.projects)){projects.value=r.projects;if(r.active&&r.projects.includes(r.active)){activeProjectPath.value=r.active;projectPath.value=r.active;try{localStorage.setItem(LS_PROJECT_PATH,r.active)}catch{}}}}catch{}}
  async function setActiveProject(path:string){if(!projects.value.includes(path))return;try{const gw=getGateway();await gw.request('project.set_active',{path});activeProjectPath.value=path;projectPath.value=path;try{localStorage.setItem(LS_PROJECT_PATH,path)}catch{};window.dispatchEvent(new CustomEvent('project-switched',{detail:{path}}))}catch{}}
  async function refreshUnassigned(){try{const gw=getGateway();const r=await gw.request<{count:number}>('session.unassigned_count',{});unassignedCount.value=r?.count??0}catch{}}
  async function adoptUnassigned(){try{const gw=getGateway();await gw.request('session.adopt_unassigned',{project_path:activeProjectPath.value});unassignedCount.value=0;window.dispatchEvent(new CustomEvent('project-adopted',{detail:{path:activeProjectPath.value}}))}catch{}}
  async function loadProvidersFromBackend(){try{const gw=getGateway();const result=await gw.request<Record<string,unknown>>('model.options',{});if(!(result?.providers as Array<unknown>)?.length)return false;const raw=result.providers as Array<{slug:string;name:string;apiKey:string;baseUrl:string;models:string[];temperature?:number;topP?:number;max_tokens?:number;reasoning_effort?:string;thinking_enabled?:boolean}>;const loaded:LLMProvider[]=raw.map(p=>({id:p.slug,provider:p.name,apiKey:p.apiKey||'',baseUrl:p.baseUrl||'',models:p.models||[],temperature:p.temperature??0.5,maxTokens:p.max_tokens??4096,topP:p.topP??1,reasoningEffort:(p.reasoning_effort as ReasoningEffort)||undefined,thinkingEnabled:p.thinking_enabled!==false}));providers.value=loaded;const ai=(result.activeProviderIndex as number)??0;if(ai>=0&&ai<loaded.length)activeProviderId.value=loaded[ai].id;const cm=result.model as string;if(cm){const aid=loaded[ai>=0&&ai<loaded.length?ai:0]?.id;if(aid)activeModel.value={...activeModel.value,[aid]:cm}};return true}catch{return false}}
  return {panelOpen,setPanelOpen,togglePanel,agents,activeAgent,setActiveAgent,getActiveAgent,providers,activeProviderId,setActiveProviderId,getActiveProvider,updateProvider,addProvider,deleteProvider,saveProviders,activeModel,setActiveModel,addModel,updateModelName,removeModel,projectPath,setProjectPath,loadProvidersFromBackend,projects,activeProjectPath,unassignedCount,loadProjects,addProject,deleteProject,setActiveProject,refreshUnassigned,adoptUnassigned}
})
