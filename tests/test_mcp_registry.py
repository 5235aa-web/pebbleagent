#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""MCP manager registry: tool naming, lookup, state shape.

Run: python tests/test_mcp_registry.py   (exit 0 = pass)
"""
import asyncio
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from agent_core.mcp.mcp_client import parse_mcp_tool_name
from agent_core.mcp.mcp_manager import get_mcp_manager
from agent_core.tool_impl.mcp_bridge import McpToolsMixin

passed = 0
failed = 0


def check(name, cond, detail=""):
    global passed, failed
    if cond:
        passed += 1
        print(f"[PASS] {name}")
    else:
        failed += 1
        print(f"[FAIL] {name} {detail}")


def main() -> int:
    mgr = get_mcp_manager()

    check("mcp prefix", mgr.is_mcp_tool("mcp__srv__tool"))
    check("plain tool not mcp", not mgr.is_mcp_tool("read_file"))
    check("parse server/tool", parse_mcp_tool_name("mcp__srv__tool") == ("srv", "tool"))
    check("parse keeps nested underscores", parse_mcp_tool_name("mcp__srv__a__b") == ("srv", "a__b"))
    check("parse plain none", parse_mcp_tool_name("read_file") is None)
    check("parse missing tool none", parse_mcp_tool_name("mcp__srv") is None)
    check("unknown tool info none", mgr.get_tool_info("mcp__none__x") is None)
    check("unknown server none", mgr.get_server_for_tool("mcp__none__x") is None)

    state = mgr.get_state()
    check("state clients list", isinstance(state.clients, list), type(state.clients).__name__)
    check("state tools list", isinstance(state.tools, list), type(state.tools).__name__)
    check("state resources dict", isinstance(state.resources, dict), type(state.resources).__name__)
    check("all tools openai shape", isinstance(mgr.get_all_tools(), list))

    class _Dummy(McpToolsMixin):
        pass

    res = asyncio.run(_Dummy()._execute_mcp_tool("mcp__none__server", {}))
    check("mcp execute resolves factory", "not defined" not in str(res).lower(), str(res)[:120])

    print(f"\nRESULT: PASS={passed} FAIL={failed}")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
