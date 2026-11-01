<template>
<div class="overlay" id="sheet-tools">
  <div class="sheet">
    <div class="sheet-hdr">
      <span class="sheet-ico">
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><path d="M14.7 6.3a1 1 0 0 0 0 1.4l1.6 1.6a1 1 0 0 0 1.4 0l3.77-3.77a6 6 0 0 1-7.94 7.94l-6.91 6.91a2.12 2.12 0 0 1-3-3l6.91-6.91a6 6 0 0 1 7.94-7.94l-3.76 3.76z"/></svg>
      </span>
      <div class="sheet-title">Tools &amp; skills<small>{{ toolTotal }} built-in · {{ skillTotal }} skills</small></div>
      <button class="sheet-x" data-close>
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>
      </button>
    </div>
    <div class="sheet-body">
      <template v-for="g in catalog.toolGroups" :key="'tool-' + g.name">
        <div class="section-h">{{ g.name }}</div>
        <div style="display:flex;flex-wrap:wrap;gap:4px">
          <span class="skill-chip" v-for="n in g.items" :key="n">{{ n }}</span>
        </div>
      </template>
      <template v-for="g in catalog.skillGroups" :key="'skill-' + g.name">
        <div class="section-h">{{ g.name }}</div>
        <div style="display:flex;flex-wrap:wrap;gap:4px">
          <span class="skill-chip" v-for="n in g.items" :key="n">{{ n }}</span>
        </div>
      </template>
    </div>
    <div class="sheet-foot">
      <span class="sf-hint">read-only catalogue</span>
      <button class="btn btn-ghost" data-close>Close</button>
    </div>
  </div>
</div>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { catalog } from '../data/store'

const toolTotal = computed(() => catalog.toolGroups.reduce((n, g) => n + g.items.length, 0))
const skillTotal = computed(() => catalog.skillGroups.reduce((n, g) => n + g.items.length, 0))
</script>
