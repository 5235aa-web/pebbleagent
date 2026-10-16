# 1. Project Overview

- **Languages**: Python 3 (backend) + Vue 3/TypeScript (frontend) + Rust (Tauri desktop shell)
- **Communication**: WebSocket (websockets) + JSON-RPC 2.0
- **Databases**: two SQLite databases — `sessions.db` (gateway) + `agent_chat.db` (agents); plus an audit database `permission_audit.db`
- **LLM**: OpenAI Python SDK → DeepSeek API (multi-provider support)
- **Entry points**: backend `run_backend.py` → `gateway/ws_server.py` (ws://127.0.0.1:19876); the desktop build launches the packaged backend `pebble-backend.exe` as a sidecar from the Tauri shell (`ui-gui-vue3/src-tauri`)
- **Frontend**: `ui-gui-vue3/` (Vue 3 + Pinia + Vite + Tauri 2 desktop shell)
- **Packaging**: PyInstaller (`pebble-backend.spec` → sidecar exe) → Tauri bundle (see Chapter 9)
- **Deprecated code**: `ui-gui/` (React)
- **Core modules**: 7 (gateway / agent_core / tools / memory / mcp / snapshot / src-tauri)

## The CKB Mechanism Itself

This knowledge base is the product of the CKB (Code Knowledge Base) mechanism. Details:

- **Knowledge Base structure**: `knowledgesource/content.md` (index) + chapter files (e.g. "04-Gateway-System.md"); both reading and updating start from the index, then locate the relevant chapter on demand
- **Skill files**: `skills/ckb/SKILL.md` (loaded by the agent) + `skills/ckb/CKB_SPEC.md` (v2.0 technical spec)
- **System prompt**: CKB detection rules are embedded at the top of `code_agent_prompt.md`
- **Agent YAML**: `code_agent.yaml` preloads the `ckb` skill
- **Trigger condition**: on conversation start, detect `knowledgesource/content.md`:
  - Exists → read the index to locate chapters, then read chapter files on demand
  - Missing → fall back to `PROJECT_GRAPH.md` (legacy single-file format); if neither exists → `ask_user_question` asks the user whether to build the knowledge base
- **Build toolchain**: `mcp__codebase_memory__index_repository` (index) → `get_architecture` (architecture) → `trace_path` (relationship tracing) → `glob` + `read_file` (sampling verification)
- **Build hard constraints**: until the build completes, do not answer task requests, execute code, modify files, or speculate about the project
