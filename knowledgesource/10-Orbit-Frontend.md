# 10. Orbit Frontend

The Orbit surface (`frontend/`) rebuilds `design-preview/pebble-orbit.html` pixel for
pixel and puts real gateway data and transport behind it. The visual layer is frozen:
`styles/orbit.css` is the prototype stylesheet, copied verbatim and kept byte-identical.
Behaviour that needs new CSS goes into `styles/app.css`, never into `orbit.css`.

## Stack and entry

| Item | Detail |
|---|---|
| Framework | Vue 3 `<script setup>` + TypeScript, Vite dev server on `:5180` |
| Entry | `main.ts` -> `App.vue` -> `StreamPane` / `RightDock` / `StatusBar` |
| Prototype runtime | `orbit/runtime.js` (verbatim prototype script), `orbit/theme.js` |
| Transport | `data/gateway.ts`: JSON-RPC 2.0 over WebSocket to the Python gateway (`:19876`) |
| Token | Tauri `get_gateway_token` first, else `window.__PEBBLE_GATEWAY_TOKEN__`, injected by a dev-only Vite plugin reading `~/.pebble/gateway.token` |
| Gateway origin | `http://localhost:5180` is whitelisted in `gateway/ws_server.py` `_ALLOWED_ORIGINS` |

## File index

### data/ — state + transport + rendering

| File | Function | Key symbols | Tag |
|---|---|---|---|
| `gateway.ts` | JSON-RPC 2.0 over WebSocket, request/notify/event dispatch, reconnect | `gateway.connect`, `gateway.request`, `gateway.post`, `onEvent`, `onStatus` | Core |
| `wire.ts` | Gateway events -> store: transcript build, compaction folding, streaming, permission decisions; **an interrupted turn settles on the gateway's body**: the `error` branch appends only the tail missing from the live row (the server copy is authoritative and always a superset), flips in-flight tool cards to `cancelled` exactly like a restored turn, and passes `interrupted` + `message_id` into `finishLive`, so the row on screen is the row the next resume rebuilds | `buildHistory`, `planCompaction`, `toggleCompact`, `loadSession`, `onEvent` | Core |
| `stream.ts` | Rendered message list, starts empty; owns the `StreamItem`/`StreamKind` types | `streamState`, `replaceStream`, `appendStream`, `StreamItem` | Core |
| `blocks.ts` | Structured block model + streaming builder; finished paragraphs commit as markdown, the unfinished tail stays plain text | `Block`, `splitBlocks`, `LiveBuilder` | Core |
| `render.ts` | Markdown runs, fenced code with `span.ln`, user bubbles with attachment chips; `clockOf`/`nowClock` normalise timestamps. Tool calls live in `StreamToolCard.vue`, not here | `renderMarkdown`, `renderUserBubble`, `toolIcon`, `clockOf`, `nowClock` | Core |
| `store.ts` | Session list, plan card, permission queue, overlay state | `sessions`, `plan`, `permissions`, `resolvePermission` | Core |
| `panels.ts` | Right-dock state (usage, files, mcp, ledger, context) refreshed after `gateway.ready` and `session.resume` | `usage`, `files`, `mcp`, `ledger`, `context`, `refreshPanes` | Core |
| `notify.ts` | Five oscillator-synthesised alert voices, two independent picks (reply done / needs input), volume, desktop + per-event alert switches, `when` gate; settings persist to `localStorage` | `notifySettings`, `SOUNDS`, `playSound`, `notify`, `requestDesktopPermission` | Core |
| `fileDrop.ts` | Composer attachment intake: window-level drag, file picker, 10 MB / 20-file caps, text decoded to `text_content`, images to `data_base64` plus a preview URL | `fileDrop`, `ingest`, `removeFile`, `clearFiles`, `wireAttachments`, `installDropZone` | Core |

### components/ — 42 components, one per prototype region

| File | Function | Tag |
|---|---|---|
| `MessageStream.vue` | `v-for` over `streamState.items`; routes each item to `CompactDivider`, `ClarifyCard`, `StreamMessage`, or verbatim `innerHTML` | Core |
| `CompactDivider.vue` | Folded-history divider: prototype `.cdiv` markup (badge / text / arrow), `data-live="1"`, keyboard toggle | Core |
| `ClarifyCard.vue` | Agent question card: prototype `.qcard` markup, per-question steps, single/multi select, a free-form field per question (typed text is always kept and submits alongside the picks; a single-choice click only swaps which preset is selected), and an answer payload that holds presets and typed text apart (`{picked, custom}`); `Submit` / `Skip`, `done` class drives the stamp | Core |
| `StreamMessage.vue` | One agent row rendered from `Block[]` (markdown runs, code, mermaid cards, tool cards, thinking rows) | Core |
| `MermaidCard.vue` | Mermaid diagram card: lazily imports `mermaid`, renders into a dotted stage with the live accent palette, streaming placeholder, parse-error surface, source toggle, copy and PNG export | Core |
| `StreamTimeline.vue` | Right-edge rail: one mark per user row (`className === 'msg user'`), hover opens a teleported card with 120 characters of the message plus `YYYY-MM-DD HH:mm:ss`, click scrolls that row to the viewport centre, a passive scroll listener marks the row being read, `#tlBottom` returns to latest | Core |
| `JumpToLatest.vue` | Bottom-right return button, mounted on `.stream` rather than inside the scroll container (an absolutely positioned child of a scroller travels with the content); passive scroll listener plus a `ResizeObserver` on `#streamInner` decide visibility, hidden within 80px of the end | Core |
| `StreamHead.vue` | Session title, agent, model and message count, all bound to state | Core |
| `UpdateButton.vue` | Update control in the bar, left of the command palette: hidden outside the packaged shell (`__TAURI_INTERNALS__`), silent check on mount, accent dot when a newer version is advertised, pulsing glyph while downloading, then relaunch through `plugin-process`; a manual check always answers through the shared `window.__pebbleToast` stack — `No update` / `You're on the latest version.` or `Update check failed` — because the button returns to its resting shape in a few seconds and silence reads as a dead click; the endpoint and pubkey live in `tauri.conf.json` | 🟡Aux |
| `IslandWrap.vue` | Live-run capsule: busy class and headline from `streamState.streaming` + `plan.steps`, window occupancy from `usage.contextTokens`/`contextPct`, money from `fmtMoney(usage.costToday \| usage.sessionCost)` (converted to the reader's display currency), elapsed from `runTiming`, tokens per second from `formatRate` (shared with `UsagePane`) | Core |
| `PermissionGate.vue` | Permission queue rendered from `permissions` | Core |
| `CompactConfirm.vue` | Compact-context confirmation sheet `#compactConfirm`: owns the `session.compress` call, disables both footer buttons while it runs, closes only on success | Core |
| `UsagePane.vue` | Usage, window ring and the pricing editor: the table comes from `config.get('pricing')` and saves through `pricing.save` with the raw per-period blocks, so `pricing_peak` / `pricing_tier_high` survive; money is a four-cell strip (`session` / `today` / `7 days` / `30 days`, from the four `usage.stats` ranges, today carrying the accent edge) with a CNY/USD switch beside the hero amount that converts for display only, while the pricing editor below stays locked to the table's own currency; its live rate reads the same `formatRate` as the island, the `input tokens` / `output tokens` boxes print their share of the session to two decimals (rounded independently, because whole percent collapsed a 79.1M/350.6k split into `100%` / `0%`), a `cache hit · session` box shows `usage.cacheHit` against `usage.tokensIn` (the full billed input, cached and uncached), and the `priced` / `unpriced` tag reads `model.options.pricing` with a real tier comparison (`flat rate` / `standard tier` / `high tier`); saving the table also refreshes the model options so the tag stops describing the previous table | Core |
| `PlanCard.vue` | Live plan slip: progress ring from `planProgress()`, steps from `plan.steps`, visibility from `plan.open`; the close button calls `closePlan()`, and a list whose steps are all `completed` retires itself after 1200 ms so the full ring is seen first | Core |
| `FilesPane.vue` | Snapshot timeline from `session.snapshots` (records grouped by `snapshot_id`, then merged per tool within a minute and disclosed six at a time) plus the changed-file list, diffs and per-file revert; the section header carries `Accept all`, the single action a reader has to find, so `.sh-act` sizes it two steps above the 8.5px header at weight 700 with matching padding — one call to `session.file_accept` that then clears the change list and its four counters; `whitelisted paths` carries a `.section-sub` line stating `up to 5 paths the LLM can access`, matching the real ceiling `path_whitelist.save` enforces (`maximum 5 paths allowed`) and `addWhitelistPath` mirrors | Core |
| `McpPanel` / `LedgerPanel` / `ContextPane` | Right-dock panes bound to `panels.ts`; `ContextPane` shows `skills loaded` as the agent's preload list plus the `load_skill` calls of the current turn (reset when the next turn starts, preloaded chips drawn weak) with a hint underneath when the user folder is empty — `no user skills · ask the agent to add one to ~/.pebble/skills`, because the list answers what this conversation loaded, not what is installed — and splits the window from the gateway's real `context_breakdown` (base prompt / preloaded skills / conversation / tool schemas) | Aux |
| `PreviewStage.vue` | Floating stage for the one file the transcript exposed: tab strip from `previewState.entries`, `file.read_text` source into a sandboxed iframe, Code view, device presets, PNG capture | Aux |
| `preview.ts` | Preview state: scans `@pebble-file:` out of `streamState.items[].plain`, reads through `file.read_text`, injects the CSP head plus the capture and comment bridges, owns `capturePreview` and `mergePreviewComments` | `previewState`, `setActive`, `reloadPreview`, `markPreviewChanged`, `capturePreview`, `activateComments`, `mergePreviewComments` | Core |
| `previewBridge.ts` | Comment picker inlined into every preview frame: hover ring, element picker, editor, numbered markers, `pebble-feedback-update` reporting; CSS uses `--fb-*` variables the host supplies | `FEEDBACK_CSS`, `FEEDBACK_JS` | Core |

## Critical paths

### 1. Session resume -> compaction folding -> render

1. `loadSession(sid)` calls `session.resume`; the gateway answers with the full
   transcript from `SessionStore.get_messages(sid, limit=999999)` — every role, every flag.
2. Boundary rows are `role='system'` whose text starts with `[COMPACT_BOUNDARY:<uuid>]`
   (`agent_core/context_compressor.py CompactBoundaryMessage`).
3. `planCompaction(messages)` splits the transcript at each boundary. The run above a
   boundary is foldable only when **every** rendered row in it carries `is_compacted`;
   a half-marked run stays fully visible instead of hiding rows nobody compacted.
4. `buildHistory` walks the transcript once. A foldable run that is not open returns
   before any markup is built, so a collapsed run costs **zero DOM**, not `display:none`.
   At the boundary index it emits one `kind:'compact'` item carrying `count` (user +
   assistant rows) and `tools` (tool rows).
5. Compaction summaries are stored as `role='user'` with `is_compact_summary=1`, so the
   summary check runs **before** the user branch; otherwise the whole multi-thousand
   character summary renders as a black outgoing bubble. `stripCompactSummary` removes
   the compressor's English scaffolding before the card is rendered.
6. `MessageStream` renders a `compact` item through `CompactDivider`, which reproduces
   the prototype `.cdiv` markup and classes; no new CSS is involved.

### 2. Fold / unfold

1. `toggleCompact(bid)` marks the run open, then `renderSegmentRows(seg)` builds its rows
   through `buildHistory(lastHistory, seg)` — the `only` argument limits the walk to that
   run and returns its buffered rows.
2. The rows are spliced in **in front of** the divider; closing removes exactly the rows
   whose ids are in the cached set. The rest of the list is never rebuilt: replies that
   arrived after the resume live only in `streamState.items`, and a rebuild would drop them.
3. The open state must be recorded **before** rendering — the builder skips folded rows,
   so rendering first returns nothing.
4. The divider's viewport offset is measured before the change and compensated after
   `nextTick`, with `scroll-behavior` suspended for the one `scrollTop` write; otherwise
   the line leaves the viewport and the click reads as a no-op.
5. Rendered rows are cached per boundary id; the cache, the build buffers and the open set
   are cleared on every `session.resume`.

### 3. Live streaming

1. `prompt.submit` -> `assistant_message.start` creates the row through `LiveBuilder`.
2. Deltas coalesce into one paint per 120 ms; only the growing block is repainted.
3. `message.complete` renders the settled text as markdown once.
4. Restored rows never animate (`live: false`): history carries hundreds of thinking rows
   and tool cards, and replaying their entry animations locked the page.

## Test coverage

`tests/run_all.mjs` imports these modules straight from `frontend/src` with no build
step, passing `--experimental-transform-types` and the `tests/ts-register.mjs` resolve
hook, which appends the extensions the source omits from its relative imports.

| Test | Module | Pins |
| --- | --- | --- |
| `tests/test_render_html.mjs` | `data/render.ts` | escaping before any markup, tokenising, code-block gutter and copy button, the markdown block set, composed user rows, the seconds-vs-milliseconds split in `dateOf` |
| `tests/test_blocks_split.mjs` | `data/blocks.ts` | fence routing to code / mermaid / svg, an open fence marked streaming, `LiveBuilder` committing only closed paragraphs and mutating the tail in place |
| `tests/test_stream_state.mjs` | `data/stream.ts` | the `items` array identity across append / replace / clear, and `streaming` settling on every rebuild |

## Boundary notes

- `styles/orbit.css` is frozen. Every deviation from the prototype lives in `styles/app.css`
  and is commented with the reason.
- `.stage:not(.open) * { pointer-events: none }` exists because `.stage-paper` sets
  `pointer-events: auto` and a child re-enables hit testing under a disabled parent — the
  invisible 820x475 sheet swallowed clicks across the right half of the transcript.
- `runtime.js` delegates clicks on `.cdiv`, `.summary-hdr`, `.tool-hdr`; rows rendered from
  the store carry `data-live` and the delegate skips them, so component state and DOM
  classes cannot disagree.
- `messages.tool_calls` holds the agent's execution order
  (`{type:'reasoning'|'text'|'tool'|'question'}`), not OpenAI tool calls. Reading it as
  OpenAI calls made every restored card look "running" and left thousands of infinite
  pulse animations running.
- The expanded reasoning block is the only scroll area inside a row, so it overrides the shared 8px scrollbar with a 4px one: `--line-strong` at rest, `--faint` once the paragraph is hovered, `--accent` while the thumb is dragged.
- Only the reasoning run still being written animates its rail: `paintAgent` marks `live` on the block that is the tail of the row, so a finished run turns static the moment prose, a tool card or a later run lands on top. `.th-static` in `app.css` is what removes the animation; a turn that reasons five times would otherwise keep five infinite scans running until the whole reply ends.
- `tauri.conf.json` disables Tauri's asset-CSP rewriting for both `script-src` and `style-src`. Any inline `<style>` in `index.html` (the splash is one) makes Tauri add a hash to `style-src`, and CSP ignores `'unsafe-inline'` as soon as a hash is present. The preview iframe inherits the host document's CSP through `srcdoc` and its own `<meta>` can only tighten it, so a hashed `style-src` silently strips every inline `<style>` inside the preview while the app shell — which loads a linked stylesheet and only needs `'self'` — stays fine.
- The launch splash lives inline in `index.html`, not in the CSS bundle: it has to paint before the module graph loads, and it keeps its own dark palette instead of following `data-theme` so a light theme cannot cause a white flash. It leaves the moment `pebble:gateway-ready` arrives — no floor, a warm start really is fast — capped at 12 s so a gateway that never answers cannot own the window. It renders only under Tauri (`__TAURI_INTERNALS__`) or with `?splash=1`. Three inclined elliptical tracks carry travelling light arcs via `stroke-dashoffset` (the geometry never rotates), a breathing core sits at the centre, and the exit collapses every orbit into the core while one ring expands and fades.
- Tool calls render as trace rows (`StreamToolCard.vue` + the `.tr-*` block at the end of `orbit.css`): no card border, a 1px rail with the tool glyph as its node, then name / argument / duration on one line. A running call walks a spark down the rail and pings the node ring; a settled one fades the rail and drops a tick at the seam. The status word stays visible only while a call is running or when it failed or was cancelled; on a completed call it fades in with the duration on hover. Duration fades in on hover, expansion goes through `grid-template-rows: 0fr → 1fr`, and the output hangs off a hairline indented under the node.
- A settled turn clears `caret` on every `raw` block in `finishLive`. Those blocks are opened with `caret: true` each time text resumes after a tool card, and only the tail was rewritten into markdown, so the paragraph runs between thinking and tool cards kept a blinking cursor until the session was reopened.
- `sendPrompt` posts `project_path` (from `projects.active`, falling back to the open session's path) alongside `prompt.submit`. The gateway turns it into the turn's working directory and renders it into the system prompt; when the field is missing the backend falls back to its own cwd, which in a packaged build is the sidecar's install directory.
- Quick notes are clickable: `appendToComposer` writes the note into `#composerTa` and dispatches an `input` event, because that textarea has no `v-model` behind it and `runtime.js` only re-runs autosize and the token meter on that event. Existing text is never rewritten — the separator is added only when the field does not already end in whitespace — and the caret is placed at the end. The row is keyboard reachable (`role="button"`, Enter/Space) and the delete button stops propagation so removing a note does not also insert it.
- The two captions in `NotesPane.vue` name the effect instead of the storage: quick notes read `click to insert into your message`, because a row appends its text to the composer, and the user note reads `prepended to every turn · will send to the LLM`, because that text is injected into every turn's prompt.
- The notes pane carries a third block, `03 Personal notebook`: entries live in `notebook.json` under `_DATA_DIR` (global, unaffected by the project path), never reach the composer or a prompt, and carry a `YYYY-MM-DD HH:MM` stamp taken when the draft opens. `add thought` opens a draft in place; it stays local until it has text, so a draft opened and abandoned leaves nothing behind, and every stored entry can be reopened for editing or deleted. It is drawn as a journal rail rather than a card stack — a hairline spine whose node lights while that entry is open, a monospace stamp, 12.5px reading type — so it does not read as a variant of the clickable quick-note rows above it.
- The user-note editor no longer inherits `.lm-ta`, an 8.5px inline field sized for a provider row; it uses `.nb-ta` at 12.5px with 1.75 leading and a 128px minimum height, the same treatment the agent-note editor got in `app.css`.
- Compaction writes the measured `after.tokens` back into `usage.contextTokens`, resets `usage.contextOut`, and recomputes `contextPct` inside `compressSession`. The ring otherwise keeps reading the last call's `latest_input_tokens`, which still describes the transcript as it was before the fold, so the window stayed at its old height until the next prompt returned a usage payload.
- The usage ring is driven from `usage.contextPct` through an inline `strokeDashoffset` on a `pathLength="100"` circle; the CSS contributes only `stroke-dasharray` and a transition. A fixed offset there drew the same two-thirds arc for every session while the label printed the real share, and the `animation … both` kept that first value frozen forever.
- `toSession` resolves the session timestamp as `last_message_at || started_at || updated_at || created_at` and treats 0 as absent. The gateway's list rows (`_row_to_list_item`) expose the start time as `started_at` and answer `last_message_at: null` for a session with no turns yet, so a fallback chain that only knew `updated_at`/`created_at` collapsed to 0 and bucketed a freshly created session into "Earlier" — the newest row landed at the bottom of the switcher.
- Session lists are scoped to `projects.active` in both `fetchSessions` and `bootstrap`; `bootstrap` resolves the registry before asking, because an unscoped `session.list` is exactly what kept the previous project's conversations on screen after a switch. A project with no stored session goes through `createSession()` rather than a bare `session.create`, so the row keeps its `project_path` and lands in the switcher immediately.
- The project switcher is data-driven: `projects` in `store.ts` holds `list` / `active` / `loaded`, `wire.ts` exposes `refreshProjects`, `addProject`, `switchProject` and `removeProject`, and `switchProject` re-runs `bootstrap()` because every session row belongs to the project that was active when it was created. The component had shipped as prototype markup — hardcoded names, no listeners on any of its buttons.
- `paintAgent` writes `plain` on every paint, not only when the turn settles: the island's tok/s, the context split and Copy mid-stream all read that field, and leaving it empty until the end made the rate read `— tok/s` for the whole reply. The island also finds the live row by scanning back for `streaming` rather than assuming the last item, so a row appended after it cannot blank the headline.
- The live tok/s figure has one owner: `runRate` in `wire.ts`, read through `formatRate()`. Every `message.delta` / `thinking.delta` / `tool.call_streaming` carries the backend's cumulative token count for the call in flight (`StreamConsumer._count_tokens`, reasoning included), so a chunk banks its own delta and the time since the previous chunk; `RATE_STOP_EVENTS` (tool start/complete, clarify, permission, usage, complete, error) closes the window, which keeps tool execution and waits out of the divisor, while side-channel pushes such as `todos.update` leave it open. `message.usage.latest_output_tokens` then replaces the estimate banked for the settled call. The earlier formula divided the row's visible characters by the whole run wall clock and again by 3.6 characters per token, so tool time, reasoning tokens and Chinese text each pushed a 30 tok/s reply down to a fraction of a token per second. The island and the pane read the same `formatRate`, which is what keeps them from disagreeing.
- The live-run island reads its tool row from the streaming row's blocks, not from the todo plan: `liveTool` walks the last message's blocks backwards for a `kind === 'tool'`, so the headline shows `Running read_file` and the expanded `tool` row shows `read_file · path · completed` even when the agent keeps no todo list. The plan is only the middle fallback, and `step X of Y` renders only when a plan exists. With no tool in flight the headline separates `Writing…` (last block is `raw`, `text`, `code` or `mermaid`) from `Thinking…` (reasoning).
- Delete is only real for rows that carry a stored id: `message.delete` reaches `session.py delete_message`, which runs `DELETE FROM messages WHERE id=? AND session_id=?` and then removes the matching `agent_chat.db` row by uuid. `finishLive` therefore rewrites the row id from `live-3` to `message.complete.message_id`; without that the numeric guard in `deleteMessage` never fires and the row returns on reload. A pending row is dropped locally, clears `pendingUser` so `user_message.saved` cannot re-add it, and says it was local-only instead of pretending.
- Scroll following is split by event source: the `ResizeObserver` on `#streamInner` is the only path allowed to move the offset (`onGrow` → `follow`), while the `scroll` listener only re-measures (`onScroll`). Routing both through one handler is what made the transcript impossible to scroll up — the follow ran before the measurement that would have cleared the stick flag. `follow()` also compares against `lastTop` and stops when the reader lowered the offset, because a wheel gesture and its scroll event are not synchronous.
- Transcript blocks are one ordered list appended in arrival order: a reasoning run merges into the tail only when that tail is already reasoning, otherwise it starts a new block at the end; a tool card is appended where the call happened. `LiveBuilder` commits finished paragraphs to markdown and keeps the open tail as plain paragraphs so a delta patches one text node, and `finishLive` settles only that tail — it must never reorder the array, which an earlier version did (reasoning + text + tools) and which scrambled every transcript.
- An interrupted turn sets `interrupted` on the row: `finishLive` and `buildHistory` both flip still-running tool cards to `cancelled` because the gateway sends no completion for a call it killed, and the row closes with a dashed `interrupted` marker.
- `frontend/src-tauri/` is the only desktop shell in the tree: package `pebble-orbit-shell`, productName `pebble-orbit`, identifier `com.pebble.agent.orbit`, `frontendDist` `../dist`, `devUrl` 5180. The window is undecorated, so `OrbitalBar` carries `data-tauri-drag-region` as the window grip and `runtime.js` drives `plugin:window|minimize`, `toggle_maximize` and `close` through `__TAURI_INTERNALS__`. The page needs no `@tauri-apps/api`; `gateway.ts` reads its token from `get_gateway_token` the same way. The `brand-ver` badge in `OrbitalBar` is not typed in: `vite.config.ts` reads `version` out of `src-tauri/tauri.conf.json` and defines `__APP_VERSION__` at build time (declared in `src/vite-env.d.ts`), so the badge always names the installer that carries it — `package.json` still says 0.1.0 and is deliberately not the source, since `npm run release -- <version>` rewrites only the Tauri config.
- The LLM pane splits into what every endpoint understands (agent, provider, models, sampling, effort, thinking) and a request-shaping block under a labelled rule: `session hdr`, `max effort` (`downgrade` / `keep`), `reasoning mode` (`thinking` / `effort_only`) and `extra headers`. All four round-trip through `model.provider.save`, whose backend whitelist is `extraHeaders`, `sessionHeader`, `reasoningMode`, `maxEffort`.
- Tool rows store their output in `messages.content`; `tool_context` only carries the call signature. Reading the context as the result is what left restored cards blank, so `add_tool_message` takes an `output` argument and `StreamToolCard` says `output not recorded` for rows written before that.
- Read tools render `already read` on success: the content still travels in `call.output` and in the transcript, the card just stops printing a file the model already consumed.
- The header's `messages` counter is a session counter, not a transcript row count: it reads `sessionState.messageCount`, seeded from the resume payload and incremented as a turn settles. Filtering `streamState.items` under-reports because folded runs cost no DOM.
- `pricing.save` validates and rewrites the whole `pricing` block per model, so the pricing pane edits the raw table from `config.get('pricing')` instead of a flattened row list; flattening silently drops `pricing_peak`, `pricing_offpeak` and `pricing_tier_high`.
- The usage dock asks for four windows in one refresh: `session` (scoped to the live session uuid), `today`, `7d`, `30d`. `total_cost` of the first lands in `usage.sessionCost`, the other three in `costToday` / `cost7d` / `cost30d`. The hero's "today" is therefore the whole database's day, not the session total — the two shared one field before, which is why a session whose model has no price still printed a figure. `refreshPanes` writes all four through `Promise.allSettled`, and `message.usage` keeps updating only the session figure in real time.
- No pane keeps prototype numbers any more: `panels.ts` starts every usage field at zero and leaves `history` empty, so an unanswered RPC reads zero instead of a plausible figure. Money is stored in one currency and read in another: `agent_chat_costs.currency` is CNY for every row, the price table is CNY, and `fmtMoney` converts through `CNY_PER_USD` (7.2, the single place to fix a stale rate) before `fmtCost` prints it. The switch beside the hero amount moves only `usage.displayCurrency` (localStorage `pebble.displayCurrency`, default CNY); the pricing inputs ignore it because a table is data and its numbers must not change meaning with a toggle. `syncUsage` no longer reads `period` as a currency — comparing `peak` / `offpeak` against `'cny'` flipped every time-of-day model's pane to `$`.
- The preview stage renders only what the conversation produced: `@pebble-file:` directives become entries, `file.read_text` supplies the source, and prose that merely mentions the directive is filtered by an extension gate. The iframe keeps `sandbox="allow-scripts"` without `allow-same-origin`, so the page cannot reach the app; a stricter CSP (`connect-src 'none'`, `form-action 'none'`) is injected as the first head node. `src/vendor/html-to-image.js` is the bundled capture library, inlined into every preview because a cross-origin frame cannot rasterise itself from the outside.
- Comments are not a separate channel. The picker reports annotations as `pebble-feedback-update`, they sit on `previewState.comments`, and `respondClarify` merges them into the clarify answer whose option asks for a change (`修改` / `Modify`) as a `Modify\n\n[HTML Page Feedback] ...` block. Switching files or reloading clears them, since a selector only means something inside one document.
- `messages.created_at` is an epoch float (`REAL NOT NULL` in the gateway store), not a
  `YYYY-MM-DD HH:MM:SS` string. Slicing it as one surfaced the fractional digits as the
  row clock (`28504 · sent`). `dateOf`/`clockOf` in `render.ts` now decide seconds versus
  milliseconds in one place, and both the row stamps and the timeline tooltip read
  through them.

## Gateway wiring status

Wired RPC (called from this surface today):

| Area | Method | Called from |
|---|---|---|
| Bootstrap | `session.list`, `session.create`, `session.most_recent`, `session.resume` | `bootstrap()` |
| Transcript | `session.interrupt`, `prompt.submit` | `sendPrompt`, `interruptSession` |
| Session rows | `session.title`, `session.delete` | `renameSession`, `deleteSession` (runtime conversation menu) |
| Approval | `permission.decide`, `permission.mode.get`, `permission.mode.set` | `decidePermission`, `syncPermissionMode`, `setPermissionMode` |
| Files | `session.file_changes`, `session.file_accept`, `session.file_revert`, `session.file_revert_all` | `refreshFileChanges`, `acceptFileChanges`, `revertFileChange`, `revertToMessage` |
| Question | `clarify.respond`, `clarify.list` | `respondClarify`, `skipClarify`, `pullClarify` |
| Dock | `usage.stats`, `mcp.list`, `memory.list`, `config.get`, `session.file_changes` | `refreshPanes` |

Events handled (`onEvent` switch): `user_message.saved`, `message.delta`, `thinking.delta`,
`tool.call_streaming`, `tool.start`, `tool.complete`, `message.complete`, `message.usage`,
`todos.update`, `session.created`, `session.updated`, `permission.request`,
`permission.resolved`, `clarify.request`, `clarify.resolved`, `file.change`,
`files.accepted`, `files.reverted`, `prompt.rejected`, `error`, `gateway.ready`.

Still unconnected, in rough priority order: status-bar counters, model panel
(`model.options` / `model.switch` / `model.params.set` / `model.provider.*`), MCP panel
actions, `theme.get` / `theme.save`, memory and notes panes, command palette
(`complete.slash` / `slash.exec` / `command.dispatch`), pairing, file tree and preview
(`file.search` / `file.read_text` / `file.write_base64`), `agent.list` / `agent.switch`,
`config.set` / `pricing.save`, `session.search`, `message.delete`, `path_whitelist.*`.

Notes that cost time to find:

- Session rows are rendered by Vue (`SessionSwitcher.vue` `v-for`), so the prototype's
  conversation menu cannot mutate them. Rename / delete / pin go through `wire.ts` and the
  store; the DOM branches only run for rows without `data-id`.
- The permission-mode control's `MODES` table is `manual` / `auto` / `readonly`; the mode is
  read back from `permission.mode.get` on load and again on `pebble:gateway-ready`.
- `message.usage` carries `this_call`; each one appends a row to `usage.history` and bumps
  `usage.calls`. Every field is optional — a tokens-only payload must not clear the cost.
- `clarify.request` arrives in two shapes (structured `questions`, and the older flat
  `question` + `choices`); both normalise to `ClarifyQuestion[]`.
- Prototype runtime handlers for `.q-opt` / `.q-go` / `.q-skip` and `.cdiv` skip live rows
  via `data-live`; without that guard they flip classes that Vue owns and the card
  desynchronises.

### 4. Rail jump navigation

1. `StreamTimeline` derives its marks from `streamState.items`, keeping only rows whose `className` is `msg user`; tool and agent rows never enter the rail, so the mark count equals the user turn count.
2. Each mark stores the row index in that array. `rowEl(index)` reads `#streamInner.children[index]`, which is valid because every row renders exactly one root node in item order; this avoids threading an anchor attribute through `CompactDivider`, `ClarifyCard` and `StreamMessage`.
3. Hover shows `plain` truncated to 60 characters plus a stamp. History supplies `created_at` as an epoch float while live rows supply a formatted string, so `stampOf` scales anything below `1e11` by 1000 and both paths end up as `YYYY-MM-DD HH:mm:ss`.
4. A click calls `scrollIntoView({ behavior: 'smooth', block: 'center' })`. A passive scroll listener on `#streamBody` marks the first row whose bottom is still below the viewport top, and `#tlBottom` scrolls to `scrollHeight`. The rail itself scrolls once it holds more marks than its height allows, with `justify-content: safe center` so the first mark stays reachable.

### 5. Composer attachments

1. `installDropZone()` is guarded by a module flag and listens on the document for dragenter / dragover / dragleave / drop, driving `fileDrop.over` so `DropOverlay` renders the veil without the caller tracking drag state. The handler keeps a counter because dragenter and dragleave fire for every child element the pointer crosses.
2. `ingest()` reads each file through `FileReader`, rejects anything above 10 MB, stops at 20 per turn and skips duplicates. Images keep a `dataUrl` for the tray thumbnail; text files decode the base64 payload into `text_content`.
3. `ComposerWrap` renders the tray from `fileDrop.files` using the prototype's film markup unchanged. `Clear` empties the batch, the per-file cross removes one entry, and the `Auto` / `Low` / `Orig` row only appears when the batch holds an image, writing `detail` onto every image in it.
4. `wireAttachments()` produces the socket shape. `sendPrompt(text, attachments)` posts it under `prompt.submit` and renders the same list into the local user bubble through `renderUserBubble`, so the row looks identical before and after the gateway echoes it back.
5. A turn carrying attachments but no text is valid. `runtime.js`'s composer submit reads the batch, sends it and clears the tray on success.

### 6. Compact request and summary rendering

1. `#compactBtn` in `StreamHead.vue` only opens the shared overlay `#compactConfirm` through `openOverlay` in `runtime.js`; it never calls the gateway itself. Cancel, the close cross, the backdrop and Escape all route through the same `.overlay` handling.
2. `CompactConfirm.vue` owns the call: `run()` disables both footer buttons, awaits `compressSession()`, toasts the outcome, and closes only on success so a failed run can be retried without reopening the sheet.
3. `summaryInner` splits the stripped summary at `#` headings, one `.cs-sec` per heading: an ordinal in the left rail (`cs-sec-num`), a serif title, an item count, and a toned rail whose colour comes from `summaryTone` — decisions land on accent, todos on gold, context on teal, everything else on faint.
4. `summaryBody` parses each section into `.cs-items` lists and `.cs-para` paragraphs; `inlineSummary` escapes first and only then restores `code` / bold / italic spans, so summary text can never inject markup. Inline `code` becomes a `.cs-code` chip pinned to the accent colour rather than the section tone, because paths and identifiers have to read the same everywhere.
