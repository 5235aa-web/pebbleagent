# 6. Tool System

### tools/ (18 files)

| File | Function | Tag |
|------|------|------|
| base_tool.py | Unified tool base class + ToolRegistry | 🔴Core |
| tool_registry.py | Tool registry (singleton) | 🔴Core |
| file_tools.py | File operation tool set | 🔴Core |
| todo_tool.py | Todo list management | 🔴Core |
| ask_question_tool.py | User question tool | 🟡Aux |
| execute_code_tool.py | Code execution tool | 🟡Aux |
| powershell_tool.py | PowerShell tool (**danger_level raised to "high" since 2026-10-16**) | 🟡Aux |
| grep_tool.py | Code search tool | 🟡Aux |
| glob_tool.py | File matching tool | 🟡Aux |
| web_fetch_tool.py | HTTP request tool | 🟡Aux |
| brief_tool.py | Brief reply tool | 🟡Aux |
| plan_mode_tool.py | Plan mode tool | 🟡Aux |
| skill_tools.py | Skill tools | 🟡Aux |
| notebook_edit_tool.py | Notebook editing | 🟡Aux |
| tool_search_tool.py | Tool search | 🟡Aux |
| test_tools.py | Test tools | 🧪Test |
| code_network_guard.py | Network guard (V1) | 🟡Aux |
| __init__.py | Module init | 📦Config |

### agent_core/mcp/ (5 files)

| File | Function | Key symbols | Tag |
|------|------|---------|------|
| mcp_manager.py | MCP connection management | McpManager | 🔴Core |
| mcp_client.py | MCP stdio client | StdioTransport | 🔴Core |
| mcp_config.py | MCP config management | McpConfigManager | 🟡Aux |
| mcp_integration.py | MCP tool registration | register_mcp_tools() | 🔴Core |
| mcp_types.py | MCP type definitions | - | 🧪Types |

### Extension Tools (extensions/tools/)

| File | Function | Convention |
|------|------|------|
| read_structure.py | Read the project's Structure.md | Exposes tool_name, tool_description, tool_parameters, execute |

Custom tools just need to be placed under `extensions/tools/` exposing those 4 attributes; `extensions_loader.py` discovers and registers them automatically.

### Tool Execution Security & Validation Chain (infrastructure under agent_core/, file index in Chapter 5)

| Stage | Module | Mechanism |
|------|------|------|
| Shell command validation | bash_validator.py (19.5KB) | Six-layer validator: read_only (read-only → concurrency-safe) / destructive (blocks destructive ops) / mode / sed -i / path out-of-bounds / semantic intent; three integration points — tool_executor_impl._tool_execute_shell (before execution), permission_enforcer (dangerous commands), tool_concurrency (read-only concurrency decision) |
| Code network behavior | code_network_guardV2.py | Static scan (crawler frameworks / HTTP-library imports / loop-pagination patterns / dynamic-import bypass) + three runtime guards: RateLimiter (≤6 req/min per domain), PathDepthGuard (≤3 navigations per turn), DownloadGuard (≤2 files / ≤50MB per turn); **since 2026-10-16 the powershell tool is scanned too (scan_code_for_scraping_behavior(command, "powershell")) — previously a full bypass of the guard** |
| LSP tool family (9) | lsp_client.py (65KB) | lsp_go_to_definition / find_references / hover / document_symbol / workspace_symbol / go_to_implementation / prepare_call_hierarchy / incoming_calls / outgoing_calls — schemas in tool_definitions.py, execution via tool_executor_impl → LspClient (auto-discovers pyright/jedi/pylsp) |
| Browser tools | tool_executor_impl (httpx) | fetch_url / web_search / browser_navigate / browser_download implemented directly via httpx (electron_bridge removed in the 2026-10 security batch) |
| Audit | gateway/permission_audit.py | All permission decisions persisted (permission_audit.db) + SHA256 checksum tamper protection v2, all-fields (see Chapter 4) |

### Notes

1. **Tool architecture — four layers**: BaseTool + ToolRegistry + Extensions Loader + MCP (MCP servers defined in the root .mcp.json, 6 of them incl. blender; dependency declarations and the frontend panel use mcp_install.json + the mcp.install_info RPC)
7. **Todo event path**: `TodoManager._event_callback` is only set inside the LLM stream. When an Agent calls `todo_write` itself, it falls back to `_ws_push_callback` (fixed 2026-07)
20. **brief tool policy (2026-09-25)**: ① code_agent_prompt.md explicitly forbids calling brief (brief renders inside a tool card — tiny font, no formatting — and is reserved for short user-facing notices, not body text; the ban appears both in the red-line rules section and the output-spec section); ② **mechanism facts (updated 2026-10-15)**: with enforce=false, agent YAML tool permissions inject tool schemas in full via get_tools_definition_with_mcp(); the `enforce` switch forces filtering (chat_agent enabled: non-whitelisted tool schemas are not uploaded + execution entry interception; see Chapter 5 note #10). code_agent has enforce off and full injection; the prompt ban remains its only soft constraint; ③ for a "hard ban" (model cannot see brief at all): set `enforce: true` + `disallowed: [brief]` in that agent's yaml (or omit brief from the whitelist) (requires backend restart); physical-removal path (7 places): tools/brief_tool.py (delete file), tools/__init__.py (import+__all__), tools/tool_registry.py (_STATIC_TOOL_MODULES), tool_definitions.py (schema block), tool_concurrency.py (WRITE_TOOLS), tool_executor_impl.py (dispatch+_tool_brief implementation), tool_permission.py (permission table); ④ prompt changes take effect after backend restart (agent cache is process-level, no mtime invalidation)
