// run_all.mjs — run all .mjs tests in tests/ (each in its own process). Exit 0 only if all pass.
// Usage: node tests/run_all.mjs
import { readdirSync } from 'node:fs'
import { spawnSync } from 'node:child_process'
import { fileURLToPath, pathToFileURL } from 'node:url'
import { dirname, join } from 'node:path'

const HERE = dirname(fileURLToPath(import.meta.url))
const ROOT = dirname(HERE)
const tests = readdirSync(HERE).filter(f => f.startsWith('test_') && f.endsWith('.mjs')).sort()

/* The tests import the shipped frontend source, which is TypeScript. Two things
   need help that a plain `node tests/x.mjs` does not give: syntax that type
   stripping alone cannot express (parameter properties, enums) and the
   extensionless relative imports frontend/src itself uses. The flag covers the
   first; ts-register.mjs installs the resolve hook for the second. Both are
   built into Node, so a test run needs no build step and no new dependency. */
const NODE_ARGS = [
  '--experimental-transform-types',
  '--import',
  pathToFileURL(join(HERE, 'ts-register.mjs')).href
]

const failed = []
for (const name of tests) {
  console.log(`\n===== ${name} =====`)
  const r = spawnSync(process.execPath, [...NODE_ARGS, join(HERE, name)], { stdio: 'inherit', cwd: ROOT })
  if (r.status !== 0) failed.push(name)
}

console.log()
if (failed.length) {
  const msg = `FAILED: ${failed.length} / ${tests.length}: ${failed.join(', ')}`
  console.log(msg)
  // GitHub Actions annotation (ignored outside CI): makes the failing test
  // names visible in the run's annotations API without opening logs.
  console.log(`::error::${msg}`)
  process.exit(1)
}
console.log(`ALL PASSED: ${tests.length} tests`)
