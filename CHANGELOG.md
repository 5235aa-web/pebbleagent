# Changelog

Every release gets a section here. `frontend/scripts/publish.mjs` copies the
section whose heading matches `version` in `frontend/src-tauri/tauri.conf.json`
into the GitHub release body **and** into the `notes` field of `latest.json`,
which is what the in-app update sheet shows before installing. Add the section
before running `npm run release`; the release stops if it is missing, because a
feed with empty notes tells the user nothing.

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

