// test_ws_recovery.mjs — WS disconnect recovery 3-layer fix
// 1) source assertions (gateway.ts / gatewayClient.ts / ws_server.py)
// 2) recoverAfterReconnect extracted from real source + 5 scenario simulations
// Run: node tests/test_ws_recovery.mjs   (exit 0 = pass)
import { readFileSync } from 'node:fs'

const gw = readFileSync(new URL('../ui-gui-vue3/src/stores/gateway.ts', import.meta.url), 'utf8')
const gc = readFileSync(new URL('../ui-gui-vue3/src/services/gatewayClient.ts', import.meta.url), 'utf8')
const ws = readFileSync(new URL('../gateway/ws_server.py', import.meta.url), 'utf8')

let pass = 0, fail = 0
function check(name, cond) {
  if (cond) { pass++; console.log(`  [PASS] ${name}`) }
  else { fail++; console.log(`  [FAIL] ${name}`) }
}

console.log('1) Fix A: gateway.ts disconnect recovery')
check('flag declared _streamLostOnDisconnect', gw.includes('let _streamLostOnDisconnect = false'))
check('flag set on disconnect (onStatus else branch)', gw.includes("else if (streaming.value) { _streamLostOnDisconnect = true; failPendingStream('Connection lost') }"))
check('reconnect consumes flag + delayed call', gw.includes("if (_streamLostOnDisconnect) { _streamLostOnDisconnect = false; setTimeout(() => { recoverAfterReconnect().catch(console.error) }, 800) }"))
check('recoverAfterReconnect defined', gw.includes('async function recoverAfterReconnect()'))
check('recovery calls resumeSession', gw.includes('await resumeSession(sid)'))
check('poll cap 12', gw.includes('for (let i = 0; i < 12; i++)'))
check('poll interval 10s', gw.includes('setTimeout(r, 10000)'))
check('tail user check', gw.includes("if (!last || last.role !== 'user') return"))

console.log('2) Fix C: gatewayClient.ts lifecycle logs')
check('field _lastCloseAt', gc.includes('private _lastCloseAt = 0'))
check('field _reconnectCount', gc.includes('private _reconnectCount = 0'))
check('reconnect gap log', gc.includes('reconnected after ${gap}ms'))
check('close log with code/clean', gc.includes('closed (code=${ev.code}, clean=${ev.wasClean})'))
check('reconnect attempt log', gc.includes('reconnect attempt #${n}'))
check('onerror has output', gc.includes("[WS] error (details via onclose)"))
check('connect failure log', gc.includes('[WS] connect failed'))

console.log('3) Fix B: ws_server.py no longer silent on disconnect')
check('dedup flag _send_failed', ws.includes('self._send_failed = False'))
check('drop log (with code)', ws.includes('Stream events dropped (connection closed, code='))
check('dedup guard (log once)', ws.includes('if not self._send_failed:'))
check('generic exception log', ws.includes('[WS] Stream send failed:'))
check('no bare pass', !/except websockets\.ConnectionClosed:\s*\n\s*pass/.test(ws))

console.log('4) recoverAfterReconnect logic simulation (real source extraction)')
const m = gw.match(/async function recoverAfterReconnect\(\) \{[\s\S]*?\n  \}/)
check('function source extracted', !!m)
if (m) {
  const fnSrc = m[0]
  function runScenario(resumeResults, streamingInit, flipStreamingAt = -1) {
    const env = {
      sessionIdRef: { value: 'sid1' },
      streaming: { value: streamingInit },
      messages: { value: [] },
      resumeCalls: 0,
    }
    const resumeSession = async () => {
      env.resumeCalls++
      if (flipStreamingAt === env.resumeCalls) env.streaming.value = true
      env.messages.value = resumeResults[Math.min(env.resumeCalls - 1, resumeResults.length - 1)] || []
    }
    const fakeSetTimeout = (fn) => { fn(); return 0 }
    const factory = new Function('sessionIdRef', 'streaming', 'messages', 'resumeSession', 'setTimeout',
      fnSrc + '\nreturn recoverAfterReconnect')
    const fn = factory(env.sessionIdRef, env.streaming, env.messages, resumeSession, fakeSetTimeout)
    return { run: fn, env }
  }

  // S1: DB already has full reply -> single resume then stop
  {
    const t = runScenario([[{ role: 'user' }, { role: 'agent' }]], false)
    await t.run()
    check(`S1 DB has reply -> 1 resume (actual ${t.env.resumeCalls})`, t.env.resumeCalls === 1)
  }
  // S2: DB only has user (backend still running) -> poll until agent appears
  {
    const t = runScenario([[{ role: 'user' }], [{ role: 'user' }, { role: 'agent' }]], false)
    await t.run()
    check(`S2 poll until stored -> 2 resumes (actual ${t.env.resumeCalls})`, t.env.resumeCalls === 2)
  }
  // S3: user already started a new stream on reconnect -> do not disturb
  {
    const t = runScenario([[]], true)
    await t.run()
    check(`S3 streaming in progress -> 0 resumes (actual ${t.env.resumeCalls})`, t.env.resumeCalls === 0)
  }
  // S4: backend never stores -> stop after cap of 12 (1+12=13)
  {
    const t = runScenario([[{ role: 'user' }]], false)
    await t.run()
    check(`S4 poll cap reached -> 13 calls (actual ${t.env.resumeCalls})`, t.env.resumeCalls === 13)
  }
  // S5: user sends new message mid-poll -> stop immediately
  {
    const t = runScenario([[{ role: 'user' }]], false, 2)
    await t.run()
    check(`S5 new message mid-poll -> stop (actual ${t.env.resumeCalls})`, t.env.resumeCalls === 2)
  }
}

console.log('='.repeat(52))
console.log(`RESULT: ${pass} passed / ${fail} failed`)
process.exitCode = fail ? 1 : 0
