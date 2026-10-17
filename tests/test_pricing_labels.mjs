// test_pricing_labels.mjs — pricing panel column-header labels (source-level checks)
// Run: node tests/test_pricing_labels.mjs   (exit 0 = pass)
import { readFileSync } from 'node:fs'

const vue = readFileSync(new URL('../ui-gui-vue3/src/components/PricingPanel.vue', import.meta.url), 'utf8')
const cssDir = new URL('../ui-gui-vue3/src/styles/', import.meta.url)
const indexCss = readFileSync(new URL('index.css', cssDir), 'utf8')
const imported = [...indexCss.matchAll(/@import\s+'\.\/([^']+)'/g)].map((m) => m[1])
const css = imported.length
  ? imported.map((f) => readFileSync(new URL(f, cssDir), 'utf8')).join('\n')
  : indexCss

let pass = 0, fail = 0
function check(name, cond) {
  if (cond) { pass++; console.log(`  [PASS] ${name}`) }
  else { fail++; console.log(`  [FAIL] ${name}`) }
}

console.log('1) label constants')
check('FIELD_LABELS defined', vue.includes("const FIELD_LABELS = ['cache miss', 'cache hit', 'output']"))
check('label cache miss present', vue.includes("'cache miss'"))
check('label cache hit present', vue.includes("'cache hit'"))
check('label output present', vue.includes("'output'"))

console.log('2) template structure')
const vforCount = (vue.match(/v-for="\(lb, j\) in FIELD_LABELS"/g) || []).length
check('all three sections render labels via v-for (3)', vforCount === 3)
const flbCount = (vue.match(/class="mp-flb"/g) || []).length
check('mp-flb label elements (3)', flbCount === 3)
check('base row index binding r.base[j]', vue.includes(':value="r.base[j]"'))
check('peak row index binding r.peak[j]', vue.includes(':value="r.peak[j]"'))
check('off row index binding r.off[j]', vue.includes(':value="r.off[j]"'))
check('legacy triple inputs removed (base)', !vue.includes(':value="r.base[0]"'))
check('legacy placeholder removed', !vue.includes('placeholder="hit"'))

console.log('3) CSS')
check('.mp-f column container defined', css.includes('.mp-f { flex: 1; min-width: 0; display: flex; flex-direction: column; gap: 2px; }'))
check('.mp-flb label style defined', css.includes('.mp-flb {'))
check('.mp-flb uses accent color (no dim opacity)',
  /\.mp-flb \{[^}]*color: var\(--accent\);[^}]*\}/.test(css) && !/\.mp-flb \{[^}]*opacity/.test(css))
check('mp-f inner input size fixed', css.includes('.mp-f .mp-inp { flex: none; width: 100%; }'))
check('mp-tag aligned to input row (margin-top 12px)', css.includes('margin-top: 12px;') && css.includes('.mp-tag {'))
check('mp-pr uses flex-start alignment', css.includes('.mp-pr { display: flex; align-items: flex-start; gap: 6px; margin-top: 3px; }'))

console.log('='.repeat(50))
console.log(`RESULT: ${pass} passed / ${fail} failed`)
process.exitCode = fail ? 1 : 0
