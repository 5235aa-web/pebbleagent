<template>
<div class="overlay" id="sheet-image">
  <div class="sheet viewer">
    <div class="sheet-hdr">
      <span class="sheet-ico">
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><rect x="3" y="3" width="18" height="18" rx="2"/><circle cx="8.5" cy="8.5" r="1.5"/><polyline points="21 15 16 10 5 21"/></svg>
      </span>
      <div class="sheet-title">{{ image.name || 'image viewer' }}<small>{{ image.dataUrl ? image.meta + ' · loaded from project' : 'no image selected' }}</small></div>
      <button class="sheet-x" data-close>
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>
      </button>
    </div>
    <div class="viewer-body">
      <div class="viewer-art">
        <img v-if="image.dataUrl" class="viewer-img" :src="image.dataUrl" :alt="image.name" />
        <svg v-else viewBox="0 0 320 180" fill="none">
          <rect x="1" y="1" width="318" height="178" rx="10" stroke="var(--line-strong)"></rect>
          <rect x="14" y="14" width="80" height="152" rx="6" fill="var(--surface-2)" stroke="var(--line)"></rect>
          <rect x="104" y="14" width="130" height="152" rx="6" fill="var(--surface-2)" stroke="var(--line)"></rect>
          <rect x="244" y="14" width="62" height="152" rx="6" fill="var(--accent-soft)" stroke="var(--accent)"></rect>
          <rect x="22" y="26" width="46" height="4" rx="2" fill="var(--line-strong)"></rect>
          <rect x="22" y="36" width="60" height="4" rx="2" fill="var(--line)"></rect>
          <rect x="22" y="46" width="52" height="4" rx="2" fill="var(--line)"></rect>
          <rect x="112" y="26" width="90" height="6" rx="3" fill="var(--accent)"></rect>
          <rect x="112" y="40" width="112" height="4" rx="2" fill="var(--line)"></rect>
          <rect x="112" y="50" width="96" height="4" rx="2" fill="var(--line)"></rect>
          <rect x="112" y="66" width="112" height="40" rx="4" fill="var(--surface-3)" stroke="var(--line)"></rect>
          <rect x="112" y="114" width="70" height="4" rx="2" fill="var(--line)"></rect>
          <rect x="112" y="124" width="100" height="4" rx="2" fill="var(--line)"></rect>
          <rect x="252" y="26" width="46" height="46" rx="4" fill="var(--surface)" stroke="var(--accent)"></rect>
          <rect x="252" y="82" width="46" height="8" rx="4" fill="var(--surface)" opacity=".7"></rect>
          <rect x="252" y="96" width="36" height="8" rx="4" fill="var(--surface)" opacity=".5"></rect>
        </svg>
      </div>
    </div>
    <div class="viewer-foot">
      <span>{{ image.meta || 'PNG' }}</span><span>·</span><span>{{ image.name || 'dock.png' }}</span>
      <span style="flex:1 1 auto"></span>
      <button class="btn btn-ghost btn-sm" @click="openFile(image.path)" :disabled="!image.path">Open</button>
      <button class="btn btn-ghost btn-sm" data-close>Close</button>
    </div>
  </div>
</div>
</template>

<script setup lang="ts">
import { image } from '../data/panels'
import { openFile } from '../data/wire'
</script>
