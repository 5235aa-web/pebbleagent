#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Tool registry parity.

Every registered handler must exist on the executor, every schema tool must be
reachable, and the legacy dispatch surface must stay complete.

Run: python tests/test_tool_registry.py   (exit 0 = pass)
"""
import inspect
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from agent_core.tool_executor_impl import AgentToolExecutorImpl, WRITE_TOOLS
from agent_core.tool_impl.registry import TOOL_HANDLERS

passed = 0
failed = 0


def check(label, cond, detail=""):
    global passed, failed
    if cond:
        passed += 1
        print(f"[PASS] {label}")
    else:
        failed += 1
        print(f"[FAIL] {label} {detail}")


EXPECTED = {
    "execute_code", "execute_shell", "powershell", "read_file", "write_file",
    "edit_file", "delete_file", "copy_file", "move_file", "list_directory",
    "create_directory", "get_file_info", "read_pdf", "grep", "glob", "ls",
    "search_in_file", "search_files", "find_files", "git_status", "git_log",
    "git_diff", "fetch_url", "browser_navigate", "browser_download",
    "get_env_var", "set_env_var", "get_system_info", "list_processes",
    "kill_process", "todo_write", "todo_read", "clarify",
    "usernote_write", "list_skills", "load_skill", "enter_plan_mode",
    "exit_plan_mode", "create_plan", "get_plan", "tool_search", "list_tools",
    "lsp_go_to_definition", "lsp_find_references", "lsp_hover",
    "lsp_document_symbol", "lsp_workspace_symbol", "lsp_go_to_implementation",
    "lsp_prepare_call_hierarchy", "lsp_incoming_calls", "lsp_outgoing_calls",
}

missing = EXPECTED - set(TOOL_HANDLERS)
check("all expected tools registered", not missing, f"missing={sorted(missing)}")
extra = set(TOOL_HANDLERS) - EXPECTED
check("no unexpected tools", not extra, f"extra={sorted(extra)}")

ex = AgentToolExecutorImpl(project_path=ROOT)

bad_attr = []
not_callable = []
for name, handler in TOOL_HANDLERS.items():
    if not callable(handler):
        not_callable.append(name)
        continue
    try:
        result = handler(ex, {}, None)
    except Exception as e:
        bad_attr.append(f"{name}: {type(e).__name__}: {e}")
        continue
    if not inspect.iscoroutine(result):
        bad_attr.append(f"{name}: not a coroutine")
    else:
        result.close()

check("all handlers callable and async", not bad_attr, f"issues={bad_attr[:5]}")
check("handlers are callables", not not_callable, f"issues={not_callable}")

check("write tools tracked", WRITE_TOOLS == {
    "write_file", "edit_file", "delete_file", "copy_file", "move_file"})

# Domain mixins must stay separate modules (no handler back in the facade).
facade_src = open(os.path.join(ROOT, "agent_core", "tool_executor_impl.py"),
                  encoding="utf-8").read()
check("facade has no tool implementation",
      "async def _tool_" not in facade_src)

print(f"\nRESULT: PASS={passed} FAIL={failed}")
sys.exit(0 if failed == 0 else 1)
