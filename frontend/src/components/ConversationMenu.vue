<template>
<div class="conv-menu" id="convMenu" aria-hidden="true">
  <button data-act="pin"><svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9"><path d="M12 17v5"/><path d="M9 10.8V4h6v6.8l2 3.2H7z"/></svg><span>Pin</span></button>
  <button data-act="rename"><svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9"><path d="M12 20h9"/><path d="M16.5 3.5a2.12 2.12 0 0 1 3 3L7 19l-4 1 1-4z"/></svg><span>Rename</span></button>

  <button data-act="export"><svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/></svg><span>Export Md</span></button>
  <div class="cm-sep"></div>
  <button data-act="delete" class="danger"><svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9"><polyline points="3 6 5 6 21 6"/><path d="M19 6l-1 14H6L5 6"/><path d="M10 11v6M14 11v6"/></svg><span>Delete</span></button>
</div>
</template>

<script setup lang="ts">
</script>
