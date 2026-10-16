# 2. Module Topology

### Call Layers (bottom-up)

```
[Config]      llm_providers.json, agent_model_config.json, agents/*.yaml
                 ↑ read
[Data]        gateway/session.py (sessions.db), multi_agents/agent_chat_history.py (agent_chat.db)
              multi_agents/transcript_manager.py (JSONL transcript archive), gateway/permission_audit.py (audit DB)
                 ↑ read/write
[Tools]       tools/ (BaseTool + ToolRegistry), multi_agents/extensions/, multi_agents/mcp/
              Tool infrastructure: bash_validator (6-layer command validation) / code_network_guardV2 (3 network guards)
              / lsp_client (backend of 9 LSP tools) / electron_bridge (built-in browser HTTP bridge)
                 ↑ call
[Services]    multi_agents/agent_chat_service_v2.py (🔴core 117KB)
              multi_agents/agent_loader.py, multi_agents/context_compressor.py
              multi_agents/tool_orchestrator.py, multi_agents/tool_executor_impl.py
              multi_agents/query_state_machine.py, multi_agents/memory/, multi_agents/snapshot/
                 ↑ call
[Gateway]     gateway/server.py (JSON-RPC 90KB), gateway/ws_server.py, gateway/config.py
              gateway/log_setup.py (unified logging), permission_manager.py, permission_service.py, permission_audit.py
                 ↑ call
[Frontend]    ui-gui-vue3/src/stores/gateway.ts (WebSocket), config.ts (config)
              ui-gui-vue3/src/components/ (40 .vue components)
                 ↑ hosted by
[Desktop Shell] ui-gui-vue3/src-tauri/ (Tauri 2 Rust): frameless window + sidecar launch/exit cleanup of pebble-backend.exe
```

### Module Dependency Table

| Module | Layer | Callers | Callees |
|------|-----|--------|---------|
| ws_server.py | Gateway | - | server.py |
| server.py | Gateway | ws_server.py, entry.py | agent_chat_service_v2, session, config |
| session.py | Data (sessions.db: session CRUD + project_path multi-project ownership + compaction flags) | create(project_path), list(limit, project_path) (incl. last_message_at = latest message time), delete(), delete_message(), adopt_unassigned(), unassigned_count() | server.py | sessions.db | 📦Data |
| agent_chat_history.py | Data | agent_chat_service_v2 | agent_chat.db |
| **agent_chat_service_v2.py** | 🔴Core | server.py → chat_stream() | OpenAI SDK, tool_orchestrator, agent_chat_history, agent_loader, context_compressor, memory |
| agent_loader.py | Service | agent_chat_service_v2 | agents/*.yaml + *_prompt.md |
| context_compressor.py | Service | agent_chat_service_v2, server.py | - |
| tool_orchestrator.py | Service | agent_chat_service_v2 | tool_executor_impl |
| tool_executor_impl.py | Service | tool_orchestrator | tools/ |
| tool_definitions.py | Tools | agent_chat_service_v2 | extensions_loader, MCP |
| extensions_loader.py | Tools | tool_definitions | extensions/tools/*.py |
| agent_router.py | ⚠️stub | none | none |
| config.py | Gateway | server.py | llm_providers.json |
| gateway.ts | Frontend | - | WebSocket |
| config.ts | Frontend | gateway.ts | server.py (via WebSocket) |
| transcript_manager.py | Data (JSONL transcript archive ~/.claude/projects/) | context_compressor, agent_chat_service_v2 | transcript files |
| permission_audit.py | Data (audit permission_audit.db) | permission_enforcer, tool_executor_impl | permission_audit.db |
| bash_validator.py | Tool infra (6-layer command validation) | tool_executor_impl, permission_enforcer, tool_concurrency | - |
| lsp_client.py | Tool infra (LSP client) | tool_executor_impl | LSP server subprocess |
| code_network_guardV2.py | Tool infra (network guards) | agent_chat_service_v2, tool_executor_impl | - |
| electron_bridge.py | Tool infra (browser bridge) | tool_executor_impl | Electron BridgeServer |
| src-tauri/lib.rs | Desktop shell (Tauri 2) | - | pebble-backend.exe (sidecar launch/cleanup) |
