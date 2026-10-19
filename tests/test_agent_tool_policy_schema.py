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

    # 1. chat_agent: schema filtered by the YAML allowlist
    from agent_core.tool_definitions import get_tools_definition

    base_names = {t["function"]["name"] for t in get_tools_definition()}
    result = svc._get_tools_definition(chat_def)
    names = {t["function"]["name"] for t in result}
    print(f"[INFO] chat_agent schema tool count: {len(names)}")

    # 白名单里的内置工具必须全部出现在过滤结果里
    # （MCP 工具是运行时注册的，不在静态 schema 中，故不参与比较）
    allowed_internal = {t for t in chat_def.tools.allowed if t in base_names}
    missing = sorted(allowed_internal - names)
    check("chat_agent schema includes every allowed internal tool", not missing, f"missing {missing}")

    # 写类与执行类工具必须被过滤掉
    forbidden = {
        "write_file", "edit_file", "delete_file", "copy_file", "move_file",
        "create_directory",
        "execute_code", "execute_shell", "powershell",
        "browser_download", "kill_process", "set_env_var",
    }
    leaked = sorted(forbidden & names)
    check("chat_agent schema excludes write and exec tools", not leaked, f"leaked {leaked}")

    # 2. code_agent: empty allowlist -> no filtering (full list)
    result2 = svc._get_tools_definition(code_def)
    names2 = {t["function"]["name"] for t in result2}
    print(f"[INFO] code_agent schema tool count: {len(names2)}")
    check("code_agent schema includes every base tool", base_names <= names2,
          f"missing {sorted(base_names - names2)}")
    check("code_agent schema includes web_search", "web_search" in names2)

    # 3. no agent_def: unfiltered (backward compatible)
    result3 = svc._get_tools_definition(None)
    check("no agent_def -> unfiltered", len(result3) == len(names2), f"{len(result3)} vs {len(names2)}")

    print(f"\nRESULT: {passed} passed, {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
