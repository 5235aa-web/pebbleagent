# Project Code Knowledge Base — PebbleAgent (Index)

> Last updated: 2026-10-19 | Scope: WebSocket gateway, agent runtime, tool system (46 built-in tools), cross-turn state, LLM billing, Vue 3 + Tauri frontend, CKB | 123 .py + 61 .vue/.ts + 3 .rs

1. Project Overview (positioning, tech stack, entry points, the CKB mechanism itself)
2. Module Topology (module layers and call-relationship diagram)
3. Critical Paths (cross-system main flows: message pipeline, config flow, WS event types, HTML comment loop, file snapshot revert)
4. Gateway System (WebSocket service, JSON-RPC, session management, permissions)
5. Agent System (chat service, context, memory, snapshots, agent definition files)
6. Tool System (built-in tools, extension tools, MCP)
7. LLM Integration & Billing (API parameter construction, cost pipeline, time-of-day pricing, context compaction)
8. Frontend UI (Vue 3: stores, components, services, composables, utils)
9. Configuration & Auxiliary Files (root configs, launch scripts, Tauri desktop shell & packaging pipeline, skills list)

> Reading guide: read this index first to locate chapters, then read the relevant chapter files on demand (e.g. "04-Gateway-System.md").
