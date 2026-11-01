<template>
<div class="drop-veil" id="dropVeil" :class="{ show: fileDrop.over }">
  <div class="drop-inner">
    <div class="drop-ico">
      <svg width="26" height="26" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="17 8 12 3 7 8"/><line x1="12" y1="3" x2="12" y2="15"/></svg>
    </div>
    <div class="drop-title">{{ fileDrop.processing ? 'Reading files…' : 'Drop to attach' }}</div>
    <div class="drop-sub">{{ fileDrop.error || 'images · text · code' }}</div>
  </div>
</div>
</template>

<script setup lang="ts">
import { fileDrop } from '../data/fileDrop'
</script>
