<template>
  <!-- Without decorations this bar is the window's grip. The attribute does not
       inherit, so every inert element on the strip carries its own copy; the
       buttons inside keep their own clicks. -->
  <header class="orbital" data-tauri-drag-region>
    <div class="brand" data-tauri-drag-region>
      <div class="brand-mark" data-tauri-drag-region>PEBBLE<em>—</em>AGENT</div>
      <span class="brand-ver" data-tauri-drag-region>v1.0</span>
    </div>
    <div class="bar-div" data-tauri-drag-region></div>

    <ProjectSwitcher />

    <!-- ---------- SESSION SWITCHER ---------- -->
    <SessionSwitcher />

    <IslandWrap />

    <BarActions />
  </header>
</template>

<script setup lang="ts">
import ProjectSwitcher from './ProjectSwitcher.vue'
import SessionSwitcher from './SessionSwitcher.vue'
import IslandWrap from './IslandWrap.vue'
import BarActions from './BarActions.vue'
</script>
