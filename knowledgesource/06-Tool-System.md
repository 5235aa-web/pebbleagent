# 6. Tool System

### agent_core/tool_impl/ (9 files — tool handlers by domain)

The executor is a thin pipeline (policy → permission → snapshot → dispatch → write-result backfill); handlers live in per-domain modules and are registered in one table.

| File | Function | Tag |
|------|------|------|
| registry.py | Declarative dispatch table (47 tool names → handler) | 🔴Core |
| filesystem.py | Read/write/edit/search/glob/ls/grep/directory tools | 🔴Core |
| execution.py | Shell, PowerShell, inline code, subprocess lifecycle | 🔴Core |
| web.py | Fetch, search, browser navigate/download, network guards | 🔴Core |
| documents.py | PDF reading + OCR | 🟡Aux |
| system.py | Env vars, system info, processes, git status/log/diff | 🟡Aux |
| workspace.py | Todo, notes, questions, skills, experience ledger writes and updates (experience_write / experience_update) | 🟡Aux |
| lsp.py | Language-server code intelligence (9 tools + formatters) | 🟡Aux |
| mcp_bridge.py | MCP tool routing (executor factory + ToolExecutionContext imported; unconnected server returns an error string) | 🟡Aux |

`agent_core/tool_executor_impl.py` is the facade: mixin composition + the execution pipeline.

### agent_core/state/ (3 files — cross-turn runtime state)

| File | Function | Tag |
|------|------|------|
| todo.py | TodoManager (per-session todo list + WebSocket push callback) | 🔴Core |
| question.py | QuestionManager (pending clarify questions + answer event) | 🔴Core |
| __init__.py | Package exports | 📦Config |

These two managers are the runtime state behind the `todo_write` / `todo_read` and `clarify` tools. The former `tools/` package (BaseTool classes + ToolRegistry) has been removed: tool schemas live in `agent_core/tool_definitions.py`, dispatch in `agent_core/tool_impl/registry.py`, and implementations in the `tool_impl/` mixins.


### agent_core/mcp/ (5 files)

| File | Function | Key symbols | Tag |
|------|------|---------|------|
| mcp_manager.py | MCP connection management (per-server connect / disconnect via `set_server_enabled_state` + `toggle_server`, per-server toggle lock, state-change callbacks) | McpManager | 🔴Core |
| mcp_client.py | MCP stdio client (**placeholder commands (REPLACE_WITH_...) rejected with a clear error, **) | StdioTransport | 🔴Core |
| mcp_config.py | MCP config management (three-layer merge; disabled list is global at `~/.pebble/disabled_mcp_servers.json`, the legacy per-project file is read as fallback) | McpConfigManager | 🟡Aux |
| mcp_integration.py | MCP tool registration and execution (missing server returns an error string) | register_mcp_tools, McpToolExecutorFactory | 🔴Core |
| mcp_types.py | MCP types: stdio / sse / http / ws configs, connection states, tool and resource records | McpServerConfig, ScopedMcpServerConfig | 🧪Types |

### Extension Tools (extensions/tools/)

| File | Function | Convention |
|------|------|------|
| read_structure.py | Read the project's Structure.md | Exposes tool_name, tool_description, tool_parameters, execute |

Custom tools just need to be placed under `extensions/tools/` exposing those 4 attributes; `extensions_loader.py` discovers and registers them automatically.

### Tool Execution Security & Validation Chain (infrastructure under agent_core/, file index in Chapter 5)

| Stage | Module | Mechanism |
|------|------|------|
| Shell command validation | bash_validator.py (19.5KB) | Six-layer validator: read_only (read-only → concurrency-safe) / destructive (blocks destructive ops) / mode / sed -i / path out-of-bounds / semantic intent; three integration points — tool_executor_impl._tool_execute_shell (before execution), permission_enforcer (dangerous commands), tool_concurrency (read-only concurrency decision); also applied to execute_code when language="bash" |
| PowerShell command validation | powershell_validator.py | PowerShellValidator: system-destructive block patterns (Format-Volume / Clear-Disk / Initialize-Disk / diskpart / bcdedit / cipher /w / mkfs / vssadmin-delete / shutdown-restart / format X: / dd) → blocked; path extraction ($env: vars expanded, ~ resolved, drive roots / project-outside paths) → blocked; dangerous-op warnings (Remove-Item / registry / policy / IEX etc.); wired into tool_executor_impl._tool_powershell |
| Code destructive scan | code_destructive_guard.py | scan_code_for_destructive_behavior: high-confidence literal patterns (rm -rf /, del /s <drive root>, format C:, mkfs, dd of=/dev/, shutil.rmtree root, PS disk commands) → blocked; wired into tool_executor_impl._tool_execute_code (all languages) |
| Per-tool runtime limits | little_guard.py | Three independent limiters, one per outbound tool: RateLimiter (≤6 req/min per domain; applied to web_search and browser_navigate), PathDepthGuard (≤3 navigations per turn + pagination patterns; browser_navigate), DownloadGuard (extension allowlist, ≤2 files / ≤50MB per turn; browser_download); counters are scoped per conversation (contextvar set in AgentToolExecutorImpl.execute) and reset per turn for that session only. No static crawler scanning: execute_code and powershell are not content-scanned, and fetch_url / MCP tools bypass these limiters |
| LSP tool family (9) | lsp_client.py (65KB) | lsp_go_to_definition / find_references / hover / document_symbol / workspace_symbol / go_to_implementation / prepare_call_hierarchy / incoming_calls / outgoing_calls — schemas in tool_definitions.py, execution via tool_executor_impl → LspClient (auto-discovers pyright/jedi/pylsp) |
| Browser tools | tool_executor_impl (httpx) | fetch_url / web_search / browser_navigate / browser_download implemented directly via httpx; browser_download filenames are sanitized (_sanitize_download_filename: rejects .. / absolute paths / drive-colon / control chars / Windows reserved names, keeps safe subdirs; PathBoundaryChecker realpath/commonpath check as backstop) |
| Audit | gateway/permission_audit.py | All permission decisions persisted (permission_audit.db) + chained SHA256 checksum v3 + write-time secret masking (see Chapter 4) |

**Design note — heuristic validation layer**: the shell / PowerShell / code / network guards are pattern-based by design. They are a fast, dependency-free first line of defence that covers the realistic command surface of day-to-day production use, adds no execution latency, and keeps every rule readable and auditable. They are not intended as a formal sandbox or AST-level taint analysis: the permission layer (ask / allow / deny rules + path whitelist) remains the authoritative boundary, and these guards sit on top of it as defence in depth. Matches are surfaced as explicit blocks with the offending pattern, so rules are easy to review and tune.

### Notes

1. **Tool architecture — three layers**: declarative dispatch (`tool_definitions.py` schemas + `tool_impl/registry.py` handlers + `tool_impl/` mixins) + Extensions Loader + MCP (MCP servers defined in the root .mcp.json, 6 of them incl. blender; dependency declarations and the frontend panel use mcp_install.json + the mcp.install_info RPC)
7. **Todo event path**: `TodoManager._event_callback` is only set inside the LLM stream. When an Agent calls `todo_write` itself, it falls back to `_ws_push_callback`
8. **MCP connect control**: the McpPanel switch calls `mcp.toggle` → `McpManager.set_server_enabled_state` (idempotent, non-blocking submit onto the WS loop). Disabled servers are excluded from `get_enabled_configs()` at init, so toggle recovers them from `get_all_configs()` before connecting. Disabling cancels the connection-monitor task (otherwise it reports a false "Connection lost" one poll later) and tolerates a failing `close()` without skipping the state switch. `mcp.list` reports `enabled` / `state` from the merged config (not the raw file) and hides unusable placeholder entries unless `mcp_install.json` declares them installable. State changes are pushed to the frontend as `mcp.state_changed`
