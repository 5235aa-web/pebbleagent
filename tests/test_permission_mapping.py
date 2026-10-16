#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Permission mapping unification (2026-10-17 security fix, review issue 4).

Before: gateway/permission_manager._get_permission_type had its own coarse
mapping whose default was EXEC_COMMAND — MCP tools (mcp__*) and other unmapped
tools fell into "exec:command", so remembering ONE MCP tool auto-approved
execute_shell/powershell for the session (privilege escalation).

Now: the method reuses agent_core.tool_permission as the single source of truth
and defaults unmapped tools to UNKNOWN_TOOL, keeping trust keys isolated.

Run: python tests/test_permission_mapping.py   (exit 0 = pass)
"""
import asyncio
import datetime
import os
import sys
import tempfile

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

    from gateway.permission_manager import get_permission_manager
    from gateway.permission_service import PermissionType

    mgr = get_permission_manager()
    # Never write the real rules file from tests
    mgr.config_path = os.path.join(tempfile.gettempdir(), "pebble_test_permission_rules.json")

    # ---- Mapping matrix (post-fix expectations) ------------------------------
    cases = {
        "read_file": PermissionType.FILE_READ,
        "write_file": PermissionType.FILE_WRITE,
        "edit_file": PermissionType.FILE_WRITE,
        "delete_file": PermissionType.FILE_DELETE,
        "execute_shell": PermissionType.EXEC_COMMAND,
        "execute_code": PermissionType.EXEC_COMMAND,
        "powershell": PermissionType.EXEC_COMMAND,
        "grep": PermissionType.FILE_READ,
        "glob": PermissionType.FILE_READ,
        "web_search": PermissionType.NETWORK_FETCH,
        "browser_download": PermissionType.NETWORK_FETCH,
        "fetch_url": PermissionType.NETWORK_FETCH,
        "mcp__github__search_repositories": PermissionType.MCP_TOOL,
        "mcp__blender__look": PermissionType.MCP_TOOL,
        "some_unknown_tool_xyz": PermissionType.UNKNOWN_TOOL,
    }
    for name, want in cases.items():
        got = mgr._get_permission_type(name)
        check(f"map {name!r} -> {want.name}", got == want, f"got {got.name}")

    # ---- Trust-key isolation (the escalation scenario) -----------------------
    mcp_key = mgr._get_permission_type("mcp__github__search_repositories")
    shell_key = mgr._get_permission_type("execute_shell")
    check("trust keys isolated: MCP tool != exec:command",
          mcp_key != PermissionType.EXEC_COMMAND, f"got {mcp_key.name}")
    check("trust keys isolated: MCP and shell differ",
          mcp_key.value != shell_key.value, f"{mcp_key.value} vs {shell_key.value}")

    from gateway.permission_service import get_permission_service

    svc = get_permission_service()
    svc._trusted_sessions.clear()
    # Simulate: user "remembered" an MCP tool for 30 minutes
    svc._trusted_sessions[("sess1", PermissionType.MCP_TOOL.value)] = (
        # naive UTC to match permission_service's internal datetime.utcnow() usage
        datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)
        + datetime.timedelta(minutes=30)
    )

    async def _checks():
        mcp_trusted = await svc.check_permission(PermissionType.MCP_TOOL, "sess1")
        shell_trusted = await svc.check_permission(PermissionType.EXEC_COMMAND, "sess1")
        unknown_trusted = await svc.check_permission(PermissionType.UNKNOWN_TOOL, "sess1")
        return mcp_trusted, shell_trusted, unknown_trusted

    mcp_trusted, shell_trusted, unknown_trusted = asyncio.run(_checks())
    check("remembered MCP tool -> MCP_TOOL trusted", mcp_trusted is True)
    check("remembered MCP tool -/-> execute_shell auto-approved (escalation closed)",
          shell_trusted is False, f"got {shell_trusted}")
    check("remembered MCP tool -/-> UNKNOWN_TOOL auto-approved",
          unknown_trusted is False, f"got {unknown_trusted}")
    svc._trusted_sessions.clear()

    # ---- Static guards --------------------------------------------------------
    with open(os.path.join(ROOT, "gateway", "permission_manager.py"), "r", encoding="utf-8") as f:
        src = f.read()
    check("static: authoritative mapping reused",
          "from agent_core.tool_permission import get_tool_permission" in src)
    check("static: unmapped default is UNKNOWN_TOOL (not EXEC_COMMAND)",
          "mapping.get(tool_name, PermissionType.UNKNOWN_TOOL)" in src)

    print(f"\n{passed} passed, {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
