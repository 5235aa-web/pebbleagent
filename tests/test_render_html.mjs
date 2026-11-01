#!/usr/bin/env node
/* Transcript renderers: escaping, highlighting, markdown, composed rows.

   These functions produce the HTML assigned to live message rows. An escaping
   regression here is an XSS regression, and a markup regression is a visual
   bug that no type check can catch, so every assertion below pins the exact
   output the shipped orbit.css class names depend on.

   Run: node --experimental-transform-types --import ./tests/ts-register.mjs \
          tests/test_render_html.mjs      (exit 0 = pass)
*/
import assert from 'node:assert/strict'
import {
  toolIcon,
  escapeHtml,
  highlight,
  renderCodeBlock,
  renderMarkdown,
  renderAttachments,
  renderUserBubble,
  renderPlain,
  nowClock,
  dateOf,
  clockOf
} from '../frontend/src/data/render.ts'

let passed = 0
const failures = []

function check(label, fn) {
  try {
    fn()
    passed++
    console.log(`[PASS] ${label}`)
  } catch (err) {
    failures.push(`${label}: ${err.message.split('\n')[0]}`)
    console.log(`[FAIL] ${label}: ${err.message.split('\n')[0]}`)
  }
}

/* ---- escapeHtml: the only thing standing between model output and innerHTML ---- */

check('escapeHtml escapes amp, angle brackets and double quotes', () => {
  assert.equal(escapeHtml('&<>"'), '&amp;&lt;&gt;&quot;')
})

check('escapeHtml escapes the ampersand first', () => {
  assert.equal(escapeHtml('&lt;'), '&amp;lt;')
})

check('escapeHtml leaves single quotes and plain text alone', () => {
  assert.equal(escapeHtml("it's fine"), "it's fine")
})

check('escapeHtml passes an empty string through', () => {
  assert.equal(escapeHtml(''), '')
})

/* ---- toolIcon: the regex chain picks one of five glyphs ---- */

check('toolIcon maps a search tool to the search glyph', () => {
  assert.ok(toolIcon('grep').includes('<circle cx="11" cy="11" r="7"/>'))
  assert.ok(toolIcon('search_files').includes('<circle cx="11" cy="11" r="7"/>'))
})

check('toolIcon maps a write tool to the edit glyph', () => {
  assert.ok(toolIcon('write_file').includes('M12 20h9'))
  assert.ok(toolIcon('move_file').includes('M12 20h9'))
  assert.ok(toolIcon('todo_write').includes('M12 20h9'))
})

check('toolIcon maps a read tool to the read glyph', () => {
  assert.ok(toolIcon('read_file').includes('M14 2H6'))
  assert.ok(toolIcon('list_processes').includes('M14 2H6'))
})

check('toolIcon maps a shell tool to the shell glyph', () => {
  assert.ok(toolIcon('execute_shell').includes('<polyline points="4 17 10 11 4 5"/>'))
})

check('toolIcon falls back to the form glyph', () => {
  assert.ok(toolIcon('kill_process').includes('<rect x="3" y="4" width="18" height="16" rx="2"/>'))
})

check('toolIcon emits a 12px stroked svg for every name', () => {
  for (const name of ['grep', 'write_file', 'read_file', 'execute_shell', 'kill_process']) {
    const svg = toolIcon(name)
    assert.ok(svg.startsWith('<svg width="12" height="12"'), name)
    assert.ok(svg.endsWith('</svg>'), name)
  }
})

/* ---- highlight: five token classes, never re-tokenizing strings or comments ---- */

check('highlight marks a keyword', () => {
  assert.ok(highlight('const x = 1', 'js').includes('<span class="tk-k">const</span>'))
})

check('highlight marks a number, including css units', () => {
  assert.ok(highlight('x = 1', 'js').includes('<span class="tk-n">1</span>'))
  assert.ok(highlight('100px', 'css').includes('<span class="tk-n">100px</span>'))
})

check('highlight marks a call by its trailing paren', () => {
  assert.equal(highlight('foo(1)', 'js'), '<span class="tk-f">foo</span>(<span class="tk-n">1</span>)')
})

check('highlight marks a comment as one run', () => {
  assert.equal(highlight('// hi', 'js'), '<span class="tk-c">// hi</span>')
})

check('highlight marks a string run and escapes its quotes', () => {
  assert.equal(highlight('"a"', 'js'), '<span class="tk-s">&quot;a&quot;</span>')
})

check('highlight does not tokenize inside a string', () => {
  assert.equal(highlight('"const"', 'js'), '<span class="tk-s">&quot;const&quot;</span>')
})

check('highlight never emits raw angle brackets', () => {
  assert.equal(highlight('<b>', 'js'), '&lt;b&gt;')
})

check('highlight resolves language aliases', () => {
  assert.ok(highlight('const', 'jsx').includes('tk-k'))
  assert.ok(highlight('None', 'py').includes('tk-k'))
  assert.ok(highlight('const', 'typescript').includes('tk-k'))
})

/* ---- renderCodeBlock: header, gutter and escaped body ---- */

check('renderCodeBlock counts the lines it renders', () => {
  const html = renderCodeBlock('js', 'a\nb')
  assert.ok(html.includes('<span class="code-lines">2 lines</span>'))
  assert.ok(html.includes('<div class="gutter">1\n2</div>'))
})

check('renderCodeBlock drops one trailing newline before counting', () => {
  assert.ok(renderCodeBlock('py', 'x\n').includes('<span class="code-lines">1 lines</span>'))
})

check('renderCodeBlock names an unlabelled block "code"', () => {
  const html = renderCodeBlock('', '')
  assert.ok(html.includes('<span class="code-lang">code</span>'))
  assert.ok(html.includes('<span class="code-lines">1 lines</span>'))
})

check('renderCodeBlock escapes the language label', () => {
  assert.ok(renderCodeBlock('<img>', 'x').includes('&lt;img&gt;'))
})

check('renderCodeBlock keeps the copy button and pre surface', () => {
  const html = renderCodeBlock('js', 'x')
  assert.ok(html.includes('<button class="code-copy" data-copy>'))
  assert.ok(html.includes('<pre class="code-pre">'))
})

check('renderCodeBlock escapes script tags in code', () => {
  const html = renderCodeBlock('js', '<script>alert(1)</script>')
  assert.ok(!html.includes('<script>'))
  assert.ok(html.includes('&lt;script&gt;'))
})

/* ---- renderMarkdown: paragraphs, h4, lists, inline runs, caret ---- */

check('renderMarkdown renders every heading level as h4', () => {
  assert.equal(renderMarkdown('# Title'), '<h4>Title</h4>')
  assert.equal(renderMarkdown('###### deep'), '<h4>deep</h4>')
})

check('renderMarkdown renders an unordered list', () => {
  assert.equal(renderMarkdown('- a\n- b'), '<ul><li>a</li><li>b</li></ul>')
})

check('renderMarkdown renders an ordered list without its markers', () => {
  assert.equal(renderMarkdown('1. a\n2. b'), '<ol><li>a</li><li>b</li></ol>')
})

check('renderMarkdown splits paragraphs on a blank line only', () => {
  assert.equal(renderMarkdown('a\n\nb'), '<p>a</p><p>b</p>')
})

check('renderMarkdown keeps a single newline as a line break', () => {
  assert.equal(renderMarkdown('a\nb'), '<p>a<br>b</p>')
})

check('renderMarkdown renders strong and emphasis', () => {
  assert.equal(renderMarkdown('**b**'), '<p><strong>b</strong></p>')
  assert.equal(renderMarkdown('*i*'), '<p><em>i</em></p>')
})

check('renderMarkdown renders inline code escaped', () => {
  assert.equal(renderMarkdown('`c`'), '<p><code>c</code></p>')
  assert.equal(renderMarkdown('`<b>`'), '<p><code>&lt;b&gt;</code></p>')
})

check('renderMarkdown does not re-parse markup inside inline code', () => {
  assert.equal(renderMarkdown('`**x**`'), '<p><code>**x**</code></p>')
})

check('renderMarkdown renders a link with the external target', () => {
  assert.equal(
    renderMarkdown('[t](http://x)'),
    '<p><a href="http://x" target="_blank" rel="noreferrer">t</a></p>'
  )
})

check('renderMarkdown escapes raw html', () => {
  assert.equal(renderMarkdown('<b>'), '<p>&lt;b&gt;</p>')
})

check('renderMarkdown returns nothing for empty input', () => {
  assert.equal(renderMarkdown(''), '')
})

check('renderMarkdown pins the caret to the last paragraph while streaming', () => {
  assert.equal(renderMarkdown('x', true), '<p>x<span class="caret"></span></p>')
  assert.equal(renderMarkdown('', true), '<p><span class="caret"></span></p>')
})

check('renderMarkdown leaves fences to the block splitter', () => {
  const html = renderMarkdown('```js\nx\n```')
  assert.ok(!html.includes('<code>'))
  assert.ok(html.includes('```'))
})

/* ---- renderPlain: the streaming fast path ---- */

check('renderPlain splits paragraphs and escapes', () => {
  assert.equal(renderPlain('a\n\nb', false), '<p>a</p><p>b</p>')
  assert.equal(renderPlain('<x>', false), '<p>&lt;x&gt;</p>')
})

check('renderPlain keeps single newlines as line breaks', () => {
  assert.equal(renderPlain('a\nb', false), '<p>a<br>b</p>')
})

check('renderPlain appends the caret only to the last paragraph while streaming', () => {
  assert.equal(renderPlain('a\n\nb', true), '<p>a</p><p>b<span class="caret"></span></p>')
})

/* ---- renderAttachments ---- */

check('renderAttachments returns nothing for an empty list', () => {
  assert.equal(renderAttachments([]), '')
})

check('renderAttachments emits one chip per attachment with its size', () => {
  const html = renderAttachments([
    { name: 'a.txt', size: '1.2 KB', kind: 'file' },
    { name: 'b.png', size: '3 KB', kind: 'image' }
  ])
  assert.equal((html.match(/attach-chip/g) || []).length, 2)
  assert.ok(html.includes('a.txt'))
  assert.ok(html.includes('1.2 KB'))
})

check('renderAttachments picks the glyph from kind', () => {
  const image = renderAttachments([{ name: 'b.png', size: '1 KB', kind: 'image' }])
  const file = renderAttachments([{ name: 'a.txt', size: '1 KB', kind: 'file' }])
  assert.ok(image.includes('<circle cx="8.5" cy="8.5" r="1.5"/>'))
  assert.ok(file.includes('M14 2H6'))
})

check('renderAttachments escapes the file name and size', () => {
  const html = renderAttachments([{ name: '<i>.txt', size: '"><b>', kind: 'file' }])
  assert.ok(!html.includes('<i>'))
  assert.ok(html.includes('&lt;i&gt;.txt'))
  assert.ok(!html.includes('"><b>'))
})

/* ---- renderUserBubble ---- */

check('renderUserBubble escapes the message text and the clock', () => {
  const html = renderUserBubble('<b>hi</b>', [], '"><x>')
  assert.ok(html.includes('&lt;b&gt;hi&lt;/b&gt;'))
  assert.ok(html.includes('&quot;&gt;&lt;x&gt;'))
})

check('renderUserBubble says "sent" when no token count is given', () => {
  assert.ok(renderUserBubble('hi', [], '10:00').includes('<span>sent</span>'))
})

check('renderUserBubble prints the token count when given', () => {
  assert.ok(renderUserBubble('hi', [], '10:00', 42).includes('<span>42 tokens</span>'))
})

check('renderUserBubble omits row actions without an item id', () => {
  assert.ok(!renderUserBubble('hi', [], '10:00').includes('msg-actions'))
})

check('renderUserBubble adds copy and delete actions with a safe item id', () => {
  const html = renderUserBubble('hi', [], '10:00', 0, 'a"1')
  assert.ok(html.includes('class="msg-actions ma-user"'))
  assert.ok(html.includes('data-msg-act="copy"'))
  assert.ok(html.includes('data-msg-act="del"'))
  assert.ok(html.includes('data-item-id="a&quot;1"'))
})

check('renderUserBubble forwards attachments', () => {
  const html = renderUserBubble('hi', [{ name: 'a.txt', size: '1 KB', kind: 'file' }], '10:00')
  assert.ok(html.includes('user-attach'))
  assert.ok(html.includes('a.txt'))
})

/* ---- dateOf / clockOf: history floats and live clock strings ---- */

check('dateOf rejects empty and unparseable input', () => {
  assert.equal(dateOf(''), null)
  assert.equal(dateOf(null), null)
  assert.equal(dateOf(undefined), null)
  assert.equal(dateOf('nope'), null)
})

check('dateOf reads a seconds epoch into milliseconds', () => {
  assert.equal(dateOf(1700000000).getTime(), 1700000000000)
})

check('dateOf leaves a milliseconds epoch alone', () => {
  assert.equal(dateOf(1700000000000).getTime(), 1700000000000)
})

check('dateOf reads a numeric string as an epoch', () => {
  assert.equal(dateOf('1700000000').getTime(), 1700000000000)
})

check('dateOf parses a stored timestamp as local time', () => {
  const d = dateOf('2026-10-29 10:22:35')
  assert.ok(d instanceof Date)
  assert.equal(d.getFullYear(), 2026)
  assert.equal(d.getMonth(), 9)
  assert.equal(d.getDate(), 29)
  assert.equal(d.getHours(), 10)
  assert.equal(d.getMinutes(), 22)
})

check('dateOf passes an ISO string with a zone to the Date parser', () => {
  assert.equal(dateOf('2026-10-29T10:22:35Z').getTime(), Date.parse('2026-10-29T10:22:35Z'))
})

check('clockOf prints HH:MM from a stored timestamp', () => {
  assert.equal(clockOf('2026-10-29 10:22:35'), '10:22')
  assert.equal(clockOf('2026-01-02 03:04:05'), '03:04')
})

check('clockOf returns nothing when it cannot read the timestamp', () => {
  assert.equal(clockOf('bad'), '')
  assert.equal(clockOf(''), '')
})

check('nowClock always prints a zero-padded HH:MM', () => {
  assert.match(nowClock(), /^\d{2}:\d{2}$/)
})

console.log(`\nRESULT: PASS=${passed} FAIL=${failures.length}`)
if (failures.length) {
  for (const f of failures) console.log(`  - ${f}`)
  process.exit(1)
}
