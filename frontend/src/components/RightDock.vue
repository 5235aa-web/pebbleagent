<template>
    <aside class="dock-right">
      <DockHead />

      <div class="dr-content">

        <!-- FILES -->
      <FilesPane />


        <!-- USAGE -->
      <UsagePane />

        <!-- CONTEXT -->
      <ContextPane />

        <!-- LLM -->


      <LlmPane />

        <!-- NOTES -->
      <NotesPane />

        <!-- GITHUB -->
      <GithubPane />

      </div>
    </aside>
</template>

<script setup lang="ts">
import DockHead from './DockHead.vue'
import FilesPane from './FilesPane.vue'
import UsagePane from './UsagePane.vue'
import ContextPane from './ContextPane.vue'
import LlmPane from './LlmPane.vue'
import NotesPane from './NotesPane.vue'
import GithubPane from './GithubPane.vue'
</script>
