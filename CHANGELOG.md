# Changelog

Every release gets a section here. `frontend/scripts/publish.mjs` copies the
section whose heading matches `version` in `frontend/src-tauri/tauri.conf.json`
into the GitHub release body **and** into the `notes` field of `latest.json`,
which is what the in-app update sheet shows before installing. Add the section
before running `npm run release`; the release stops if it is missing, because a
feed with empty notes tells the user nothing.

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

