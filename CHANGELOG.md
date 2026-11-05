# Changelog

Every release gets a section here. `frontend/scripts/publish.mjs` copies the
section whose heading matches `version` in `frontend/src-tauri/tauri.conf.json`
into the GitHub release body **and** into the `notes` field of `latest.json`,
which is what the in-app update sheet shows before installing. Add the section
before running `npm run release`; the release stops if it is missing, because a
feed with empty notes tells the user nothing.

## 1.1.5

- The MCP panel stopped putting a token field on every server. Only a server whose env carries a credential gets one now, and it writes into the variable that server actually reads (`GITHUB_PERSONAL_ACCESS_TOKEN`); before this every entry showed "paste token…" and the secret went to a fixed `MCP_TOKEN` key that github never looks at.
- A server that is configured but cannot start now says what it needs. `mcp.list` reports `ready`, `missing`, `requires_token` and `token_env` per server, plus an `available` list drawn from `mcp_install.json`; an `npx` server on a machine without Node, a `uvx` server without uv, and `codebase-memory` still pointing at `REPLACE_WITH_...` all show `needs Node.js 18+` and an Install button instead of looking broken. `mcp.install_server` handles npx and uvx entries now, not just download-type ones.
- Saving a token no longer floods the approval queue. Both MCP RPCs used the bundled `.mcp.json` as their base when no user file existed, so the first secret saved on a fresh install copied all six bundled servers into `~/.pebble/.mcp.json` and approved only the edited one; the other five appeared as "wants to run" banners. Each write now touches a single entry.
- The command palette is built from the stores now instead of static markup. It used to hold fourteen hand-written rows, four of which only fired a "Command ran" toast because they carried no handler, and three subtitles with numbers typed once ("14 entries", "3 installed · 2 running", "5 sessions"). Session rows come from the real list, counts from `ledger.total` and `mcp.servers`, `Switch session` is gone because the sessions group already lists the same rows, and `Model & agents` opens the right dock's LLM tab — `ModelPanel.vue` was a hardcoded prototype (claude-sonnet/opus/haiku, two invented agents, an Apply button that only fired a toast) while `LlmPane.vue` already renders the real provider stack.
- The Files pane no longer opens on another project's files. It seeded four changes with invented `+/-` counts and two whitelist paths from the author's machine, and a session that edited nothing kept showing them; both are gone now, and `files.state` separates an untouched session from a refused read. The whitelist sheet gained Edit and Remove, an `invalid entries` section for paths the gateway refuses to save, and a reason for every refusal (blank, duplicate, not connected, five-root ceiling) — the old Add silently returned false for all four.

## 1.1.4

- Skill media is trimmed, not the skills. The huashu-design examples that made the package heavy are gone: six background music tracks and 24 pre-rendered showcase screenshots. All 24 showcase HTML files stay, so the style gallery still works, and the skill's instructions now open those HTML files instead of pointing at images that no longer ship. Every skill still loads and behaves exactly as before, the install is simply much smaller.
- `read_pdf` is removed. A coding agent has no use for it, and the engine behind it (PyMuPDF) would have cost roughly 66 MB, none of which ever reached the shipped build. Users who need PDF content have better tools.
- Uploading a PDF is unaffected. A PDF dropped into the conversation is still read through `pypdf`, a separate library; it now appears in `requirements-optional.txt`, where it had been missing all along.
- Also out of the bundle: numpy and sounddevice (27.8 MB, both unreachable once OCR was removed) and the project's own knowledge base (0.31 MB). With the trimmed skill media the sidecar loses about 57 MB uncompressed.
- `grep` now carries its own ripgrep. The tool shells out to `rg`, and the packaged app depended on the user having it on PATH, which almost nobody does; `vendor/ripgrep/rg.exe` (4 MB) ships in the bundle and `_resolve_rg()` only falls back to PATH when that copy is missing.
- `glob` was broken for any pattern carrying a directory. It compared `fnmatch` against the bare file name, so `knowledgesource/*.md` returned nothing; matching is now segment by segment, `*` staying inside one segment and `**` spanning any depth, while `*.py` still searches every depth as before.

## 1.1.3

- The bundled skill set drops from 120 to 50. Kept are the skills the agent's own instructions depend on and the ones most users actually reach for: the CKB workflow, the design and OfficeCLI document chain, and the HyperFrames video pipeline. The rest were written for the author's own workflows and no longer ship.
- Anything missing installs on demand: ask the agent to add the skill you want and it writes `~/.pebble/skills/<name>/SKILL.md`, which loads like a bundled one and shadows a bundled skill of the same name.

## 1.1.2

- Compaction no longer folds an empty summary. When the model answers with an empty body, the retry's answer is kept instead of being discarded, and a summary that carries headings but no real content is rejected rather than stored as a successful compaction.
- Compaction gained a fallback chain: model, one strict retry, a reformat of whatever came back, then a rule-based summary built from the newest user turns. If none of them carries usable text the operation reports failure and writes nothing, so the session stays intact and can be retried.
- Headings are fixed English (`Task / Status / Decisions / Todo / Clues`) while the section bodies follow the conversation's language, decided in code from the user messages rather than left to the model.
- Tool results now reach the summary at all. The gateway forwarded only the call arguments and dropped the returned output, so tool-heavy sessions summarised into nothing; arguments and results are both forwarded now, along with the names needed to tell a read from a shell run.
- Tool rows are graded by family: reads are cut to 200 characters, edits to 400, while commands and every failed call keep their full output, because a stack trace exists nowhere else.
- Compaction keeps one whole turn instead of the newest N messages: from the last user message through every reply and tool call it produced, capped at twenty rows. On a tool-heavy turn the old rule kept two shell echoes.
- Summary budgets are much larger: input 700K tokens, output 250K. A three-hundred-thousand-token session that used to be trimmed to thirty now goes in whole.
- Folded messages are backed up to the project's history file. The session id and project path were previously dropped, so no backup was ever written.
- `web_search` uses DuckDuckGo's html results and falls back to Bing pinned to the US market, replacing the localized Bing endpoint. Google is not in the chain: its static endpoint answers a JavaScript redirect shell rather than results. The tool now asks for English keywords, since localized sources return markedly weaker results for technical queries.

## 1.1.1

- Session switcher: the five seeded prototype conversations are gone. The list starts empty and the gateway's `session.list` answer replaces it, so a fresh project no longer shows clickable sessions that do not exist.
- A failed `session.list` and an empty one are now handled differently: a failure leaves the panel untouched, while an empty answer clears the rows before a new session is created.
- An empty panel says which empty it is: still loading, could not read, or no sessions yet.
- Deleting a session asks for confirmation in the shared sheet. A refused delete puts the row back and shows the gateway's reason, and an offline delete is refused instead of reported as success.
- The row's More button opens the conversation menu only; the leftover prototype toast that fired alongside it is gone.
- Search filters through Vue state instead of inline `display`, so it survives a list re-render and is cleared when the project changes.
- Export falls back to the gateway's markdown when it cannot write its own file, instead of downloading a header-only stub.
- A reconnect re-reads the session list instead of trusting the one loaded at startup.
- Experience ledger: the five seeded demo entries and the hardcoded total are gone. The list starts empty, `memory.list` replaces it, and the panel says loading, could not read or no entries yet instead of rendering all three as an empty list.
- The detail pane edits now: the statement is a textarea, confidence is a slider, and Save works on a draft. It reports success or the gateway's refusal, and Delete asks for confirmation first.
- Agent notes: switching agent, scope or tab asks before discarding unsaved text. A slow read can no longer land on the wrong agent, and a failed read keeps the draft and says so instead of looking like an empty note.
- Clear asks for confirmation, and Save/Clear report success or refusal instead of leaving the button silent.
- An agent that disappears from the list is no longer left selected, and Save/Clear stay disabled until an agent is chosen.
- New entry reports a refused save, and the footer shows what is on screen versus what is stored instead of a hardcoded "synced".
- Usage pane: saving the pricing table reports the gateway's own verdict. A rejected table used to read "saved" while the file on disk was untouched; the reason now stays on screen until the next attempt.
- A period with no rates yet starts every cell at zero, so editing a single price on a freshly opened peak or offpeak tab can actually be saved.
- Today / 7 days / 30 days keep updating when the current session cannot be read, instead of freezing on their last numbers.
- Model names are editable in the table and a duplicate is refused; removing a stored model asks first; unsaved edits are marked and Save is disabled until something changes.
- A pricing table that finishes loading while the editor holds unsaved work asks whether to use the stored table or keep the edits.

## 1.1.0

- Project panel: the file tree now lists the active project instead of the app's install folder, and says whether it is loading, empty or refused instead of drawing a blank list.
- `projects.json` is written atomically with a `.bak` copy. An unreadable registry is kept as `projects.json.corrupt` and reported in the panel rather than shown as "no projects"; adding or removing a project is refused while it is corrupt.
- Empty paths no longer resolve to the sidecar's working directory, so adding a project, adopting sessions or starting a session can no longer point the tools at the install folder.
- Switching projects is refused while a reply is running, including a turn started by another client, and a failed switch rolls the active project back instead of reporting success.
- The panel has a single owner again: the leftover prototype handlers that toasted "switched" without calling the gateway are gone, the popover closes after a pick, and refused add/remove/switch operations say so.
- Adding a project opens the path the gateway actually stored, removing one asks first, and adopt reports how many sessions moved.
- Sessions stored under an older spelling of the project path (separators, trailing slash, case) are visible again instead of vanishing from the list.
- One shared confirmation sheet replaces `window.confirm` for provider deletion.
- Removed the dead `Manage` button in the project panel; the session list's `Manage` now reads `Projects`.

## 1.0.9

- Fixed the add-provider crash loop; a failing RPC now returns an error without closing the WebSocket.
- Fixed provider/model routing for duplicate model ids, per-agent bindings, and draft providers; drafts activate only with a base URL and at least one model.
- Deleting a provider or its last model now repairs the agent model instead of sending a stale name.
- Invalid provider operations now fail; base URLs and request-shaping fields are normalised and validated.
- Provider and agent configs are written atomically with a `.bak` copy; a corrupt user config no longer falls back to bundled sample providers.
- LLM pane: draft/active/no-key states, per-provider settings, Enter/blur model entry, auto-activation, delete confirmation, and dirty-edit preservation.
- Unknown provider fields and agent settings survive saves; agent selection persists; the reply model label follows the actual model.

## 1.0.8

- Fixed the update sheet opening above the window. It was mounted inside the title bar, and the bar's blur effect made the overlay size itself against that 78px strip instead of the window, so the notes were cut off at the top and could not be read.
- Carries what 1.0.7 shipped, since that release's own notes were unreadable because of the bug above: the LLM pane can add and switch providers from the panel (the provider grid ends with a `new provider` card, and clicking a card makes it the active endpoint; before this, adding one meant editing `llm_providers.json` by hand), and the pairing sheet is marked unavailable instead of showing a QR code and a code button for a mobile companion that is not part of this build.

## 1.0.7

- The LLM pane can add and switch providers from the panel now. The provider grid ends with a `new provider` card, and clicking a card makes it the active endpoint. Before this, adding one meant editing `llm_providers.json` by hand, and clicking a card only changed its outline.
- The pairing sheet is marked unavailable. It still showed a QR code and a code button for a mobile companion that is not part of this build; the sheet now carries a notice and the `New code` button is gone.

## 1.0.6
- Optimize the experience and make the titles in the side menu bar more readable. The "max toks" option that was not useful in the LLM menu has been removed. The new update notification function has been added. After clicking the "Update" button later, an update notice will appear, which will help you understand the situation.

## 1.0.5

- User skills: drop a `SKILL.md` into `~/.pebble/skills` and it shows up next to
  the built-in ones. A user skill with the same name overrides the built-in copy,
  so you can take one apart, adjust it, and keep it across updates.
- Custom MCP servers: the agent can write `~/.pebble/.mcp.json`. Every entry
  waits for your approval in the MCP panel before it is launched, and editing an
  entry sends it back to waiting so the approved text is always the text that
  runs. Removing an entry disconnects it straight away instead of leaving the
  process on the rack until the next restart.
- MCP tools whose names contain a hyphen (`get-sum`) now dispatch correctly.
  Before this, only the hyphen-free tools of a server could be called.
- Section headings in the right dock are larger and drawn in the primary ink, so
  they read as headings instead of disabled text.
- Removed the `max tok` slider. It never reached the API, so answer length is
  unchanged: the provider's own default applies, which is what was happening
  already.
- Token shares in Usage show two decimals instead of rounding to a whole percent.
- The version badge in the top left follows the build version instead of a
  hardcoded label.

