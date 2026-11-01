<template>
    <main class="stream" :class="{ 'plan-open': plan.open }">
      <StreamHead />

      <MessageStream />

      <!-- jump to latest — outside the scroll container, so it stays put -->
      <JumpToLatest />

      <!-- timeline -->
      <StreamTimeline />

      <!-- ---------- LIVE PLAN — a floating slip, not a sidebar ---------- -->
      <PlanCard />

      <!-- composer -->
      <ComposerWrap />

    </main>
</template>

<script setup lang="ts">
import { plan } from '../data/store'
import StreamHead from './StreamHead.vue'
import MessageStream from './MessageStream.vue'
import JumpToLatest from './JumpToLatest.vue'
import StreamTimeline from './StreamTimeline.vue'
import PlanCard from './PlanCard.vue'
import ComposerWrap from './ComposerWrap.vue'
</script>
