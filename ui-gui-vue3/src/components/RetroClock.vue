<script setup lang="ts">
import{ref,onMounted,onUnmounted}from'vue'
const h=ref(0);const m=ref(0);const s=ref(0)
function u(){const d=new Date();h.value=d.getHours();m.value=d.getMinutes();s.value=d.getSeconds()}
u();let i:number;onMounted(()=>{i=window.setInterval(u,1000)});onUnmounted(()=>clearInterval(i))
const pad=(n:number)=>String(n).padStart(2,'0')
</script>
<template><span class="rclock"><span class="rclock-digit">{{pad(h)}}</span><span class="rclock-colon">:</span><span class="rclock-digit">{{pad(m)}}</span><span class="rclock-colon">:</span><span class="rclock-digit">{{pad(s)}}</span></span></template>
