#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Permission chain end to end: rule parsing, modes, allow/deny/ask.

Run: python tests/test_permission_e2e.py   (exit 0 = pass)
"""
import json
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from gateway.permission_manager import PermissionManager, PermissionMode

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
    tmp = tempfile.mkdtemp(prefix="pebble-perm-")
    rules_path = os.path.join(tmp, "permission_rules.json")
    with open(rules_path, "w", encoding="utf-8") as f:
        json.dump({
            "mode": "manual",
            "allow": ["read_file", "web_search:*docs.python.org*"],
            "deny": ["execute_code:rm -rf*", "read_file:*.secret"],
            "ask": ["write_file"],
        }, f)

    pm = PermissionManager(config_path=rules_path)
    check("mode manual", pm.get_mode() == PermissionMode.MANUAL, str(pm.get_mode()))

    ok, why = pm.check_permission_sync("read_file", "README.md")
    check("read allowed", ok, why)

    ok, why = pm.check_permission_sync("read_file", ".env")
    check("manual asks for secret file read", (not ok) and "sensitive" in why, why)

    ok, why = pm.check_permission_sync("write_file", "x.py")
    check("write asks", (not ok) and "confirmation" in why, why)

    ok, why = pm.check_permission_sync("execute_code", "rm -rf /tmp/x")
    check("deny rule", (not ok) and "denied" in why, why)

    ok, why = pm.check_permission_sync("web_search", "https://docs.python.org/3/")
    check("allow pattern", ok, why)

    ok, why = pm.check_permission_sync("web_search", "https://example.com/")
    check("unmatched low risk allowed", ok and "low risk" in why, why)

    ok, why = pm.check_permission_sync("delete_file", "x.py")
    check("unmatched high risk asks", (not ok) and "confirmation" in why, why)

    pm.set_mode(PermissionMode.READONLY)
    ok, why = pm.check_permission_sync("read_file", "README.md")
    check("readonly read", ok, why)
    ok, why = pm.check_permission_sync("read_file", "app.secret")
    check("readonly honors deny rule", (not ok) and "denied by rule" in why, why)
    ok, why = pm.check_permission_sync("read_file", ".env")
    check("readonly blocks secret file read", (not ok) and "sensitive" in why, why)
    ok, why = pm.check_permission_sync("read_file", "secrets/token.txt")
    check("readonly blocks secret dir read", (not ok) and "sensitive" in why, why)
    ok, why = pm.check_permission_sync("write_file", "x.py")
    check("readonly write", (not ok) and "readonly" in why, why)

    # locked 模式已移除：旧配置值必须抛错，不能静默接受
    try:
        PermissionMode("locked")
        check("locked mode removed", False, "PermissionMode('locked') still accepted")
    except ValueError:
        check("locked mode removed", True, "")

    pm.set_mode(PermissionMode.AUTO)
    ok, why = pm.check_permission_sync("execute_code", "rm -rf /tmp/x")
    check("auto allows all", ok, why)

    ok, why = pm.check_permission_sync("read_file", ".env")
    check("auto blocks secret file read", (not ok) and "sensitive" in why, why)

    with open(rules_path, "r", encoding="utf-8") as f:
        saved = json.load(f)
    check("mode persisted", saved.get("mode") == "auto", str(saved.get("mode")))
    check("rules persisted", len(saved.get("deny", [])) == 2, str(saved))

    print(f"\nRESULT: PASS={passed} FAIL={failed}")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
