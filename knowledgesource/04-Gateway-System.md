# 4. Gateway System

### gateway/ (10 .py)

| File | Function | Key symbols | Callers | Callees | Tag |
|------|------|---------|--------|---------|------|
| ws_server.py | WebSocket server (**token auth + Origin whitelist + limits**: token generated/persisted at {PEBBLE_HOME}/gateway.token (delete to rotate), verified via X-Pebble-Token header **preferred** or `?token=` query (header-first) with hmac.compare_digest (close 4401); Origin whitelist localhost:5174 / tauri.localhost variants (close 4403; non-browser clients without Origin pass on token check only); connection cap 8 (close 4429); max_size 128MiB + prompt payload limits (text ≤2M chars / ≤20 attachments / ≤14MB base64 each / ≤100MB total → "prompt.rejected" event); **run_server calls log_setup.setup_logging**; **WebSocketWriter._send logs "Stream events dropped" once per closed connection (_send_failed dedup prevents spam) — a deliberate operational log; frontend recovers display from DB after reconnect**; **startup audit-integrity check (_verify_audit_integrity_background → PermissionAuditLogger.verify_integrity in a thread)**) | handle_ws, main, _load_or_create_gateway_token, _check_payload_limits, _verify_audit_integrity_background | - | server.py, log_setup | 🔴Entry |
| server.py | JSON-RPC handler (incl. file.read_text, session.compress manual-compaction RPC, usernote.read/write, quick-notes.read/write, assistant messages saved with full execution_order + **msg_uuid=assistant_msg_uuid cross-DB alignment**, error events persisted as **Error:** messages, **config.get 'pricing': price table + authoritative period + holiday data (cn_holidays) pushed to frontend**, message.usage forwarded with period, **user_message.saved / message.complete event payloads carry session_id (for frontend session-list refresh)**), **full RPC family (60+ methods) — see the "JSON-RPC Method List" below — incl. message.delete, session.export / export_save (session export to markdown → project_path/history/), project.* (multi-project management), file.open, session.file_* (file-changes panel family), memory.*, permission.*, etc.**; **file.* RPCs (read_text/read_base64/write_base64/open/search) enforce a project-boundary/whitelist path guard (_guard_rpc_path; file.open additionally allows http/https/ftp URLs); permission.check uses sync evaluation + fail-closed; mcp.trust_project RPC + mcp.list returns pending_project_servers**; **path_whitelist.save validates entries (non-empty absolute paths, drive/filesystem roots rejected); permission.mode.set writes a mode_changed audit event; permission.audit.verify RPC** | handle_request, handle_prompt_submit | ws_server.py | agent_chat_service_v2, session, agent_chat_history | 🔴Core |
| session.py | sessions.db ORM | SessionStore class (incl. insert_compaction_result: boundary+summary insert, is_compacted/is_compact_summary flags; list subquery emits last_message_at = latest message time for frontend date display; **all write methods have rollback protection**: rollback on failure releases the write lock, preventing cascading "database is locked") | server.py | sessions.db | 📦Data |
| config.py | Startup config loading | load_llm_config | server.py | llm_providers.json | 📦Config |
| entry.py | TUI stdin JSON-RPC | main, dispatch | - | server.py | 🟡Aux |
| log_setup.py | Unified logging (stderr + RotatingFileHandler → {PEBBLE_HOME}/logs/backend.log, 5MB × 5 rotation, UTF-8; idempotent, silently degrades if the file is not writable) | setup_logging | ws_server.run_server, entry.py | logging.handlers | 🟡Aux |
| permission_manager.py | Permission manager (**modes: manual / auto / readonly / locked; rule syntax `tool:pattern` (e.g. `execute_shell:git *`, `write_file:*.env`); rules evaluated deny → ask → allow; **inline-code guard: prefix allow rules are skipped for python -c / node -e / powershell -Command / bash -c forms and escalate to ask**; **_get_permission_type reuses agent_core.tool_permission as the single mapping source and defaults unmapped tools to UNKNOWN_TOOL (trust keys isolated from exec:command); `git *` prefix match uses a word boundary**) | PermissionManager | server.py | permission_rules.json | 🟡Aux |
| permission_service.py | Permission push service (**trust granularity = (session, permission_type)** — remembering "read" does not auto-approve exec/delete; MCP_TOOL / UNKNOWN_TOOL permission types, level MEDIUM) | get_permission_service | ws_server.py | - | 🟡Aux |
| permission_audit.py | Permission-decision audit log (SQLite: permission_audit.db): event types (check/allow/deny/timeout/path-violation/rule-matched/mode-changed) + decision sources (user approve/deny/rule/bypass/low-risk/timeout) + **chained checksum v3: sha256(prev_checksum|payload) over all 20 security fields — tampering or prefix-strip downgrade breaks the chain and fails verification; v2 records handled compatibly; writes serialized by a lock**; **write-time masking (_mask_sensitive: sk-/ghp_/gho_/github_pat_/Bearer/key=value patterns → ***, applied to target/content/tool_args/reason/error_message/extra before checksum); verify_integrity wired to ws_server startup + permission.audit.verify RPC; log_permission_check in PermissionEnforcer.check (records full tool_args, masked)**; query / get_statistics / export_to_json | PermissionAuditLogger, get_audit_logger, AuditEvent, AuditEventType, AuditDecisionSource | permission_enforcer, tool_executor_impl | permission_audit.db | 🔴Core |

### JSON-RPC Method List (server.py)

| Domain | Methods |
|------|------|
| session | create / list / most_recent / resume / title / delete / info / search / export (returns markdown) / export_save (writes project_path/history/) / compress / interrupt / file_changes / file_accept / file_diff / file_revert / file_revert_all / unassigned_count / adopt_unassigned |
| project | list / add / delete / set_active / set_path (multi-project management) |
| message | delete (message deletion) |
| model | options / provider.set / provider.save / provider.delete / params.set / switch |
| agent | list / switch |
| mcp | list (incl. pending_project_servers) / reload / install_info / trust_project |
| memory | list / get / create / update / delete / prompt |
| agent-memory | get / set / clear |
| permission | mode.get / mode.set (audited) / rules.get / check (sync evaluation, fail-closed) / decide / audit.verify (chained-checksum integrity check) |
| path_whitelist | read / save (entries validated absolute, non-empty, no drive/filesystem roots) |
| file | search / read_text / read_base64 / write_base64 / open (project-boundary/whitelist guard ; open additionally allows http/https/ftp URLs) |
| usernote / quick-notes | read / write |
| pricing | save |
| config | get / set |
| other | setup.status / complete.slash / slash.exec / command.dispatch / clarify.respond |

> Event types (WS push) are not in this table: message.delta / thinking.delta / tool.* / clarify.request / permission.request / todos.update etc. — see Path 3 in Chapter 3.
