# Project Code Knowledge Base — PebbleAgent (Index)

> Last updated: 2026-10-21 | Scope: WebSocket gateway, agent runtime, tool system (46 built-in tools), cross-turn state, LLM billing, token usage for unpriced models, per-provider request shaping (headers / reasoning mode), first-turn session auto-naming, live token-rate display, Vue 3 + Tauri frontend, Tauri CSP asset-hardening exemption for the srcdoc preview, CKB | 129 .py + 62 .vue/.ts + 3 .rs (excludes __pycache__, dist, build, node_modules)

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
