# Project Code Knowledge Base — PebbleAgent (Index)

> Last updated: 2026-10-15 | Twenty-one items: search_files hardening + grep/git output decoding + session deletion race + manual compaction trigger revamp (>10 → ≥2 messages) + compacted-message frontend folding + "silent stream termination" fix (log persistence + finish_reason + suspicious-end auto-retry) + sidebar time-based auto grouping (seven buckets + label highlight, English) + ask_user_question multi-question pipeline fix (dual-channel compatibility) + chat_agent tool whitelist enforcement (enforce: schema filtering + execution interception, four file tools only) + input box top-edge drag resize (38px ~ 70% viewport, localStorage persistence) + jump-vs-fold conflict fix (folding-message jump targets divider without expanding) + thinking toggle and effort revamp (frontend toggle + all-provider reasoning_effort + Xiaomi max→high downgrade) + model price editor panel ($ button + pricing.save atomic write + hot reload) + provider tabs horizontal scroll (arrows + wheel scrolling, replacing wrap) + price panel input column headers (cache miss/cache hit/output) + WS disconnect display-loss fix (frontend reconnect auto-recovery + backend disconnect logging + frontend lifecycle logging) + rendering performance fixes (streaming throttle 120ms + line cache; segment-level v-memo freeze rolled back — content-mixing bug) + diagnostic assets retention note (.pebble/_tmp_verify tool list + disconnect log rationale) + requirements.txt dependency manifest added (18 direct dependencies in four groups) + mcp_install.json fixes (codebase-memory 404 link → DeusData Releases; blender description install steps) + full knowledge base calibration (7 major modules added + Tauri desktop shell & packaging pipeline + file size corrections + full RPC list + frontend omissions) | 87 .py + 57 .vue/.ts + 2 .rs

1. Project Overview (positioning, tech stack, entry points, the CKB mechanism itself)
2. Module Topology (module layers and call-relationship diagram)
3. Critical Paths (cross-system main flows: message pipeline, config flow, WS event types, HTML comment loop)
4. Gateway System (WebSocket service, JSON-RPC, session management, permissions)
5. Agent System (chat service, context, memory, snapshots, agent definition files)
6. Tool System (built-in tools, extension tools, MCP)
7. LLM Integration & Billing (API parameter construction, cost pipeline, time-of-day pricing, context compaction)
8. Frontend UI (Vue 3: stores, components, services, composables, utils)
9. Configuration & Auxiliary Files (root configs, launch scripts, Tauri desktop shell & packaging pipeline, skills list)

> Reading guide: read this index first to locate chapters, then read the relevant chapter files on demand (e.g. "04-Gateway-System.md").
