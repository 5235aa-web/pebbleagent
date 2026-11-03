# Changelog

Every release gets a section here. `frontend/scripts/publish.mjs` copies the
section whose heading matches `version` in `frontend/src-tauri/tauri.conf.json`
into the GitHub release body **and** into the `notes` field of `latest.json`,
which is what the in-app update sheet shows before installing. Add the section
before running `npm run release`; the release stops if it is missing, because a
feed with empty notes tells the user nothing.

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

