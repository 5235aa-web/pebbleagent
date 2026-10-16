#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Permission audit hardening (2026-10-17 security fix, review issue 5).

Before: audit checksum chain existed but (a) no masking — commands/code with
API keys landed in the DB in plaintext; (b) verify_integrity was never called
anywhere; (c) log_permission_check (which records full tool_args) was never
invoked — only log_permission_decision was.

Now: write-time masking, startup integrity check + RPC, and the check-request
event is recorded again.

Run: python tests/test_audit_hardening.py   (exit 0 = pass)
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

    from gateway.permission_audit import PermissionAuditLogger, AuditEvent, AuditEventType

    # ---- Unit: masking matrix ------------------------------------------------
    m = PermissionAuditLogger._mask_sensitive
    cases = [
        ("curl -H 'Authorization: Bearer abcdef1234567890' http://x", "abcdef1234567890"),
        ("export OPENAI_KEY=sk-abcdefgh12345678", "sk-abcdefgh12345678"),
        ("git remote add origin https://ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZ123456@github.com/x", "ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZ123456"),
        ('{"apiKey": "verysecretvalue123"}', "verysecretvalue123"),
        ("token=abc123def456&x=1", "abc123def456"),
        ("password: hunter2hunter2", "hunter2hunter2"),
    ]
    for text, secret in cases:
        masked = m(text)
        check(f"mask hides {secret[:16]}...", secret not in masked, f"got {masked!r}")
    # benign text must survive untouched
    benign = "python tests/run_all.py && echo done"
    check("mask keeps benign text", m(benign) == benign, f"got {m(benign)!r}")
    check("mask keeps None/empty", m("") == "" and m(None) is None)

    # ---- Integration: masking applied at write time --------------------------
    tmp_db = os.path.join(tempfile.mkdtemp(prefix="pebble_audit_test_"), "audit.db")
    logger = PermissionAuditLogger(db_path=tmp_db)

    logger.log_event(AuditEvent(
        event_type=AuditEventType.PERMISSION_CHECK.value,
        tool_name="execute_shell",
        target="export TOKEN=sk-live-abcdef123456 && deploy",
        content="curl -H 'Authorization: Bearer topsecretvalue99' http://x",
        reason='api_key="mysecretkey123456"',
        allowed=True,
        decision_source="user_approve",
    ))
    conn = sqlite3.connect(tmp_db)
    row = conn.execute("SELECT target, content, reason FROM permission_audit_log ORDER BY id DESC LIMIT 1").fetchone()
    conn.close()
    joined = " ".join(row)
    check("write-time mask: sk- gone", "sk-live-abcdef123456" not in joined, f"row={row}")
    check("write-time mask: bearer gone", "topsecretvalue99" not in joined, f"row={row}")
    check("write-time mask: json secret gone", "mysecretkey123456" not in joined, f"row={row}")

    # ---- verify_integrity: clean chain passes, tamper fails ------------------
    logger.log_event(AuditEvent(
        event_type=AuditEventType.PERMISSION_ALLOWED.value,
        tool_name="read_file", target="a.py", allowed=True, decision_source="low_risk",
    ))
    check("verify_integrity passes on clean chain", logger.verify_integrity() is True)

    conn = sqlite3.connect(tmp_db)
    conn.execute("UPDATE permission_audit_log SET content = 'TAMPERED' WHERE id = 1")
    conn.commit()
    conn.close()
    check("verify_integrity detects tamper", logger.verify_integrity() is False)

    # ---- log_permission_check works and records tool_args --------------------
    tmp_db2 = os.path.join(tempfile.mkdtemp(prefix="pebble_audit_test2_"), "audit.db")
    logger2 = PermissionAuditLogger(db_path=tmp_db2)
    logger2.log_permission_check(
        tool_name="write_file",
        tool_args={"file_path": "a.py", "content": "x"},
        permission_type="FILE_WRITE",
        permission_level="high",
        target="a.py",
        content="a.py",
        project_path=ROOT,
        conversation_uuid="conv1",
        agent_name="code_agent",
    )
    conn = sqlite3.connect(tmp_db2)
    row = conn.execute(
        "SELECT event_type, tool_args, permission_type FROM permission_audit_log ORDER BY id DESC LIMIT 1"
    ).fetchone()
    conn.close()
    check("log_permission_check records tool_args",
          row[0] == "permission_check" and "a.py" in (row[1] or "") and row[2] == "FILE_WRITE",
          f"row={row}")

    # ---- Static guards --------------------------------------------------------
    def read(path):
        with open(os.path.join(ROOT, path), "r", encoding="utf-8") as f:
            return f.read()

    check("static: enforcer calls log_permission_check",
          "log_permission_check(" in read("agent_core/permission_enforcer.py"))
    check("static: startup integrity check wired in ws_server",
          "_verify_audit_integrity_background" in read("gateway/ws_server.py"))
    check("static: audit verify RPC present",
          'method == "permission.audit.verify"' in read("gateway/server.py"))
    check("static: masking applied in log_event",
          "self._mask_sensitive(event.content)" in read("gateway/permission_audit.py"))

    print(f"\n{passed} passed, {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
