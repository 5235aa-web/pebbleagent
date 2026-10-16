<script setup lang="ts">import{ref,computed}from'vue';import{useChatStore}from'../stores/chat'
const cs=useChatStore();const cl=ref(false)
</script>
<template>
  <div v-if="cs.todos.length>0&&cs.completedCount!==cs.todos.length" class="tw">
    <div class="tw-hdr" @click="cl=!cl">
      <div class="tw-stats"><span class="tw-done">{{cs.completedCount}}</span><span class="tw-div">/</span><span class="tw-total">{{cs.todos.length}}</span><span class="tw-label">tasks</span></div>
      <div v-if="cl&&cs.todos.find(t=>t.status==='in_progress'||t.status==='pending')" class="tw-preview"><span class="tw-preview-text">{{cs.todos.find(t=>t.status==='in_progress'||t.status==='pending')?.content}}</span></div>
      <button class="tw-toggle" @click.stop="cl=!cl" :title="cl?'expand':'collapse'"><span class="tw-arrow" :class="{open:!cl}"><svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="9 18 15 12 9 6"/></svg></span></button>
    </div>
    <div v-if="!cl" class="tw-list">
      <div v-for="todo in cs.todos" :key="todo.id" class="tw-item" :class="'status-'+todo.status">
        <div class="tw-status-icon">
          <svg v-if="todo.status==='completed'" class="tw-icon completed" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><circle cx="12" cy="12" r="10"/><polyline points="9 12 12 15 16 10"/></svg>
          <span v-else-if="todo.status==='in_progress'" class="pulse-dot"/>
          <svg v-else class="tw-icon pending" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"/></svg>
        </div>
        <span class="tw-content" :class="{done:todo.status==='completed'}">{{todo.content}}</span>
        <span v-if="todo.priority" class="tw-priority" :class="todo.priority">{{todo.priority==='high'?'high':todo.priority==='medium'?'med':'low'}}</span>
      </div>
    </div>
  </div>
</template>
