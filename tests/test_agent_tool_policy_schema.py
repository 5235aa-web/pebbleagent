#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Agent tool policy: AgentChatServiceV2._get_tools_definition applies the agent filter end-to-end.

Run: python tests/test_agent_tool_policy_schema.py   (exit 0 = pass)
"""
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

    from agent_core.agent_chat_service_v2 import AgentChatServiceV2
    from agent_core.agent_loader import load_agent_yaml, clear_agent_cache

    clear_agent_cache()
    chat_def = load_agent_yaml("chat_agent")
    code_def = load_agent_yaml("code_agent")

    # Bypass __init__ (no full service boot needed); only attrs used by _get_tools_definition
    svc = AgentChatServiceV2.__new__(AgentChatServiceV2)
    svc._mcp_initialized = False
    svc._mcp_manager = None

    # 1. chat_agent: schema filtered to exactly 4 tools
    result = svc._get_tools_definition(chat_def)
    names = sorted(t["function"]["name"] for t in result)
    print(f"[INFO] chat_agent schema tool count: {len(names)}")
    check(
        "chat_agent schema == 4 tools",
        names == ["edit_file", "read_file", "search_files", "write_file"],
        f"got {names}",
    )

    # 2. code_agent: unfiltered (full list, enforce off)
    result2 = svc._get_tools_definition(code_def)
    names2 = sorted(t["function"]["name"] for t in result2)
    print(f"[INFO] code_agent schema tool count: {len(names2)}")
    check("code_agent schema unfiltered (>4)", len(names2) > 4, f"got {len(names2)}")
    check("code_agent schema includes web_search", "web_search" in names2)

    # 3. no agent_def: unfiltered (backward compatible)
    result3 = svc._get_tools_definition(None)
    check("no agent_def -> unfiltered", len(result3) == len(names2), f"{len(result3)} vs {len(names2)}")

    print(f"\nRESULT: {passed} passed, {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
