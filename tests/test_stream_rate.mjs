// test_stream_rate.mjs - live token-rate window, EXECUTED FROM REAL SOURCE.
// ratePush / rateTick / rateStop are extracted from gateway.ts and run directly,
// so algorithm drift in the store fails this test loudly.
// Run: node tests/test_stream_rate.mjs   (exit 0 = pass)
import { readFileSync } from 'node:fs'

const src = readFileSync(new URL('../ui-gui-vue3/src/stores/gateway.ts', import.meta.url), 'utf8')

let PASS = 0, FAIL = 0
function check(name, cond, detail = '') {
  if (cond) { PASS++; console.log(`  [PASS] ${name}`) }
  else { FAIL++; console.log(`  [FAIL] ${name} ${detail}`) }
}

function extract(re, label) {
  const m = src.match(re)
  if (!m) throw new Error(`cannot extract ${label} from gateway.ts - update the extractor`)
  return m[1]
}

const pushBody = extract(/function ratePush\(tokens: number\) \{([\s\S]*?)\n  \}/, 'ratePush')
const tickBody = extract(/function rateTick\(\) \{([\s\S]*?)\n  \}/, 'rateTick')
const stopBody = extract(/function rateStop\(\) \{([\s\S]*?)\n  \}/, 'rateStop')

function makeStore() {
  const state = {
    rateSamples: [],
    streamRate: { value: 0 },
    streaming: { value: true },
    now: 0,
    timer: null,
    intervals: 0,
  }
  const fakePerformance = { now: () => state.now }
  const fakeWindow = {
    setInterval: (fn, ms) => { state.intervals++; state.timer = { fn, ms }; return state.timer },
    clearInterval: (t) => { if (state.timer === t) state.timer = null },
  }
  const head = 'let rateTimer = null, rateLastAt = 0, rateTokens = 0\n'
  const bodies = 'function ratePush(tokens){' + pushBody + '}\n'
    + 'function rateTick(){' + tickBody + '}\n'
    + 'function rateStop(){' + stopBody + '}\n'
  const tail = 'return { ratePush, rateTick, rateStop, timer: () => rateTimer }'
  const factory = new Function(
    'rateSamples', 'streaming', 'streamRate', 'performance', 'window',
    head + bodies + tail
  )
  const api = factory(state.rateSamples, state.streaming, state.streamRate, fakePerformance, fakeWindow)
  return { state, api }
}

// 1. delta tokens / delta seconds
{
  const { state, api } = makeStore()
  state.now = 1000; api.ratePush(10)
  state.now = 1500; api.ratePush(20)
  api.rateTick()
  check('rate = delta tokens / delta seconds', state.streamRate.value === 20, String(state.streamRate.value))
  check('timer started on first sample', api.timer() !== null)
  check('refresh interval is 500ms', state.timer && state.timer.ms === 500)
}

// 2. counter reset (a new API call restarts the counter) clears the window
{
  const { state, api } = makeStore()
  state.now = 1000; api.ratePush(10)
  state.now = 1500; api.ratePush(20)
  state.now = 1600; api.ratePush(5)
  check('counter reset clears samples', state.rateSamples.length === 1, JSON.stringify(state.rateSamples))
  api.rateTick()
  check('no negative rate after reset', state.streamRate.value >= 0, String(state.streamRate.value))
}

// 3. idle gap hides the rate
{
  const { state, api } = makeStore()
  state.now = 1000; api.ratePush(10)
  state.now = 1500; api.ratePush(20)
  state.now = 4000; api.rateTick()
  check('idle > 1.5s clears rate', state.streamRate.value === 0, String(state.streamRate.value))
}

// 4. turn end stops the timer
{
  const { state, api } = makeStore()
  state.now = 0; api.ratePush(10)
  state.streaming.value = false
  api.rateTick()
  check('stop clears timer', api.timer() === null)
  check('stop clears rate', state.streamRate.value === 0)
  check('stop clears samples', state.rateSamples.length === 0)
}

// 5. zero token deltas are ignored
{
  const { state, api } = makeStore()
  state.now = 0; api.ratePush(0)
  check('zero tokens ignored', state.rateSamples.length === 0 && api.timer() === null)
}

// 6. samples older than the 2.5s window are trimmed
{
  const { state, api } = makeStore()
  state.now = 0; api.ratePush(10)
  state.now = 1000; api.ratePush(30)
  state.now = 2600; api.ratePush(60)
  check('old samples trimmed', state.rateSamples.length === 2, JSON.stringify(state.rateSamples))
  check('oldest sample dropped', state.rateSamples[0].v === 30, JSON.stringify(state.rateSamples))
}

// 7. a single sample is not enough for a rate
{
  const { state, api } = makeStore()
  state.now = 0; api.ratePush(10)
  api.rateTick()
  check('single sample -> no rate', state.streamRate.value === 0, String(state.streamRate.value))
}

console.log(`\nRESULT: PASS=${PASS} FAIL=${FAIL}`)
process.exit(FAIL === 0 ? 0 : 1)
