# 9. Configuration & Auxiliary Files

### Root Config & Scripts (config / scripts / packaging / misc)

| File | Function | Read/Write by | Tag |
|------|------|--------|------|
| llm_providers.json | Provider list, API key, model, temperature, topP, reasoning_effort, thinking_enabled | server.py (rw), config.ts (read via WebSocket) | 📦Core |
| agent_model_config.json | Per-agent model_source | server.py (rw), agent_chat_service_v2 (read) | 📦Core |
| model_pricing.json | Per-model price table (input/cache_hit/output per 1M tokens); deepseek-v4-flash / deepseek-v4-pro / deepseek-v4-flash-vision-exp carry pricing_peak/pricing_offpeak dual tiers (vision same-priced as flash), mimo/qwen keep single pricing | agent_chat_service_v2._load_model_pricing (startup + mtime hot reload), server.py config.get 'pricing' (frontend pulls authoritative price table + period) | 📦Core |
| cn_holidays.json | Chinese statutory holiday data (State Council schedule, incl. make-up days; holidays = full-day off-peak; workdays recorded only); 2025–2026 covered | agent_chat_service_v2 (startup preload + candidate-file snapshot hot reload), server.py config.get 'pricing' (pushed for frontend local pre-judgment) | 📦Core |
| usernote.json | User note (cross-session persistent) | server.py (rw), agent_chat_service_v2 (read → injected into prompt) | 📦Core |
| quick_notes.json | Quick notes (cross-session persistent, under ~/.pebble/, follows _DATA_DIR) | server.py (rw), QuickNotes.vue (via WebSocket) | 📦Core |
| .mcp.json | MCP server config (standard .mcp.json format; 6 servers: github / time / codebase-memory / think-tool / chrome-devtools / blender). blender = uvx mcp-for-blender (PyPI, formerly blender-mcp) → connects to the Blender addon socket (localhost:9876); requires uv + Blender 3.0+ with the MCP addon enabled (addon ships Hyper3D/Hunyuan3D generation + Poly Haven/Poly Pizza free-asset integration). Evaluated Hyper3D Rodin's official remote MCP (api.hyper3d.com/api/mcp via mcp-remote bridge): generation consumes paid credits — user decided against it. **Project-level files require approval**: fingerprint (path + content sha256) recorded in ~/.pebble/mcp_trusted.json; unapproved files are skipped with a log warning; approve via McpPanel or the mcp.trust_project RPC | mcp_config.py, server.py (read) | 📦Config |
| mcp_install.json | MCP dependency declaration: prerequisites (node / uv check_command + official download links) and servers metadata (each MCP's type/package/requires/description; codebase-memory download points to DeusData Releases; **sha256 required for auto-download: absent = download refused; manual 'path' install unaffected**) | server.py mcp.install_info (read; shutil.which dependency checks), McpPanel.vue (via mcp.list / mcp.install_info RPC) | 📦Config |
| permission_rules.json | Permission rules (mode + allow/deny/ask; syntax `tool:pattern`, e.g. `execute_shell:git *`, `write_file:*.env`) | permission_manager.py | 📦Config |
| path_whitelist.json | Path whitelist (max 5 entries) | server.py, agent_chat_service_v2._load_path_whitelist | 📦Config |
| requirements.txt | Python dependency list (18 direct deps in four groups: core 4 / optional 11 / skills 1 / build 2; ">= verified version" format; uninstalled packages annotated in comments; minimum Python 3.11 — numpy>=2.4 requires it, dev environment 3.14) | pip install | 📦Build |
| start_backend.bat / start_gui.bat | Windows launch scripts | user | 🟡Script |
| peb_check_holiday.py / peb_check_holiday_env.py / peb_check_pricing_scope.py | Billing-decision checks (holiday main rules 30 cases + isolated env: priority+hot reload+fallback+degradation / peak-scope: all-model time sensitivity + numeric assertions) | user runs manually (python <script>) | 🧪Test |
| pebble-backend.spec | PyInstaller packaging config (entry run_backend.py; datas bundle skills / agent_core/agents / knowledgesource / config JSONs; output dist/pebble-backend.exe) | build (Tauri sidecar chain, see below) | 📦Build |
| run_backend.py | Backend entry point (→ gateway/ws_server.py) | user / Tauri sidecar | 🟡Script |
| skills_loader.py | Skills loader (skills/ directory scan; YAML frontmatter description, single-line fallback for non-standard files) | list_skills / load_skill / get_skill_names | 🟡Aux |
| agent_chat.db | Frozen DB (unused); the current message DB is {PEBBLE_HOME}/agent_chat.db (e.g. %USERPROFILE%\.pebble\) | - | 🗑️Deprecated |

### Tauri Desktop Shell & Packaging Pipeline (ui-gui-vue3/src-tauri/)

Shell = Tauri 2 (Rust), productName "pebble-agent", identifier com.pebble.agent.

| File | Function | Tag |
|------|------|------|
| src/lib.rs | Shell core: sidecar backend management (launch / priority boost / exit cleanup); **get_gateway_token command (reads {USERPROFILE}/.pebble/gateway.token for the webview)** | 🔴Core |
| src/main.rs | Entry (windows_subsystem hides the console in release) | 🟡Aux |
| tauri.conf.json | Frameless window (1280×800, decorations:false, dragDropEnabled:false); devUrl 5174; **CSP + devCsp** (default-src 'self'; script/style allow 'unsafe-inline' + https: — required by the srcdoc preview inheriting the host policy; connect-src allows ws/http 127.0.0.1:19876; devCsp additionally allows the 5174 HMR endpoints); bundle resources binaries/*; shell plugin (open only ^https?://) | 📦Config |
| capabilities/default.json | Window permissions (minimize / toggle-maximize / close / start-dragging) + shell:allow-open | 📦Config |
| Cargo.toml | tauri 2.11.3 + plugin-log / plugin-shell; lib crate = app_lib | 📦Config |
| binaries/pebble-backend-x86_64-pc-windows-msvc.exe | Sidecar backend (PyInstaller artifact ~89MB) | 📦Artifact |
| gen/schemas/ | Tauri-generated permission schemas (automatic) | 📦Generated |

**Sidecar lifecycle (src/lib.rs)**:
1. setup: find `pebble-backend-x86_64-pc-windows-msvc.exe` in resource_dir → launch with `CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP` (stdout/stderr nulled) → set HIGH_PRIORITY_CLASS + IO priority Normal
2. 1 second later boost_all_backends: iterate a Toolhelp32 process snapshot and boost every pebble-backend* process (the Python child spawned by the PyInstaller bootloader) once more
3. RunEvent::Exit: `taskkill /F /T /PID` kills the whole sidecar process tree

**Packaging pipeline**:
1. PyInstaller (root `pebble-backend.spec`, entry run_backend.py) → `dist/pebble-backend.exe` (datas bundle skills / agent_core/agents / knowledgesource / config JSONs)
2. Copy the artifact to `ui-gui-vue3/src-tauri/binaries/pebble-backend-x86_64-pc-windows-msvc.exe` (Tauri sidecar naming: {name}-{target-triple}.exe)
3. Tauri bundle (`bundle.resources "binaries/*"`, targets:"all") → installer; frontend build = `npm run build` (vue-tsc && vite build)

> ⚠️ The sidecar filename must carry the target-triple suffix for Tauri to recognize it; after backend changes, re-run PyInstaller and copy to binaries/.

### .pebble/ Auxiliary Directory (diagnostic tools & verification scripts, kept by user decision)

`.pebble/` is the in-project runtime-data directory (snapshots / truncation archives / _tmp_* investigation artifacts). `_tmp_verify/` accumulated diagnostic tools and verification scripts from past investigations and fixes — **low frequency, zero cost, high diagnostic value; the user has confirmed keeping them all** (reuse directly for future troubleshooting, do not clean up):

| File | Purpose | Tag |
|------|------|------|
| _tmp_verify/thread_monitor.html | **Main-thread monitor page** — open in a browser when suspecting jank/disconnects: rAF sampling measures main-thread blocking in real time (records >500ms blocks, distinguishes hidden windows, shows fps/max block duration); the first-hand measurement tool for "streaming jank/disconnect" issues | 🟡Diag |
| _tmp_verify/measure_mem.py | WebView2 memory-growth measurement (90s window; filters msedgewebview2 processes by com.pebble.agent, reports per-PID delta and MB/min growth) | 🟡Diag |
| _tmp_verify/inspect_db.py | Quick agent_chat.db inspection (table schema + conversation data-volume ranking) | 🟡Diag |
| _tmp_verify/storage_perf.html | localStorage performance test page (read/parse/write timings for a ~7.8M-char conversation cache; diagnosing write jank) | 🟡Diag |
| _tmp_verify/verify_*.mjs / verify_*.py | Regression scripts from past fixes (rendering performance / WS recovery / thinking toggle / price panel / input resize / jump-folding / agent tool whitelist etc., re-runnable) | 🧪Verify |
| _tmp_verify/verify_security_fixes.py | Security regression script (isolated PEBBLE_HOME): WS token/auth helpers / payload limits / file.* path guard / PathBoundaryChecker / unknown-tool mapping / sync permission evaluation / trust granularity / audit checksum / enforcer end-to-end | 🧪Verify |
| _tmp_verify/check_*.py / investigate_*.py / extract_*.py / *_out.txt / real_messages.json | Investigation helpers and output snapshots (billing checks / interruption probes / event extraction / real message samples) | 🟡Investigation |

> Retention rationale (user-confirmed): these assets are used passively only when diagnosing, cost nothing at rest; rebuilding them after removal would be expensive if an issue recurs, so they are kept wholesale. Companion long-term logs: backend "Stream events dropped" (Chapter 4 ws_server.py) and frontend [WS] lifecycle (Chapter 8 gatewayClient.ts).

### Built-in Skills (114 skill directories total)

| Skill | Path | Function |
|-------|------|------|
| ckb | skills/ckb/ | Code knowledge-base mechanism (detect → build → verify) |
| design | skills/design/ | Frontend design / Office document task specs |
| diagnose | skills/diagnose/ | Structured debugging workflow (6 phases) |
| tdd | skills/tdd/ | Red-green-refactor loop + test strategy |
| zoom-out | skills/zoom-out/ | Big-picture view — module map and call relationships |
| read-the-damn-docs | skills/read-the-damn-docs/ | Read the docs before writing code |
| quick-recap | skills/quick-recap/ | Status marker at the end of each response |
| officecli | skills/officecli/ | OfficeCLI hard rules (PPT/Word/Excel) |
| canvas-design | skills/canvas-design/ | Canvas design (incl. 30+ fonts) |
| html-diagram | skills/html-diagram/ | HTML diagram/architecture chart generation |
| frontend-design | skills/frontend-design/ | General frontend design specs |
| huashu-design | skills/huashu-design/ | Picture-book design (incl. TTS/PDF/PPTX scripts) |
| dream-loop | skills/dream-loop/ | Closed-loop 3D visual construction (target image → Blender build → critic iteration; from achimala/dream-loop, incl. references/scripts) |
| blender-character-workflow | skills/blender-character-workflow/ | Blender character creation/repair workflow (nine-view references, face/hair shaping, production delivery; from icesixgod/awesome-astra-blender-characters) |

**Extended skills (100)**: deck-* (20 presentation templates), officecli-* (8 Office document workflows: docx / pptx / xlsx / pitch-deck / academic-paper / financial-model / word-form / data-dashboard), frame-* (7 motion frames), social-* (6 social cards), web-proto-* (3), imagegen-* (2), plus theme-factory / taste-skill / soft-skill / minimalist-skill / brutalist-skill / stitch-skill / redesign-skill / image-to-code-skill / output-skill etc., loaded dynamically via `list_skills` / `load_skill`.
