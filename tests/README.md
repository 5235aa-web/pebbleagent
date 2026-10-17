# Tests

Standalone verification tests for PebbleAgent. No test framework required: every
`test_*.py` / `test_*.mjs` file runs on its own and exits `0` on pass, non-zero on fail.

## Running

```bash
# All Python tests (each in its own process)
python tests/run_all.py

# All JS tests
node tests/run_all.mjs

# A single test
python tests/test_agent_tool_policy.py
node tests/test_fold_algo.mjs

# Static analysis gate (rules F + E9, configured in ruff.toml)
ruff check agent_core gateway tools tests
```

The repository also has three billing/holiday regression scripts at the project root
(`peb_check_*.py`) which follow the same exit-code convention.

## Test index

### Tool policy (chat_agent 4-tool allowlist)
| Test | Covers |
| --- | --- |
| `test_agent_tool_policy.py` | YAML load, allowlist filter, policy check |
| `test_agent_tool_policy_executor.py` | executor entry-point interception (denies before any execution) |
| `test_agent_tool_policy_schema.py` | `_get_tools_definition` applies the filter end-to-end |

### Compaction & billing
| Test | Covers |
| --- | --- |
| `test_context_compaction_keep.py` | dynamic keep-count, tool-pair protection, boundary/summary structure |
| `test_pricing_save.py` | `pricing.save` validation, atomic write, hot reload, unknown-field preservation |
| `test_pricing_labels.mjs` | pricing panel column labels (source checks) |

### Frontend rendering / UI
| Test | Covers |
| --- | --- |
| `test_render_perf.mjs` | stream flush throttle, v-memo revert guard, line render cache |
| `test_ws_recovery.mjs` | WS disconnect recovery (source checks + logic simulation) |
| `test_jump_collapse.mjs` | jump-to-compacted-message lands on fold divider |
| `test_fold_algo.mjs` | fold algorithm **executed from real `SegmentedMessageList.vue` source** |
| `test_input_resize.mjs` | input-box drag clamp formulas + source consistency |
| `test_time_groups.mjs` | conversation time bucketing **executed from real `Sidebar.vue` source** |

### Core mechanisms
| Test | Covers |
| --- | --- |
| `test_thinking_params.py` | thinking toggle/effort (`build_thinking_params`, `_bool`, server expressions) |
| `test_clarify_dual.py` | `clarify` dual-channel answers + guards |
| `test_log_persistence.py` | log setup idempotency + suspicious stream-end detection |

### Chat loop
| Test | Covers |
| --- | --- |
| `test_chat_stream_e2e.py` | real `chat_stream` loop against a fake client: single turn, tool round, cancel, suspicious retry, usage accounting |
| `test_gateway_facade.py` | every `srv.<name>` used by rpc handlers exists in `gateway/server.py` |

### Storage, guards & protocols
| Test | Covers |
| --- | --- |
| `test_transcript_manager.py` | transcript write/read/search/delete under a temporary `PEBBLE_HOME` |
| `test_guard_scope.py` | per-session rate/navigation/download counters, scoped reset, asyncio task isolation |
| `test_permission_e2e.py` | rule parsing, allow/deny/ask, manual/auto/readonly/locked, persisted mode |
| `test_mcp_registry.py` | MCP tool naming, name parsing, lookup, state shape, executor factory resolution |
| `test_lsp_framing.py` | LSP Content-Length framing, invalid length/JSON, closed stream |
| `test_grep_tool_async.py` | GrepTool async subprocess path (asyncio import, real search) |
| `test_tool_registry_autoregister.py` | module scan registration (BaseTool subclasses, SimpleTool excluded) |
| `test_revert_batch.py` | `restore_batch` conflict policy (abort / skip_conflicts) |
| `test_revert_single.py` | single-file revert: exact path matching, failure rollback, remaining count |

## Design conventions

- **Exit codes, not frameworks.** Each test collects `[PASS]/[FAIL]` lines and exits non-zero
  if anything failed. CI only needs the exit code.
- **Real source, not copies.** Where possible, tests extract the actual function bodies
  (`new Function` / AST) from the shipped source files, so algorithm drift in the app
  fails the test loudly instead of silently passing a stale copy.
- **Isolation.** Tests that touch storage set a temporary `PEBBLE_HOME`; tests that go
  through the permission chain use a temporary rules file (mode changes persist there,
  never in the project config). No production data is touched.
- **No network.** All tests run offline.
