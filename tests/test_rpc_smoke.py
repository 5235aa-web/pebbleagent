#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""RPC smoke tests: the four most-used endpoints plus path guard.

Run: python tests/test_rpc_smoke.py   (exit 0 = pass)
"""
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

TMP = tempfile.mkdtemp(prefix="pebble_rpc_smoke_")
os.environ["PEBBLE_HOME"] = TMP
os.environ["PEBBLE_CONFIG_DIR"] = TMP

import gateway.server as sv  # noqa: E402

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


def main() -> int:
    # ---- session.create ----
    r = sv.handle_request("session.create", {"project_path": ""}, "1")
    ok = isinstance(r, dict) and r.get("id") == "1" and r.get("result")
    check("session.create returns result", ok, f"got {r}")
    sid = (r.get("result") or {}).get("session_id") if ok else None
    check("session.create returns session_id", bool(sid), f"got {sid}")
    info = (r.get("result") or {}).get("info") or {}
    check("session.create info has model", "model" in info, f"got {list(info.keys())}")
    check("session.create info has tools", isinstance(info.get("tools"), dict), f"got {type(info.get('tools'))}")

    # ---- session.list ----
    r = sv.handle_request("session.list", {"limit": 10}, "2")
    sessions = (r.get("result") or {}).get("sessions") if isinstance(r, dict) else None
    check("session.list returns sessions", isinstance(sessions, list), f"got {type(sessions)}")
    check("session.list includes created session",
          any(s.get("id") == sid for s in (sessions or [])), f"sid={sid}")

    # ---- file.read_text (inside project) ----
    probe = os.path.join(ROOT, "_tmp_rpc_smoke.txt")
    with open(probe, "w", encoding="utf-8") as fh:
        fh.write("hello-rpc")
    try:
        r = sv.handle_request("file.read_text", {"path": probe}, "3")
        res = (r.get("result") or {}) if isinstance(r, dict) else {}
        check("file.read_text ok", res.get("ok") is True, f"got {res}")
        check("file.read_text content", res.get("text") == "hello-rpc", f"got {res.get('text')!r}")
    finally:
        try:
            os.remove(probe)
        except OSError:
            pass

    # ---- file.read_text (outside project) ----
    outside = os.path.join(os.path.expanduser("~"), ".pebble_rpc_smoke_outside.txt")
    r = sv.handle_request("file.read_text", {"path": outside}, "4")
    res = (r.get("result") or {}) if isinstance(r, dict) else {}
    check("file.read_text rejects outside path", res.get("ok") is False, f"got {res}")

    # ---- mcp.list ----
    r = sv.handle_request("mcp.list", {}, "5")
    res = (r.get("result") or {}) if isinstance(r, dict) else {}
    check("mcp.list returns servers", isinstance(res.get("servers"), list), f"got {type(res.get('servers'))}")

    # ---- unknown method ----
    r = sv.handle_request("no.such.method", {}, "6")
    check("unknown method returns None", r is None, f"got {r}")

    print()
    if failed:
        print(f"FAILED: {failed} checks")
        return 1
    print(f"ALL PASSED: {passed} checks")
    return 0


if __name__ == "__main__":
    sys.exit(main())
