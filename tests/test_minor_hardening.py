#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Minor hardening batch.

7a BaseTool background-thread permission skip removed
7b path_whitelist.save validates entries (no drive/filesystem roots)
7c permission.mode.set writes an audit record
7d WS token: X-Pebble-Token header preferred over ?token=
7f allow-rule prefix match gets word boundary (Bash(git:*) must not pass "gitk")

Run: python tests/test_minor_hardening.py   (exit 0 = pass)
"""
import os
import sqlite3
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

    def read(path):
        with open(os.path.join(ROOT, path), "r", encoding="utf-8") as f:
            return f.read()


    # ================= 7b: path_whitelist.save validation =================
    from gateway.server import handle_request

    resp = handle_request("path_whitelist.save", {"paths": ["C:\\"]}, "1")
    check("7b: drive root rejected", "error" in resp and "too broad" in resp["error"]["message"],
          f"got {resp}")
    resp = handle_request("path_whitelist.save", {"paths": ["relative/path"]}, "1")
    check("7b: relative path rejected", "error" in resp and "absolute" in resp["error"]["message"],
          f"got {resp}")
    resp = handle_request("path_whitelist.save", {"paths": ["", "x"]}, "1")
    check("7b: empty entry rejected", "error" in resp and "non-empty" in resp["error"]["message"],
          f"got {resp}")
    resp = handle_request("path_whitelist.save", {"paths": ["a", "b", "c", "d", "e", "f"]}, "1")
    check("7b: >5 entries rejected", "error" in resp and "maximum 5" in resp["error"]["message"],
          f"got {resp}")

    # ================= 7c: mode change audit =================
    from gateway.permission_audit import get_audit_logger

    tmp_audit_dir = tempfile.mkdtemp(prefix="pebble_audit_minor_")
    tmp_audit_db = os.path.join(tmp_audit_dir, "audit.db")
    alog = get_audit_logger()
    alog.db_path = tmp_audit_db
    alog._init_db()

    resp = handle_request("permission.mode.set", {"mode": "manual"}, "2")
    check("7c: mode.set succeeds", resp.get("result", {}).get("ok") is True, f"got {resp}")

    conn = sqlite3.connect(tmp_audit_db)
    row = conn.execute(
        "SELECT event_type, target FROM permission_audit_log ORDER BY id DESC LIMIT 1"
    ).fetchone()
    conn.close()
    check("7c: mode change audited", row is not None and row[0] == "mode_changed" and row[1] == "manual",
          f"row={row}")

    # ================= 7d: WS token header preferred =================
    from gateway.ws_server import _extract_ws_token

    class _Req:
        def __init__(self, path, headers):
            self.path = path
            self.headers = headers

    class _WS:
        def __init__(self, path, headers):
            self.request = _Req(path, headers)

    got = _extract_ws_token(_WS("/?token=querytoken", {"X-Pebble-Token": "headertoken"}))
    check("7d: header preferred over query string", got == "headertoken", f"got {got!r}")
    got = _extract_ws_token(_WS("/?token=querytoken", {}))
    check("7d: query fallback works", got == "querytoken", f"got {got!r}")
    got = _extract_ws_token(_WS("/", {}))
    check("7d: empty when absent", got == "", f"got {got!r}")

    # ================= 7f: rule prefix word boundary =================
    from gateway.permission_manager import (
        PermissionRule, PermissionBehavior, get_permission_manager,
    )

    mgr = get_permission_manager()
    rule = PermissionRule(tool="execute_shell", pattern="git *", effect=PermissionBehavior.ALLOW)
    check("7f: 'git status' matches", mgr._match_rule(rule, "execute_shell", "git status") is True)
    check("7f: bare 'git' matches", mgr._match_rule(rule, "execute_shell", "git") is True)
    check("7f: 'gitk' does NOT match", mgr._match_rule(rule, "execute_shell", "gitk") is False)
    check("7f: 'git-secret' does NOT match", mgr._match_rule(rule, "execute_shell", "git-secret") is False)
    check("7f: 'git; rm -rf /' does NOT match (conservative)",
          mgr._match_rule(rule, "execute_shell", "git; rm -rf /") is False)

    print(f"\n{passed} passed, {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
