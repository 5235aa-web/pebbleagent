import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import type { TodoItem, TodoStatus } from '../types/agentChat'
import { getGateway } from '../services/gatewayClient'
import { useConfigStore } from './config'

export interface Conversation { id:string;title:string;meta:string;pinned:boolean;messageCount?:number;startedAt?:number;lastActivityAt?:number }
interface BackendSession { id:string;title:string;preview?:string;message_count?:number;started_at?:number;last_message_at?:number|null }
const LS_PINNED_KEY='pebble:pinned'
function loadPinned():string[]{try{const r=localStorage.getItem(LS_PINNED_KEY);return r?JSON.parse(r):[]}catch{return[]}}
function savePinned(ids:string[]){try{localStorage.setItem(LS_PINNED_KEY,JSON.stringify(ids))}catch{}}
function lsKey(s:string):string{return`pebble:${s}`}
function fmtTime(ts:number):string{const n=Date.now()/1000;const d=n-ts;if(d<60)return'just now';if(d<3600)return`${Math.floor(d/60)}m ago`;if(d<86400)return`${Math.floor(d/3600)}h ago`;if(d<604800)return`${Math.floor(d/86400)}d ago`;return new Date(ts*1000).toLocaleDateString()}
// 会话列表 meta 统一构建：时间为会话内最新一条消息时间（last_message_at，空会话回退创建时间）
function buildMeta(ts:number|undefined|null,mc:number):string{const t=ts?fmtTime(ts):'';return t?`${t} · ${mc} messages`:`${mc} messages`}

export const useChatStore = defineStore('chat',()=>{
  const todos = ref<TodoItem[]>([])
  const conversations = ref<Conversation[]>([])
  const activeConversationId = ref<string|null>(null)
  const loadingConversations = ref(false)
  let initialLoadDone = false

  function setTodos(newTodos:TodoItem[]){todos.value=newTodos}
  function clearTodos(){todos.value=[]}
  const pendingCount=computed(()=>todos.value.filter(t=>t.status!=='completed').length)
  const completedCount=computed(()=>todos.value.filter(t=>t.status==='completed').length)

  function toConv(s:BackendSession):Conversation{const p=loadPinned();const mc=s.message_count||0;const la=s.last_message_at||s.started_at;return{id:s.id,title:s.title||'New conversation',meta:buildMeta(la,mc),pinned:p.includes(s.id),messageCount:mc,startedAt:s.started_at,lastActivityAt:la}}

  async function loadConversations(projectPath?:string){loadingConversations.value=true;try{const gw=getGateway();const params:Record<string,unknown>={limit:200};if(projectPath)params.project_path=projectPath;const r=await gw.request<{sessions:BackendSession[]}>('session.list',params);conversations.value=(r.sessions||[]).map(toConv)}catch(err){console.warn('[ChatStore] Failed:',err)}finally{loadingConversations.value=false}}
  async function initConversations(){if(initialLoadDone)return;initialLoadDone=true;await loadConversations()}

  function selectConversation(id:string){if(!id)return;activeConversationId.value=id;window.dispatchEvent(new CustomEvent('gateway:resume-session',{detail:{sessionId:id}}))}
  function togglePin(id:string){conversations.value=conversations.value.map(c=>c.id===id?{...c,pinned:!c.pinned}:c);savePinned(conversations.value.filter(c=>c.pinned).map(c=>c.id))}
  function renameConv(id:string,t:string){conversations.value=conversations.value.map(c=>c.id===id?{...c,title:t}:c);getGateway().request('session.title',{session_id:id,title:t}).catch(()=>{})}
  function deleteConv(id:string){const idx=conversations.value.findIndex(c=>c.id===id);const next=idx>=0?conversations.value[idx+1]||conversations.value[idx-1]:null;conversations.value=conversations.value.filter(c=>c.id!==id);if(activeConversationId.value===id){if(next)selectConversation(next.id);else activeConversationId.value=null};savePinned(loadPinned().filter(pid=>pid!==id));localStorage.removeItem(lsKey(`messages:${id}`));getGateway().request('session.delete',{session_id:id}).catch(()=>{})}
  function addConversation(){window.dispatchEvent(new CustomEvent('gateway:create-session'))}

  // 会话有新消息活动时本地刷新列表条目：日期=刚刚（lastActivityAt=now），条数与后端 increment_messages 语义一致（仅用户消息 +1）
  function touchConversation(id:string,bumpCount=false){if(!id)return;const now=Date.now()/1000;conversations.value=conversations.value.map(c=>{if(c.id!==id)return c;const mc=(c.messageCount||0)+(bumpCount?1:0);return{...c,messageCount:mc,lastActivityAt:now,meta:buildMeta(now,mc)}})}

  function handleSessionLoaded(d:{sessionId:string;title?:string;messageCount?:number}){if(!d?.sessionId)return;activeConversationId.value=d.sessionId;conversations.value=conversations.value.map(c=>{if(c.id!==d.sessionId)return c;const mc=d.messageCount??c.messageCount??0;return{...c,messageCount:mc,title:d.title||c.title,meta:buildMeta(c.lastActivityAt??c.startedAt,mc)}})}
  function handleSessionCreated(d:{sessionId:string;title?:string}){if(!d?.sessionId)return;const p=loadPinned();const now=Date.now()/1000;const c:Conversation={id:d.sessionId,title:d.title||'New conversation',meta:buildMeta(now,0),pinned:p.includes(d.sessionId),messageCount:0,startedAt:now,lastActivityAt:now};conversations.value=[c,...conversations.value];activeConversationId.value=d.sessionId}
  function handleSessionUpdated(d:{sessionId:string;title:string}){if(!d?.sessionId)return;conversations.value=conversations.value.map(c=>c.id===d.sessionId?{...c,title:d.title||c.title}:c)}

  if (typeof window !== 'undefined') {
    window.addEventListener('gateway-todos', ((e:CustomEvent) => {
      const d = e.detail as Record<string,unknown>
      if (d && Array.isArray(d.todos)) {
        setTodos((d.todos as Array<{id:string;content:string;status:string;priority?:string}>).map(t => ({
          id:t.id, content:t.content,
          status:(t.status as TodoStatus)||'pending',
          priority:(t.priority as 'high'|'medium'|'low')||'medium',
        })))
      }
    }) as EventListener)
    window.addEventListener('gateway:session-created', ((e:CustomEvent) => { handleSessionCreated(e.detail) }) as EventListener)
    window.addEventListener('gateway:session-loaded', ((e:CustomEvent) => { handleSessionLoaded(e.detail) }) as EventListener)
    window.addEventListener('gateway:session-updated', ((e:CustomEvent) => { handleSessionUpdated(e.detail) }) as EventListener)
    // 新消息活动（用户消息已保存 / 回复完成）→ 刷新对应会话条目的日期与条数
    window.addEventListener('gateway:user-message-saved', ((e:CustomEvent) => { const d = e.detail as {sessionId?:string} | undefined; if (d?.sessionId) touchConversation(d.sessionId, true) }) as EventListener)
    window.addEventListener('gateway:message-complete', ((e:CustomEvent) => { const d = e.detail as {sessionId?:string} | undefined; if (d?.sessionId) touchConversation(d.sessionId, false) }) as EventListener)
    // 切换项目：重新加载会话列表（按项目过滤）
    window.addEventListener('project-switched', (() => { loadConversations(useConfigStore().activeProjectPath) }) as EventListener)
    window.addEventListener('project-adopted', (() => { loadConversations(useConfigStore().activeProjectPath) }) as EventListener)
  }

  return {todos,setTodos,clearTodos,pendingCount,completedCount,conversations,activeConversationId,selectConversation,togglePin,renameConv,deleteConv,addConversation,initConversations,loadConversations,loadingConversations,handleSessionLoaded,handleSessionCreated,handleSessionUpdated,touchConversation}
})
