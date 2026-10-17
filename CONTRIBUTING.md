# Contributing

Thanks for considering a contribution. This document covers the development setup, the checks a pull request must pass, and the conventions the codebase follows.

## Before you start

- Bug reports and feature requests: open an issue with the version, the steps to reproduce, and the expected behavior.
- Security issues: contact the maintainer directly (see the repository profile) instead of opening a public issue.

## Requirements

- Windows 10/11 (64-bit) is the only supported platform for now
- Python 3.11+ (the development environment uses 3.14)
- Node.js 18+ for the frontend and npx-based MCP servers (CI uses 22)
- Rust toolchain only if you build the Tauri desktop shell

## Setup

```bash
pip install -r requirements.txt
cd ui-gui-vue3 && npm install
```

The backend writes runtime data to `%USERPROFILE%\.pebble\`. Set `PEBBLE_HOME` to redirect it, which is also how the tests isolate storage.

## Running

```bash
python run_backend.py          # backend (WebSocket gateway)
cd ui-gui-vue3 && npm run dev  # frontend on http://localhost:5174
```

`start_backend.bat` and `start_gui.bat` are convenience wrappers for the same commands.

## Checks before a pull request

Run these from the repository root. CI runs the same set.

```bash
python -m compileall -q agent_core gateway tools tests run_backend.py skills_loader.py peb_check_holiday.py peb_check_holiday_env.py peb_check_pricing_scope.py
ruff check agent_core gateway tools tests
python tests/run_all.py
node tests/run_all.mjs
cd ui-gui-vue3 && npx vue-tsc --noEmit
```

`ruff` is configured in `ruff.toml` and runs only the `F` and `E9` rule families: undefined names, unused imports and variables, redefinitions, empty f-strings, and syntax errors. Style rules are intentionally off so the gate stays high-signal. Do not add blanket `# noqa` comments; if a name is genuinely used through a dynamic path, add a narrow `# noqa: F401` with a one-line reason.

CI runs the Python checks on 3.11 and 3.14, and the frontend checks once.

## Tests

Tests are standalone: each `tests/test_*.py` and `tests/test_*.mjs` file runs on its own and exits `0` on success. No test framework is required.

- Put new behavior behind a test in `tests/`. A bug fix without a regression test will usually be asked for one.
- Tests must be offline. Never call a real model, a real MCP server, or a real network endpoint.
- Tests that touch storage must set a temporary `PEBBLE_HOME`; tests that touch permissions must use a temporary rules file.
- Prefer extracting the real source over copying logic into the test, so drift fails the test instead of passing a stale copy. See `tests/README.md` for the existing patterns.

## Code conventions

- Comments explain why, not what. Do not leave change logs, incident notes, or dated remarks in comments.
- Keep functions focused. If a function needs more than a screenful of scrolling to follow, consider extracting a named step.
- Do not add new module-level mutable state without a scope or reset story; the network guards and the gateway facade are the existing examples.
- Keep the public RPC surface and the WebSocket event protocol backward compatible. Add fields instead of renaming them.

## Pull requests

- One concern per pull request. Refactors and behavior changes belong in separate PRs.
- Describe the motivation, the behavior change, and how you verified it.
- Link the issue when one exists.
- CI must be green. The maintainers may ask for a smaller diff or a test before merging.

## License

Contributions are accepted under the GNU Affero General Public License v3.0. See [LICENSE](LICENSE).
