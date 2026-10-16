# 4. Gateway System

### gateway/ (10 .py)

| File | Function | Key symbols | Callers | Callees | Tag |
|------|------|---------|--------|---------|------|
| ws_server.py | WebSocket server (**serve(max_size=None) removed the per-message size limit** — fixed 2026-10-03 for the incident where large base64 images exceeding the 1MiB default were rejected with code 1009; **run_server() calls log_setup.setup_logging() since 2026-10-15**; **WebSocketWriter._send no longer silent on disconnect (2026-10-15): logs "Stream events dropped" once per closed connection (_send_failed dedup prevents spam) — a deliberate operational log (not debug residue, silent in normal operation), the first clue for connection troubleshooting; frontend recovers display from DB after reconnect**) | handle_ws(), main() | - | server.py, log_setup | 🔴Entry |
| server.py | JSON-RPC handler (incl. file.read_text, session.compress manual-compaction RPC, usernote.read/write, quick-notes.read/write, assistant messages saved with full execution_order + **msg_uuid=assistant_msg_uuid cross-DB alignment**, error events persisted as **Error:** messages, **config.get 'pricing': price table + authoritative period + holiday data (cn_holidays) pushed to frontend**, message.usage forwarded with period, **user_message.saved / message.complete event payloads carry session_id (for frontend session-list refresh)**), **full RPC family (60+ methods, verified 2026-10-15) — see the "JSON-RPC Method List" below — incl. message.delete, session.export / export_save (session export to markdown → project_path/history/), project.* (multi-project management), file.open, session.file_* (file-changes panel family), memory.*, permission.*, etc.**) | handle_request(), handle_prompt_submit() | ws_server.py | agent_chat_service_v2, session, agent_chat_history | 🔴Core |
| session.py | sessions.db ORM | SessionStore class (incl. insert_compaction_result: boundary+summary insert, is_compacted/is_compact_summary flags; list() subquery emits last_message_at = latest message time for frontend date display; **since 2026-10-15 all write methods have rollback protection**: rollback on failure releases the write lock, preventing cascading "database is locked") | server.py | sessions.db | 📦Data |
| config.py | Startup config loading | load_llm_config() | server.py | llm_providers.json | 📦Config |
| entry.py | TUI stdin JSON-RPC | main(), dispatch() | - | server.py | 🟡Aux |
| log_setup.py | Unified logging (added 2026-10-15: stderr + RotatingFileHandler → {PEBBLE_HOME}/logs/backend.log, 5MB × 5 rotation, UTF-8; idempotent, silently degrades if the file is not writable) | setup_logging() | ws_server.run_server(), entry.py | logging.handlers | 🟡Aux |
| permission_manager.py | Permission manager | PermissionManager | server.py | permission_rules.json | 🟡Aux |
| permission_service.py | Permission push service | get_permission_service() | ws_server.py | - | 🟡Aux |
| permission_audit.py | Permission-decision audit log (SQLite: permission_audit.db): event types (check/allow/deny/timeout/path-violation/rule-matched/mode-changed) + decision sources (user approve/deny/rule/bypass/low-risk/timeout) + SHA256 checksum tamper protection (verify_integrity); query() / get_statistics() / export_to_json() | PermissionAuditLogger, get_audit_logger(), AuditEvent, AuditEventType, AuditDecisionSource | permission_enforcer, tool_executor_impl | permission_audit.db | 🔴Core |

### JSON-RPC Method List (server.py, verified 2026-10-15)

| Domain | Methods |
|------|------|
| session | create / list / most_recent / resume / title / delete / info / search / export (returns markdown) / export_save (writes project_path/history/) / compress / interrupt / file_changes / file_accept / file_diff / file_revert / file_revert_all / unassigned_count / adopt_unassigned |
| project | list / add / delete / set_active / set_path (multi-project management) |
| message | delete (message deletion) |
| model | options / provider.set / provider.save / provider.delete / params.set / switch |
| agent | list / switch |
| mcp | list / reload / install_info |
| memory | list / get / create / update / delete / prompt |
| agent-memory | get / set / clear |
| permission | mode.get / mode.set / rules.get / check / decide |
| path_whitelist | read / save |
| file | search / read_text / read_base64 / open |
| usernote / quick-notes | read / write |
| pricing | save |
| config | get / set |
| other | setup.status / complete.slash / slash.exec / command.dispatch / clarify.respond |

> Event types (WS push) are not in this table: message.delta / thinking.delta / tool.* / clarify.request / permission.request / todos.update etc. — see Path 3 in Chapter 3.
