<script setup lang="ts">import{ref,computed,onMounted,onUnmounted}from'vue';import type{Message}from'../types/agentChat'
const p=defineProps<{messages:Message[];containerRef:any;onScrollToIndex?:(idx:number)=>void}>()
const v=ref(false);const tip=ref({show:false,x:0,y:0,content:'',time:''});const tr=ref<HTMLDivElement|null>(null)
let ht:ReturnType<typeof setTimeout>|null=null
const um=computed(()=>p.messages.filter(m=>m.role==='user'))
const ft=(ts?:number):string=>{if(!ts)return'';try{const d=new Date(ts);const x=(n:number)=>String(n).padStart(2,'0');return`${d.getFullYear()}-${x(d.getMonth()+1)}-${x(d.getDate())} ${x(d.getHours())}:${x(d.getMinutes())}:${x(d.getSeconds())}`}catch{return''}}
const hde=(e:MouseEvent,msg:Message)=>{const el=e.currentTarget as HTMLElement;const r=el.getBoundingClientRect();const t=(msg.content||'(empty message)').trim();const pr=t.length>120?t.slice(0,120)+'...':t;tip.value={show:true,x:r.left+r.width/2,y:r.bottom+3,content:pr,time:ft(msg.createdAt)}}
const hdl=()=>tip.value={...tip.value,show:false}
const gi=(msg:Message):number=>p.messages.findIndex(m=>m.id===msg.id&&m.role==='user')
const sm=(msgIdx:number)=>{if(p.onScrollToIndex)p.onScrollToIndex(msgIdx);else{const el=p.containerRef?.current?.querySelector(`[data-msg-idx="${msgIdx}"]`);if(el)el.scrollIntoView({behavior:'smooth',block:'center'})}}
function onMove(e:MouseEvent){const msgs=document.querySelector('.msgs')as HTMLElement|null;if(!msgs)return;const rect=msgs.getBoundingClientRect();v.value=e.clientY>=rect.top&&e.clientY<rect.top+50}
function onWheel(e:WheelEvent){if(!v.value||!tr.value)return;e.preventDefault();tr.value.scrollLeft+=e.deltaY}
onMounted(()=>{const main=document.querySelector('.main')as HTMLElement|null;if(main){main.addEventListener('mousemove',onMove);main.addEventListener('wheel',onWheel,{passive:false})}})
onUnmounted(()=>{const main=document.querySelector('.main')as HTMLElement|null;if(main){main.removeEventListener('mousemove',onMove);main.removeEventListener('wheel',onWheel)}})
</script>
<template>
  <div v-if="um.length>0" class="tb" :class="{visible:v}"><div ref="tr" class="tb-track">
    <span v-for="(msg,i) in um" :key="msg.id||i" class="tb-dot-wrap" @mouseenter="(e:MouseEvent)=>hde(e,msg)" @mouseleave="hdl" @click="sm(gi(msg))"><span class="tb-dot"/></span>
  </div></div>
  <div v-if="tip.show" class="tb-tip" :style="{position:'fixed',left:tip.x+'px',top:tip.y+'px',transform:'translateX(-50%)',zIndex:10000}"><p class="tb-tip-text">{{tip.content}}</p><span v-if="tip.time" class="tb-tip-time">{{tip.time}}</span></div>
</template>
