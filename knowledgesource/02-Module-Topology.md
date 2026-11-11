# 2. Module Topology

### Call Layers (bottom-up)

```
[Config]      llm_providers.json, agent_model_config.json, agents/*.yaml
                 ↑ read
[Data]        gateway/session.py (sessions.db), agent_core/agent_chat_history.py (agent_chat.db)
              agent_core/transcript_manager.py (JSONL transcript archive), gateway/permission_audit.py (audit DB)
                 ↑ read/write
[Tools]       agent_core/tool_definitions.py (45 schemas) + agent_core/tool_impl/ (dispatch + handlers)
              agent_core/extensions/, agent_core/mcp/, agent_core/state/ (todo + question managers)
              Tool infrastructure: bash_validator (6-layer command validation) / little_guard (3 runtime limiters, per-session counters)
              / lsp_client (backend of 9 LSP tools)
                 ↑ call
[Services]    agent_core/agent_chat_service_v2.py (🔴core 74KB, 1588 lines)
              agent_core/agent_loader.py, agent_core/context_compressor.py
              agent_core/tool_orchestrator.py, agent_core/tool_executor_impl.py
              agent_core/query_state_machine.py, agent_core/memory/, agent_core/snapshot/
                 ↑ call
[Gateway]     gateway/server.py (entry: prompt pipeline + RPC dispatch), gateway/rpc/ (domain handlers)
              gateway/ws_server.py, gateway/config.py
              gateway/log_setup.py (unified logging), permission_manager.py, permission_service.py, permission_audit.py
                 ↑ call
[Frontend]    frontend/src/data/gateway.ts (WebSocket), wire.ts (events -> store)
              frontend/src/components/ (Vue components), frontend/src/data/blocks.ts (stream blocks)
                 ↑ hosted by
[Desktop Shell] frontend/src-tauri/ (Tauri 2 Rust, crate pebble-orbit-shell): frameless window + sidecar launch/exit cleanup of pebble-backend.exe
```

### Module Dependency Table

| Module | Layer | Callers | Callees |
|------|-----|--------|---------|
| ws_server.py | Gateway | - | server.py |
| server.py | Gateway | ws_server.py, entry.py | agent_chat_service_v2, session, config, rpc/ |
| rpc/ | Gateway | server.py | agent_chat_service_v2, session, memory, mcp |
| session.py | Data (sessions.db: session CRUD + project_path multi-project ownership + compaction flags) | create(project_path), list(limit, project_path) (incl. last_message_at = latest message time), delete, delete_message, adopt_unassigned, unassigned_count | server.py | sessions.db | 📦Data |
| agent_chat_history.py | Data | agent_chat_service_v2 | agent_chat.db |
| **agent_chat_service_v2.py** | 🔴Core | server.py → chat_stream | OpenAI SDK, tool_orchestrator, agent_chat_history, agent_loader, context_compressor, memory |
| agent_loader.py | Service | agent_chat_service_v2 | agents/*.yaml + *_prompt.md |
| context_compressor.py | Service | agent_chat_service_v2, server.py | - |
| tool_orchestrator.py | Service | agent_chat_service_v2 | tool_executor_impl |
| tool_executor_impl.py | Service | tool_orchestrator | tool_impl/ |
| tool_definitions.py | Tools | agent_chat_service_v2 | extensions_loader, MCP |
| extensions_loader.py | Tools | tool_definitions | extensions/tools/*.py |
| config.py | Gateway | server.py | llm_providers.json |
| gateway.ts | Frontend | - | WebSocket |
| config.ts | Frontend | gateway.ts | server.py (via WebSocket) |
| transcript_manager.py | Data (JSONL transcript archive ~/.pebble/projects/) | context_compressor, agent_chat_service_v2 | transcript files |
| permission_audit.py | Data (audit permission_audit.db) | permission_enforcer, tool_executor_impl | permission_audit.db |
| bash_validator.py | Tool infra (6-layer command validation) | tool_executor_impl, permission_enforcer, tool_concurrency | - |
| lsp_client.py | Tool infra (LSP client) | tool_executor_impl | LSP server subprocess |
| little_guard.py | Tool infra (runtime limiters for web_search / fetch_url / browser_download; counters scoped per session via contextvar) | agent_chat_service_v2, tool_executor_impl | - |
| frontend/src-tauri/src/lib.rs | Desktop shell (Tauri 2) | - | pebble-backend.exe (sidecar launch/cleanup) |

## Test suite (`tests/`)

Run with `python tests/run_all.py` (one process per file, exit 0 only if all pass). The JS runner `tests/run_all.mjs` holds no `test_*.mjs` files today and passes with zero.

| File | Purpose | Tag |
|------|---------|-----|
| tests/run_all.py | Runs every `test_*.py` in its own process; exit 0 only when all pass | 🧪Test |
| tests/run_all.mjs | JS runner; currently holds no `test_*.mjs` and passes with zero | 🧪Test |
| tests/ts-hook.mjs | Node loader hook that lets the runner import TypeScript | 🧪Test |
| tests/ts-register.mjs | Registers the TS hook before the JS runner starts | 🧪Test |
| tests/test_agent_tool_policy.py | Agent tool policy: chat_agent allowlist (YAML load, filter, policy check) | 🧪Test |
| tests/test_agent_tool_policy_executor.py | Agent tool policy: executor entry-point interception | 🧪Test |
| tests/test_agent_tool_policy_schema.py | Agent tool policy: `_get_tools_definition` applies the agent filter | 🧪Test |
| tests/test_audit_hardening.py | Permission audit hardening | 🧪Test |
| tests/test_browser_download_sanitize.py | `browser_download` filename sanitisation | 🧪Test |
| tests/test_chat_stream_e2e.py | chat_stream integration: single turn, tool round, cancel, suspicious retry, usage | 🧪Test |
| tests/test_clarify_dual.py | clarify multi-question dual-channel submission | 🧪Test |
| tests/test_code_agent_prompt_contract.py | Prompt/YAML contract for code_agent | 🧪Test |
| tests/test_command_validation.py | Command and code validation parity | 🧪Test |
| tests/test_compaction_budget.py | Compaction budgeting: window, calibration, splitting, no rule fallback | 🧪Test |
| tests/test_compaction_marker_keep.py | Compaction markers: the newest messages stay in the LLM context | 🧪Test |
| tests/test_context_compaction_keep.py | Context compaction: dynamic keep-count, tool-pair integrity | 🧪Test |
| tests/test_experience_ledger.py | Experience ledger: write, recall, supersede, signals, notes, migration | 🧪Test |
| tests/test_gateway_facade.py | Gateway facade: every `srv.<name>` used by rpc handlers exists | 🧪Test |
| tests/test_guard_scope.py | Guard scope isolation | 🧪Test |
| tests/test_interrupted_reply.py | Interrupted-reply persistence at the gateway layer | 🧪Test |
| tests/test_log_persistence.py | Log persistence plus suspicious stream-end detection | 🧪Test |
| tests/test_lsp_framing.py | LSP message framing: Content-Length headers, JSON body, error paths | 🧪Test |
| tests/test_mcp_registry.py | MCP manager registry: tool naming, lookup, state shape | 🧪Test |
| tests/test_minor_hardening.py | Minor hardening batch | 🧪Test |
| tests/test_orchestration_cleanup.py | Orchestration-layer cleanup | 🧪Test |
| tests/test_permission_e2e.py | Permission chain end to end: rule parsing, modes, allow/deny/ask | 🧪Test |
| tests/test_permission_mapping.py | Permission mapping unification | 🧪Test |
| tests/test_pricing_save.py | `pricing.save` end to end against the real gateway.server module | 🧪Test |
| tests/test_project_panel_rpcs.py | Project panel RPCs: tree root, corrupt passthrough, add identity, visibility | 🧪Test |
| tests/test_project_registry.py | Project registry: normalisation, atomic save, corruption recovery | 🧪Test |
| tests/test_provider_headers.py | Provider-specific headers and reasoning-mode shaping | 🧪Test |
| tests/test_provider_lifecycle.py | Provider/model lifecycle: active-provider coherence, deletion repair, atomic save | 🧪Test |
| tests/test_provider_ui.mjs | Provider panel UI behaviour | 🧪Test |
| tests/test_revert_batch.py | `RevertExecutor.restore_batch`: conflict policy for `session.file_revert_all` | 🧪Test |
| tests/test_revert_single.py | Single-file revert: exact path matching and failure rollback | 🧪Test |
| tests/test_rpc_smoke.py | RPC smoke tests: the four most-used endpoints plus the path guard | 🧪Test |
| tests/test_session_title.py | Session auto-title generation | 🧪Test |
| tests/test_skills_frontmatter.py | Skill frontmatter parsing | 🧪Test |
| tests/test_snapshot_external_modification.py | Snapshot revert: external-modification detection | 🧪Test |
| tests/test_stream_consumer.py | Stream consumer accumulation and usage capture | 🧪Test |
| tests/test_thinking_params.py | Thinking and reasoning parameter shaping per provider | 🧪Test |
| tests/test_tool_orchestrator_e2e.py | Tool orchestration end to end: partitioning, batches, concurrency | 🧪Test |
| tests/test_tool_registry.py | Tool registry and built-in tool definitions | 🧪Test |
| tests/test_transcript_manager.py | Transcript manager: segment writes and metadata re-append | 🧪Test |
