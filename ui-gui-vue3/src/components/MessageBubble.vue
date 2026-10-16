<script setup lang="ts">
import { computed, ref } from 'vue'; import type { Message } from '../types/agentChat'
import CollapsibleCard from './CollapsibleCard.vue'; import MessageContent from './MessageContent.vue'
import AskQuestionCard from './AskQuestionCard.vue'; import CompactSummaryCard from './CompactSummaryCard.vue'
import ToolExecutionCard from './ToolExecutionCard.vue'; import Tooltip from './Tooltip.vue'
import ImageViewer from './ImageViewer.vue'
import { useGatewayStore } from '../stores/gateway'
const p=defineProps<{message:Message;'data-msg-idx'?:number}>()
const g=useGatewayStore()
const iu=computed(()=>p.message.role==='user')
// 消息执行流内是否已内联提问元素（新路径）；有则不重复渲染消息级 questionCard（旧数据兼容）
const hasQInline=computed(()=>!!(p.message.executionOrder?.some(it=>it.type==='question')))
const ts=computed(()=>{if(!p.message.createdAt)return'';const d=new Date(p.message.createdAt);const x=(n:number)=>String(n).padStart(2,'0');return`${d.getFullYear()}-${x(d.getMonth()+1)}-${x(d.getDate())} ${x(d.getHours())}:${x(d.getMinutes())}:${x(d.getSeconds())}`})
function fm(ms:number):string{return ms<1000?`${ms}ms`:`${(ms/1000).toFixed(1)}s`}
// 段级 v-memo 冻结已回退（2026-10-15）：Vue 在 v-for 内 v-memo 共享缓存槽位，同分支段（memo 值相同）会互相串内容（详见 VERSION）
const copied=ref(false);const deleting=ref(false)
const canCopy=computed(()=>(p.message.content||'').length>0)
const canDelete=computed(()=>true)
async function handleCopy(){const t=p.message.content||'';if(!t)return;try{await navigator.clipboard.writeText(t);copied.value=true;setTimeout(()=>copied.value=false,1500)}catch{const ta=document.createElement('textarea');ta.value=t;document.body.appendChild(ta);ta.select();document.execCommand('copy');document.body.removeChild(ta);copied.value=true;setTimeout(()=>copied.value=false,1500)}}
async function handleDelete(){if(deleting.value||!p.message.id)return;deleting.value=true;try{if(p.message.messageId&&g.sessionId){await g.request('message.delete',{session_id:g.sessionId,message_id:p.message.messageId})};window.dispatchEvent(new CustomEvent('gateway:delete-message',{detail:{messageId:p.message.messageId||0,localId:p.message.id}}))}catch(err){console.warn('[MessageBubble] Delete failed:',err)}finally{deleting.value=false}}
// image viewer state
const ivSrc=ref('');const ivAlt=ref('')
function openImage(src: string, alt: string) { ivSrc.value = src; ivAlt.value = alt }
function closeImage() { ivSrc.value = '' }
</script>
<template>
  <div v-if="!deleting" class="msg" :class="iu?'user':'agent'" :data-msg-idx="p['data-msg-idx']">
    <div class="av" :class="iu?'user':'agent'">
      <svg v-if="!iu" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><rect x="3" y="3" width="18" height="14" rx="2"/><line x1="8" y1="21" x2="16" y2="21"/><line x1="12" y1="17" x2="12" y2="21"/></svg>
      <svg v-else width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><path d="M20 21v-2a4 4 0 00-4-4H8a4 4 0 00-4 4v2"/><circle cx="12" cy="7" r="4"/></svg>
    </div>
    <div class="bub" :class="{user:iu}">
      <div class="who">{{iu?'you':'agent'}}<span v-if="ts" class="who-time">{{ts}}</span></div>
      <div>
        <template v-if="iu">
          <div v-if="message.attachments?.length" class="msg-files">
            <div v-for="(a,i) in message.attachments" :key="i" class="msg-file" :class="{ 'msg-img': a.isImage && a.dataUrl }">
              <template v-if="a.isImage && a.dataUrl">
                <img :src="a.dataUrl" :alt="a.name" class="msg-img-thumb" @click="openImage(a.dataUrl!, a.name)" />
              </template>
              <template v-else>
                <span class="msg-file-icon">{{ a.icon }}</span>
              </template>
              <span class="msg-file-name">{{ a.name }}</span>
              <span class="msg-file-size">{{ a.size < 1024 ? a.size+'B' : (a.size/1024).toFixed(1)+'KB' }}</span>
            </div>
          </div>
          <MessageContent v-if="message.content" :content="message.content"/>
        </template>
        <template v-else-if="message.executionOrder&&message.executionOrder.length>0">
          <template v-for="item in message.executionOrder" :key="item.id">
            <CollapsibleCard v-if="item.type==='reasoning'&&item.content" label="reasoning" :time="item.durationMs?fm(item.durationMs):undefined">{{item.content}}</CollapsibleCard>
            <MessageContent v-if="item.type==='text'&&item.content" :content="item.content"/>
            <ToolExecutionCard v-if="item.type==='tool'&&item.toolInfo" :tool-name="item.toolInfo.label" :status="item.toolInfo.status" :files="item.toolInfo.files" :input="item.toolInfo.input" :output="item.toolInfo.output" :error="item.toolInfo.error" :duration="item.toolInfo.duration"/>
            <AskQuestionCard v-if="item.type==='question'" :key="item.id" :id="item.questionId||item.id" :questions="item.questions||[]" :state="item.state"/>
          </template>
          <div v-if="message.interrupted" class="interrupted-badge"><svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><circle cx="12" cy="12" r="10"/><line x1="12" y1="8" x2="12" y2="12"/><line x1="12" y1="16" x2="12.01" y2="16"/></svg> Interrupted</div>
          <AskQuestionCard v-if="message.questionCard&&!hasQInline" :key="message.questionCard.id" :id="message.questionCard.id" :questions="message.questionCard.questions" :state="message.questionCard.state"/>
          <CompactSummaryCard v-if="message.summaryCard" :summary="message.summaryCard"/>
        </template>
        <template v-else>
          <CollapsibleCard v-if="message.reasoning" label="reasoning">{{message.reasoning}}</CollapsibleCard>
          <MessageContent v-if="message.content" :content="message.content"/>
          <AskQuestionCard v-if="message.questionCard" :key="message.questionCard.id" :id="message.questionCard.id" :questions="message.questionCard.questions" :state="message.questionCard.state"/>
          <CompactSummaryCard v-if="message.summaryCard" :summary="message.summaryCard"/>
          <div v-if="message.interrupted" class="interrupted-badge"><svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><circle cx="12" cy="12" r="10"/><line x1="12" y1="8" x2="12" y2="12"/><line x1="12" y1="16" x2="12.01" y2="16"/></svg> Interrupted</div>
        </template>
      </div>
      <div v-if="canCopy||canDelete" class="msg-actions">
        <Tooltip v-if="canCopy" :text="copied?'Copied':'Copy'" position="bottom">
          <button class="msg-act-btn" @click="handleCopy">
            <svg v-if="copied" width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><polyline points="20 6 9 17 4 12"/></svg>
            <svg v-else width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><rect x="9" y="9" width="13" height="13" rx="2"/><path d="M5 15H4a2 2 0 01-2-2V4a2 2 0 012-2h9a2 2 0 012 2v1"/></svg>
          </button>
        </Tooltip>
        <Tooltip v-if="canDelete" text="Delete" position="bottom">
          <button class="msg-act-btn" @click="handleDelete">
            <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><polyline points="3 6 5 6 21 6"/><path d="M19 6v14a2 2 0 01-2 2H7a2 2 0 01-2-2V6m3 0V4a2 2 0 012-2h4a2 2 0 012 2v2"/></svg>
          </button>
        </Tooltip>
      </div>
    </div>
  </div>
  <ImageViewer :src="ivSrc" :alt="ivAlt" @close="closeImage" />
</template>
