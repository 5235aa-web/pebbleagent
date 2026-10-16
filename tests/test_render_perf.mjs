// test_render_perf.mjs — render performance 3-layer fix (source checks + logic mirrors)
// Run: node tests/test_render_perf.mjs   (exit 0 = pass)
import { readFileSync } from 'node:fs'

const gw = readFileSync(new URL('../ui-gui-vue3/src/stores/gateway.ts', import.meta.url), 'utf8')
const mb = readFileSync(new URL('../ui-gui-vue3/src/components/MessageBubble.vue', import.meta.url), 'utf8')
const mc = readFileSync(new URL('../ui-gui-vue3/src/components/MessageContent.vue', import.meta.url), 'utf8')

let pass = 0, fail = 0
function check(name, cond) {
  if (cond) { pass++; console.log(`  [PASS] ${name}`) }
  else { fail++; console.log(`  [FAIL] ${name}`) }
}
function extractFn(src, name) {
  const start = src.indexOf(`function ${name}(`)
  if (start < 0) return null
  let level = 0
  for (let j = src.indexOf('{', start); j < src.length; j++) {
    if (src[j] === '{') level++
    else if (src[j] === '}') { level--; if (level === 0) return src.slice(start, j + 1) }
  }
  return null
}

console.log('1) Fix-1 throttle (gateway.ts)')
check('_lastFlushAt declared', gw.includes('let _lastFlushAt = 0'))
check('flush records time', gw.includes('_lastFlushAt = performance.now()'))
check('120ms throttle calc', gw.includes('Math.max(0, 120 - (performance.now() - _lastFlushAt))'))
check('old rAF impl removed', !gw.includes('flushTimeoutRef = requestAnimationFrame(flushStreamState)'))
check('setTimeout scheduling', gw.includes('window.setTimeout(flushStreamState, wait)'))
{
  let timerAt = -1, lastFlushAt = -1e9, flushCount = 0
  for (let t = 0; t <= 60000; t += 1) {
    if (t % 10 === 0 && timerAt < 0) {
      const wait = Math.max(0, 120 - (t - lastFlushAt))
      timerAt = t + wait
    }
    if (timerAt >= 0 && t >= timerAt) { flushCount++; timerAt = -1; lastFlushAt = t }
  }
  const expected = Math.floor(60000 / 120) + 1
  check(`60s high-freq delta -> flush ~${expected} (actual ${flushCount})`, Math.abs(flushCount - expected) < 10)
}

console.log('2) Fix-2 segment freeze (MessageBubble.vue) — REVERTED 2026-10-15')
check('isFrozen function removed', !mb.includes('function isFrozen'))
check('no v-memo attribute usage', !mb.includes('v-memo="['))
check('v-for reverted (no index var)', mb.includes('v-for="item in message.executionOrder"'))
check('revert note present', mb.includes('段级 v-memo 冻结已回退'))
check('reasoning branch intact', mb.includes("item.type==='reasoning'&&item.content"))
check('question branch intact', mb.includes("item.type==='question'"))

console.log('3) Fix-3 line cache (MessageContent.vue)')
check('lineCache declared', mc.includes('const lineCache=new Map<string,string>()'))
check('rimCached defined', mc.includes('function rimCached(line:string):string'))
check('template uses rimCached', mc.includes('v-html="rimCached(line)"'))
check('old rim call removed', !mc.includes('v-html="rim(line)"'))
{
  const fnSrc = extractFn(mc, 'rimCached')
  check('rimCached extracted', !!fnSrc)
  if (fnSrc) {
    let rimCalls = 0
    const rim = (l) => { rimCalls++; return 'R<' + l + '>' }
    const jsSrc = fnSrc.replace(/function rimCached\(line:string\):string\{/, 'function rimCached(line){')
    const factory = new Function('rim', `const lineCache=new Map()\n${jsSrc}\nreturn { rimCached, lineCache }`)
    const { rimCached, lineCache } = factory(rim)
    const r1 = rimCached('line A')
    const r2 = rimCached('line A')
    check('same line cached (rim called once)', rimCalls === 1 && r1 === r2 && r1 === 'R<line A>')
    rimCached('line B')
    check('different lines computed', rimCalls === 2)
    check('cache size correct', lineCache.size === 2)
    for (let i = 0; i < 7000; i++) rimCached('bulk-' + i)
    check('auto clear on overflow', lineCache.size <= 3001)
  }
}

console.log('='.repeat(56))
console.log(`RESULT: ${pass} passed / ${fail} failed`)
process.exitCode = fail ? 1 : 0
