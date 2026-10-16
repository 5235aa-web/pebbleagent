// run_all.mjs — run all .mjs tests in tests/ (each in its own process). Exit 0 only if all pass.
// Usage: node tests/run_all.mjs
import { readdirSync } from 'node:fs'
import { spawnSync } from 'node:child_process'
import { fileURLToPath } from 'node:url'
import { dirname, join } from 'node:path'

const HERE = dirname(fileURLToPath(import.meta.url))
const ROOT = dirname(HERE)
const tests = readdirSync(HERE).filter(f => f.startsWith('test_') && f.endsWith('.mjs')).sort()

const failed = []
for (const name of tests) {
  console.log(`\n===== ${name} =====`)
  const r = spawnSync(process.execPath, [join(HERE, name)], { stdio: 'inherit', cwd: ROOT })
  if (r.status !== 0) failed.push(name)
}

console.log()
if (failed.length) {
  console.log(`FAILED: ${failed.length} / ${tests.length}: ${failed.join(', ')}`)
  process.exit(1)
}
console.log(`ALL PASSED: ${tests.length} tests`)
