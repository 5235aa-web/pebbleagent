<script setup lang="ts">import{ref,watch,nextTick}from'vue'
const p=defineProps<{label:string;time?:string;defaultOpen?:boolean;icon?:boolean}>()
const o=ref(p.defaultOpen!==false);const br=ref<HTMLDivElement|null>(null)
watch(()=>o.value,async(v)=>{await nextTick();const e=br.value;if(!e)return;if(v){e.style.maxHeight='none';await nextTick();const h=e.scrollHeight;e.style.maxHeight='0';e.offsetHeight;e.style.maxHeight=h+'px'}else{const h=e.scrollHeight;e.style.maxHeight=h+'px';e.offsetHeight;e.style.maxHeight='0'}})
watch(()=>[p.defaultOpen],()=>{const e=br.value;if(!e)return;e.style.maxHeight=p.defaultOpen===false?'0':'none'},{immediate:true})
</script>
<template>
  <div class="dcard"><div class="dcard-inner">
    <div class="dcard-hdr" :class="{open:o}" @click="o=!o">
      <span class="arr"><svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="9 18 15 12 9 6"/></svg></span>
      <span v-if="icon" class="dcard-icon"><slot name="icon"/></span>
      <span class="lbl">{{label}}</span>
      <span v-if="time" class="tm">{{time}}</span>
    </div>
    <div ref="br" class="dcard-body"><div class="dcard-body-inner"><slot/></div></div>
  </div></div>
</template>
