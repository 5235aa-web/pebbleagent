<template>
      <div class="dr-head">
        <div class="dr-tabs">
          <button class="dr-tab on" data-tab="files">Files <span class="cnt">7</span></button>
          <button class="dr-tab" data-tab="usage">Usage</button>
          <button class="dr-tab" data-tab="context">Context</button>
          <button class="dr-tab" data-tab="llm">LLM</button>
          <button class="dr-tab" data-tab="notes">Notes</button>
        </div>
        <button class="icon-btn tip tip-right" data-tip="Open preview stage" id="openStage">
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7"><rect x="2.5" y="4" width="19" height="13" rx="2"/><line x1="8" y1="21" x2="16" y2="21"/><line x1="12" y1="17" x2="12" y2="21"/></svg>
        </button>
        <button class="icon-btn tip tip-right" data-tip="Collapse dock" id="collapseRight">
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><polyline points="9 18 15 12 9 6"/></svg>
        </button>
      </div>
</template>

<script setup lang="ts">
</script>
