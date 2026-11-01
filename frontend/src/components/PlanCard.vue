<template>
      <aside class="plan-card" id="planCard" :aria-hidden="!plan.open">
        <span class="plan-fold"></span>
        <span class="plan-glow"></span>
        <header class="plan-head">
          <div class="plan-ring">
            <svg viewBox="0 0 46 46">
              <circle class="pr-track" cx="23" cy="23" r="19" pathLength="100"></circle>
              <circle class="pr-fill" cx="23" cy="23" r="19" pathLength="100"></circle>
            </svg>
            <span class="pr-num">{{ progress.done }}<small>/{{ progress.total }}</small></span>
          </div>
          <div class="plan-head-text">
            <span class="plan-kicker">live plan</span>
            <span class="plan-title">{{ plan.title }}</span>
            <span class="plan-sub">{{ plan.sub }}</span>
          </div>
          <button class="plan-x" id="planClose" data-tip="Hide plan">
            <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>
          </button>
        </header>
        <div class="plan-body">
          <i class="plan-spine"></i>
          <ol class="plan-steps" id="planSteps">
            <li v-for="(s, i) in plan.steps" :key="s.id" class="plan-step" :class="s.state" :style="{ '--i': i }">
              <span class="ps-node">
                <svg v-if="s.state === 'done'" width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3"><path class="ps-check" d="M20 6 9 17l-5-5"/></svg>
                <i v-else-if="s.state === 'now'" class="ps-pulse"></i>
              </span>
              <div class="ps-body"><span class="ps-task">{{ s.task }}</span><span class="ps-note">{{ s.note }}</span></div>
              <span v-if="s.state === 'now'" class="ps-now">now</span>
            </li>
          </ol>
        </div>
        <footer class="plan-foot">
          <span class="plan-live"><i></i>working</span>
          <span class="plan-spacer"></span>
          <span class="plan-tok">{{ plan.token }}</span>
        </footer>
      </aside>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { plan, planProgress } from '../data/store'

const progress = computed(() => planProgress())
</script>
