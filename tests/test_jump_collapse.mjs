// test_jump_collapse.mjs — jump-to-compacted-message lands on the fold divider (no auto-expand).
// Branch mirror of VirtualMessageList.scrollToMessageIndex + source consistency checks (drift guard).
// Run: node tests/test_jump_collapse.mjs   (exit 0 = pass)
import { readFileSync } from 'node:fs'

let pass = 0, fail = 0
function check(name, cond, detail = '') {
  if (cond) { pass++; console.log(`[PASS] ${name}`) }
  else { fail++; console.log(`[FAIL] ${name} ${detail}`) }
}

// ===== 1. Branch decision mirror =====
// inputs: byMsgHit (target belongs to a collapsible segment), isExpanded (segment already expanded)
// output: 'capsule' (scroll to fold divider, no expand) | 'normal' (scroll to the message)
function decide(byMsgHit, isExpanded) {
  if (byMsgHit && !isExpanded) return 'capsule'
  return 'normal'
}

check('in collapsed segment + not expanded -> capsule', decide(true, false) === 'capsule')
check('in collapsed segment + expanded -> normal', decide(true, true) === 'normal')
check('not in a segment (no byMsg hit) -> normal', decide(false, false) === 'normal')
check('not in a segment (other state) -> normal', decide(false, true) === 'normal')

// ===== 2. VirtualMessageList source consistency =====
const vml = readFileSync(new URL('../ui-gui-vue3/src/components/SegmentedMessageList.vue', import.meta.url), 'utf-8')
const s1 = vml.indexOf('function scrollToMessageIndex')
const e1 = vml.indexOf('defineExpose', s1)
const fnBody = vml.slice(s1, e1)
check('function body exists', s1 >= 0 && e1 > s1)
check('queries capsule via [data-cb-id]', fnBody.includes('[data-cb-id='))
check('no longer writes expand state (expanded.value = next)', !fnBody.includes('expanded.value = next'))
check('no longer uses nextTick (no wait for expand render)', !fnBody.includes('nextTick'))
check('capsule branch: smooth scroll', fnBody.includes("cb?.scrollIntoView({ behavior: 'smooth'"))
check('capsule branch returns immediately (no fallthrough)', /cb\?\.scrollIntoView[^\n]*\n\s*return/.test(fnBody))
check('normal path kept (data-msg-idx query)', fnBody.includes('[data-msg-idx='))
check('function is synchronous now (no async prefix)', !vml.slice(s1 - 30, s1).includes('async '))

// ===== 3. MessageSearch source consistency =====
const ms = readFileSync(new URL('../ui-gui-vue3/src/components/MessageSearch.vue', import.meta.url), 'utf-8')
const s2 = ms.indexOf('function scrollTo(msgIdx: number)')
const e2 = ms.indexOf('function handleKeyDown', s2)
const msBody = ms.slice(s2, e2)
check('MessageSearch function body exists', s2 >= 0 && e2 > s2)
check('delegates to unified entry when available', msBody.includes('p.onScrollToIndex(msgIdx)'))
check('fallback moved into else branch', msBody.includes('} else {'))
check('else branch keeps DOM fallback', msBody.includes('[data-msg-idx='))

// ===== 4. Caller chain structural confirmation =====
const app = readFileSync(new URL('../ui-gui-vue3/src/App.vue', import.meta.url), 'utf-8')
check('App: handleScrollToMessageIndex -> vlistRef', app.includes('vlistRef.value?.scrollToMessageIndex(i)'))
check('App: toMsg event -> vlistRef (Sidebar search chain)', app.includes('gateway:scroll-to-message') && app.includes('vlistRef.value?.scrollToMessageIndex(i)'))

console.log(`\nRESULT: ${pass} passed, ${fail} failed`)
process.exit(fail === 0 ? 0 : 1)
