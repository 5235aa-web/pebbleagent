# Project Code Knowledge Base — PebbleAgent (Index)

> Last updated: 2026-11-11 | experience injection hardened: weak English signal patterns need correction context, kind-round-robin selection with a 14-entry window, evidence line dropped, cache signature built from the selected rows | Scope: system prompt prefix stability — the experience block is selected deterministically (no recall query) and appended last, so neither a new turn nor a process restart invalidates the provider's prompt cache MCP project-level trust (the bundled .mcp.json loads without an approval prompt because its _MEIxxxx path changes every launch; stale approvals pruned at startup), project panel RPCs (tree rooted at the active project with loading/refused/empty states, corrupt registry surfaced in the panel, add answers the stored path, session.list matches normalised project paths, shared confirm sheet replacing window.confirm), project registry hardening (atomic `projects.json` + `.corrupt` quarantine, empty-path guard, running-turn switch refusal), first-launch project folder guide (setup dialog + empty registry), runtime gateway-token resolution (Tauri command first, dev injection is serve-only), WebSocket gateway with a second opt-in TLS listener for mobile clients (self-signed cert + fingerprint pinning, single-use pairing codes, live enable/disable), agent runtime, tool system (45 built-in tools on Windows, 44 elsewhere: powershell is platform-gated), cross-turn state, LLM billing, token usage for unpriced models, per-provider request shaping (headers / reasoning mode), first-turn session auto-naming, live token-rate display, Vue 3 + Tauri shell (frontend/, chapter 10), clarify cards with a free-form answer field per question, Tauri CSP asset-hardening exemption for the srcdoc preview, right-side preview dock (single live preview iframe outside the message flow, multi-file tabs), Android client transport, reconnect and background keeping, Orbit frontend rebuild with collapsed compaction runs (zero-DOM folding, divider pinned across toggles), CKB, built-in skills trimmed from 120 to 50 (the prompt's seven, the design/officecli chain, the HyperFrames video family, six frontend extensions), clickable transcript links (markdown and bare `http(s)` addresses become `a.md-link` with `data-href`, clicked through a delegated listener to `system.open_url`, which opens the OS default browser and refuses every other scheme) | 135 .py + 61 .vue/.ts (frontend/src) + 3 .rs (excludes __pycache__, dist, build, node_modules)

1. Project Overview (positioning, tech stack, entry points, the CKB mechanism itself)
2. Module Topology (module layers and call-relationship diagram)
3. Critical Paths (cross-system main flows: message pipeline, config flow, WS event types, HTML comment loop, file snapshot revert)
4. Gateway System (WebSocket service, JSON-RPC, session management, permissions)
5. Agent System (chat service, context, memory, snapshots, agent definition files)
6. Tool System (built-in tools, extension tools, MCP)
7. LLM Integration & Billing (API parameter construction, cost pipeline, time-of-day pricing, context compaction)
8. Configuration & Auxiliary Files (root configs, launch scripts, Tauri desktop shell in frontend/src-tauri & packaging pipeline, skills list)
9. Android Client (Kotlin + Compose companion app: gateway transport and retry loop, connection lifecycle guards, foreground service and background keeping, catch-up resync, global event routing, streaming transcript, approval/clarification queues, file changes, usage, settings and accent theming)
10. Orbit Frontend (the only front end: frontend/ gateway transport, transcript build, compaction folding, streaming blocks, interrupted-turn settling, prototype-frozen CSS)

## Ownership

```
01-Project-Overview   — cross-cutting only
02-Module-Topology    — cross-cutting; carries the miscellaneous table
03-Critical-Paths     — cross-cutting only
04-Gateway-System     — gateway/**
05-Agent-System       — agent_core/**
06-Tool-System        — agent_core/tool_impl/**, agent_core/extensions/**, agent_core/mcp/**
07-LLM-Billing        — cross-cutting only (billing and compaction behaviour, not a directory)
08-Configuration      — root *.json / *.yaml / *.toml, run_backend.py, pebble-backend.spec, frontend/src-tauri/**
09-Android-Client     — external: android/** (companion app source lives outside this repository)
10-Orbit-Frontend     — frontend/src/**
```

> Coverage: `ckb_audit.py` reports missing 0, extra 0, weak 10. The ten weak rows are `__init__.py` and `prompt.py` entries whose short names match more than one file, so the chapter scope cannot resolve them uniquely.

> Reading guide: read this index first to locate chapters, then read the relevant chapter files on demand (e.g. "04-Gateway-System.md").
