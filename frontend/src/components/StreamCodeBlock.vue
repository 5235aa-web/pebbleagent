<template>
  <div class="code">
    <div class="code-hdr">
      <span class="code-lang">{{ lang || 'code' }}</span>
      <span class="code-lines">{{ lineCount }} lines</span>
      <span class="code-hdr-spacer"></span>
      <button class="code-copy" data-copy>
        <svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><rect x="9" y="9" width="12" height="12" rx="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/></svg>
        copy
      </button>
    </div>
    <div class="code-body">
      <div class="gutter">{{ gutter }}</div>
      <pre class="code-pre"><span class="ln" v-for="(l, i) in rendered" :key="i" v-html="l"></span></pre>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { highlight } from '../data/render'

const props = defineProps<{ lang: string; code: string }>()

const lines = computed(() => props.code.replace(/\n$/, '').split('\n'))
const lineCount = computed(() => lines.value.length)
const gutter = computed(() => lines.value.map((_, i) => i + 1).join('\n'))
const rendered = computed(() => lines.value.map(line => (line ? highlight(line, props.lang) : '')))
</script>
