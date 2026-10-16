<script setup lang="ts">
const p=defineProps<{open:boolean;title:string;message:string;confirmLabel?:string;cancelLabel?:string;danger?:boolean}>()
const e=defineEmits(['confirm','cancel'])
</script>
<template><Teleport to="body"><div v-if="p.open" class="cfm-overlay" @click.self="e('cancel')"><div class="cfm-dialog" @click.stop><div class="cfm-hdr" :class="p.danger?'cfm-hdr-danger':'cfm-hdr-primary'"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" style="flex-shrink:0"><path d="M12 9v4m0 4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z"/></svg><span class="cfm-title">{{p.title}}</span></div><div class="cfm-body">{{p.message}}</div><div class="cfm-actions"><button v-if="p.cancelLabel" class="cfm-btn cfm-btn-cancel" @click="e('cancel')">{{p.cancelLabel}}</button><button class="cfm-btn" :class="p.danger?'cfm-btn-danger':'cfm-btn-primary'" @click="e('confirm')">{{p.confirmLabel||'OK'}}</button></div></div></div></Teleport></template>
