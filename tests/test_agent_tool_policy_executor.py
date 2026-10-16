#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Agent tool policy: executor entry-point interception (chat_agent allowlist enforced end-to-end).

Run: python tests/test_agent_tool_policy_executor.py   (exit 0 = pass)
"""
import asyncio
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def main() -> int:
    passed = 0
    failed = 0

    def check(name, cond, detail=""):
        nonlocal passed, failed
        if cond:
            passed += 1
            print(f"[PASS] {name}")
        else:
            failed += 1
            print(f"[FAIL] {name} {detail}")

    from agent_core.tool_executor_impl import AgentToolExecutorImpl
    from agent_core.query_state_machine import ToolExecutionContext

    # --- Test environment setup: keep the permission chain non-interactive ---
    # In default mode, permission_manager waits up to 120s for user approval
    # (read_file is MEDIUM risk), which would hang unattended test runs.
    # Set BYPASS in memory only — do NOT call set_mode(), which persists to disk.
    import tempfile
    from gateway.permission_manager import get_permission_manager, PermissionMode
    _mgr = get_permission_manager()
    _mgr.config_path = os.path.join(tempfile.gettempdir(), "pebble_test_permission_rules.json")
    _mgr.context.mode = PermissionMode.BYPASS

    async def run_checks():
        ex = AgentToolExecutorImpl(project_path=ROOT)
        ctx = ToolExecutionContext(
            conversation_uuid="test-uuid",
            agent_name="chat_agent",
            project_path=ROOT,
            cancel_event=asyncio.Event(),
        )

        # 1. web_search must be denied at entry (never reaches network)
        r = await ex.execute("web_search", {"query": "test"}, ctx)
        check("web_search denied at entry", r.startswith("Error:") and "allowlist" in r, f"got: {r[:120]}")

        # 2. MCP tool denied at entry
        r2 = await ex.execute("mcp__blender__look", {}, ctx)
        check("MCP tool denied at entry", r2.startswith("Error:") and "allowlist" in r2, f"got: {r2[:120]}")

        # 3. execute_code denied at entry
        r3 = await ex.execute("execute_code", {"code": "print(1)", "language": "python"}, ctx)
        check("execute_code denied at entry", r3.startswith("Error:") and "allowlist" in r3, f"got: {r3[:120]}")

        # 4. read_file passes policy and really executes
        target = os.path.join(ROOT, "agent_core", "agents", "chat_agent.yaml")
        r4 = await ex.execute("read_file", {"file_path": target}, ctx)
        check("read_file passes policy (real read)", ("chat_agent" in r4) and ("allowlist" not in r4), f"got: {r4[:150]}")

        # 5. code_agent: web_search must NOT be policy-denied (enforce off)
        from agent_core.agent_loader import check_agent_tool_policy

        check("code_agent web_search not policy-denied", check_agent_tool_policy("code_agent", "web_search") is None)

    asyncio.run(run_checks())

    print(f"\nRESULT: {passed} passed, {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
